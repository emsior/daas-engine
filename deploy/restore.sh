#!/usr/bin/env bash
# Odtworzenie danych z kopii zrobionej przez backup.sh. NADPISUJE bieżące dane (wolumeny control i runtime).
#
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz --yes     # bez pytania
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz --yes --no-start
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz --yes --no-start --no-pre-backup
#
# Kolejnosc: weryfikacja sumy SHA-256 i zawartosci, kopia bezpieczenstwa biezacych danych (prefiks pre-restore),
# zatrzymanie api i web, podmiana danych, kontrola SQLite, opcjonalny start. Zalecane: --no-start i RUNBOOK, sekcja 5.
# Rollback wersji aplikacji: ustaw w .env poprzedni DAAS_VERSION (obraz musi byc lokalnie albo w rejestrze), potem ten skrypt.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

archive=""
assume_yes=0
no_start=0
no_pre_backup=0
for arg in "$@"; do
  case "$arg" in
    --yes) assume_yes=1 ;;
    --no-start) no_start=1 ;;
    --no-pre-backup) no_pre_backup=1 ;;
    -h | --help)
      sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "Nieznany argument: $arg" >&2
      exit 2
      ;;
    *)
      [ -z "$archive" ] || {
        echo "Podaj jedno archiwum." >&2
        exit 2
      }
      archive="$arg"
      ;;
  esac
done
[ -n "$archive" ] || {
  echo "Uzycie: $0 <archiwum.tar.gz> [--yes] [--no-start] [--no-pre-backup]" >&2
  exit 2
}
[ -f "$archive" ] || {
  echo "Brak pliku: $archive" >&2
  exit 1
}
archive="$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"
if ! archive_target="$(readlink -f -- "$archive")" || [ ! -f "$archive_target" ]; then
  echo "Nie udalo sie ustalic docelowego pliku archiwum: odmawiam." >&2
  exit 1
fi

# 1. suma kontrolna (wymagana) i zawartosc
if [ ! -f "$archive.sha256" ]; then
  echo "Brak $archive.sha256: nie odtwarzam kopii bez sumy kontrolnej." >&2
  exit 1
fi
(cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256")

gzip -t "$archive"
listing="$(tar -tzf "$archive")"
if printf '%s\n' "$listing" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
  echo "Archiwum zawiera sciezki bezwzgledne lub '..': odmawiam." >&2
  exit 1
fi
if printf '%s\n' "$listing" | grep -qEv '^(control|runtime)(/|$)'; then
  echo "Archiwum zawiera pliki spoza control/ i runtime/: odmawiam." >&2
  exit 1
fi
printf '%s\n' "$listing" | grep -q '^control/' || {
  echo "W archiwum nie ma control/ (control.db): odmawiam." >&2
  exit 1
}

# 2. potwierdzenie
if [ "$assume_yes" != "1" ]; then
  echo "To nadpisze biezace dane wdrozenia danymi z: $(basename "$archive")"
  printf 'Wpisz "restore", aby kontynuowac: '
  read -r answer
  [ "$answer" = "restore" ] || {
    echo "Przerwano."
    exit 1
  }
fi

# 3. kopia bezpieczenstwa tego, co jest teraz
pre_backup_path=""
if [ "$no_pre_backup" = "1" ]; then
  echo "UWAGA: --no-pre-backup pomija kopie biezacych danych; brak zrodla uzgodnienia." >&2
  echo "Wymagany konserwatywny reset: python -m app.cli restore-quarantine --revoke-all." >&2
else
  echo "Kopia bezpieczenstwa biezacych danych (pre-restore)..."
  if ! backup_output="$(BACKUP_PREFIX=pre-restore BACKUP_KEEP_PATH="$archive_target" ./backup.sh --cold)"; then
    printf '%s\n' "$backup_output"
    echo "Kopia pre-restore nie powiodla sie: przerywam przed podmiana danych." >&2
    exit 1
  fi
  printf '%s\n' "$backup_output"
  pre_backup_path="$(printf '%s\n' "$backup_output" | sed -n 's/^BACKUP_ARCHIVE=//p')"
  if [ -z "$pre_backup_path" ] || [ ! -f "$pre_backup_path" ] || [ ! -f "$pre_backup_path.sha256" ]; then
    echo "Brak potwierdzonej sciezki kopii pre-restore: przerywam przed podmiana danych." >&2
    exit 1
  fi
fi

# 4. podmiana
echo "Zatrzymuje web i api..."
if ! docker compose stop web api; then
  echo "Nie udalo sie zatrzymac web i api: przerywam przed podmiana danych." >&2
  exit 1
fi
if ! running_services="$(docker compose ps --status running --services api web)"; then
  echo "Nie udalo sie sprawdzic stanu web i api: przerywam przed podmiana danych." >&2
  exit 1
fi
if [ -n "$running_services" ]; then
  echo "Web lub api nadal dziala: przerywam przed podmiana danych." >&2
  exit 1
fi

docker compose run --rm --no-deps -T --entrypoint sh maint -c \
  'set -e; find /data/control /data/runtime -mindepth 1 -delete; tar -xzf - -C /data; mkdir -p /data/control /data/runtime; chown -R 10001:10001 /data/control /data/runtime' \
  <"$archive"

# 5. Kontrola bez uruchamiania aplikacji; maint ma network_mode: none.
if ! docker compose run --rm --no-deps -T --entrypoint python maint - <<'PY'
import sqlite3
import sys
from contextlib import closing

try:
    with closing(sqlite3.connect("file:/data/control/control.db?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        valid = integrity == [("ok",)] and not foreign_keys
except sqlite3.Error:
    valid = False
if not valid:
    print("SQLite integrity verification failed.", file=sys.stderr)
    raise SystemExit(1)
print("SQLite integrity_check and foreign_key_check: OK")
PY
then
  echo "Kontrola SQLite nie powiodla sie. Web i api pozostaja zatrzymane." >&2
  exit 1
fi

if [ "$no_start" = "1" ]; then
  echo "Dane odtworzone; web i api pozostaja zatrzymane (--no-start)."
  echo "Dalsze kroki przy zamknietym wejsciu i zablokowanym ruchu wychodzacym (RUNBOOK, sekcja 5):"
  echo "  DAAS_MAINTENANCE_MODE=true docker compose up -d api"
  echo "  docker compose stop api"
  echo "  docker compose run --rm --no-deps -T api python -m app.cli restore-quarantine --revoke-all"
  echo "Potwierdz czlonkow i role, wykonaj test dymny; dopiero potem zakoncz tryb serwisowy."
else
  echo "Uruchamiam api i web..."
  docker compose up -d api web
  echo "Gotowe. Sprawdz: ./smoke.sh --wait 90"
fi
if [ -n "$pre_backup_path" ]; then
  echo "Zachowaj kopie pre-restore do uzgodnienia: $pre_backup_path"
else
  echo "Kopia pre-restore: brak (--no-pre-backup); wymagany restore-quarantine --revoke-all."
fi
