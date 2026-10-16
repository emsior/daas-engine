#!/usr/bin/env bash
# Odtworzenie danych z kopii zrobionej przez backup.sh. NADPISUJE bieżące dane (wolumeny control i runtime).
#
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz
#   ./restore.sh backups/daas-20261010T120000Z.tar.gz --yes     # bez pytania
#
# Kolejnosc: weryfikacja sumy SHA-256 i zawartosci, kopia bezpieczenstwa biezacych danych (prefiks pre-restore),
# zatrzymanie api i web, podmiana danych, start. Po odtworzeniu uruchom ./smoke.sh.
# Rollback wersji aplikacji: ustaw w .env poprzedni DAAS_VERSION (obraz musi byc lokalnie albo w rejestrze), potem ten skrypt.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

archive=""
assume_yes=0
for arg in "$@"; do
  case "$arg" in
    --yes) assume_yes=1 ;;
    -h | --help)
      sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
  echo "Uzycie: $0 <archiwum.tar.gz> [--yes]" >&2
  exit 2
}
[ -f "$archive" ] || {
  echo "Brak pliku: $archive" >&2
  exit 1
}
archive="$(cd "$(dirname "$archive")" && pwd)/$(basename "$archive")"

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
echo "Kopia bezpieczenstwa biezacych danych (pre-restore)..."
BACKUP_PREFIX=pre-restore ./backup.sh --cold || echo "Uwaga: kopia pre-restore sie nie udala, kontynuuje na Twoja odpowiedzialnosc." >&2

# 4. podmiana
echo "Zatrzymuje web i api..."
docker compose stop web api >/dev/null 2>&1 || true

docker compose run --rm -T --entrypoint sh maint -c \
  'set -e; find /data/control /data/runtime -mindepth 1 -delete; tar -xzf - -C /data; mkdir -p /data/control /data/runtime; chown -R 10001:10001 /data/control /data/runtime' \
  <"$archive"

echo "Uruchamiam api i web..."
docker compose up -d api web
echo "Gotowe. Sprawdz: ./smoke.sh --wait 90"
