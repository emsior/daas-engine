#!/usr/bin/env bash
# Kopia zapasowa danych DaaS Engine Cloud: wolumeny `control` (control.db) i `runtime` (organizacje: DuckDB,
# uploady, raporty, audyt) w jednym archiwum tar.gz z data UTC w nazwie i sumą SHA-256.
#
#   ./backup.sh                  # tryb zimny: zatrzymuje api na kilka sekund (spojna kopia DuckDB i SQLite)
#   ./backup.sh --online         # bez zatrzymania api; kopia moze byc niespojna (tylko awaryjnie)
#   KEEP=30 BACKUP_DIR=/srv/backups ./backup.sh
#
# Zmienne: BACKUP_DIR (domyslnie deploy/backups), KEEP (ile ostatnich kopii zostaje, domyslnie 14),
#          BACKUP_PREFIX (domyslnie daas). Archiwum zawiera dane klientow: prawa 0600, trzymaj je szyfrowane
#          i poza tym serwerem. Sekretow z .env (DAAS_SECRET_KEY) kopia NIE zawiera: przechowuj je osobno.
# BACKUP_KEEP_PATH: wewnetrzna ochrona archiwum odtwarzanego przez restore.sh; moze pozostac ponad limit KEEP.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

BACKUP_DIR="${BACKUP_DIR:-$here/backups}"
KEEP="${KEEP:-14}"
PREFIX="${BACKUP_PREFIX:-daas}"
mode="cold"

for arg in "$@"; do
  case "$arg" in
    --online) mode="online" ;;
    --cold) mode="cold" ;;
    -h | --help)
      sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Nieznany argument: $arg" >&2
      exit 2
      ;;
  esac
done

case "$KEEP" in
  '' | *[!0-9]*)
    echo "KEEP musi byc liczba calkowita >= 1" >&2
    exit 2
    ;;
esac
[ "$KEEP" -ge 1 ] || {
  echo "KEEP musi byc >= 1" >&2
  exit 2
}
case "$PREFIX" in
  *[!A-Za-z0-9_-]* | '')
    echo "BACKUP_PREFIX moze zawierac tylko litery, cyfry, - i _" >&2
    exit 2
    ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "Brak polecenia docker." >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "Brak polecenia sha256sum." >&2
  exit 1
}

umask 077
mkdir -p "$BACKUP_DIR"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd -P)"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"

version="2.0.0"
if [ -f .env ]; then
  version="$(sed -n 's/^DAAS_VERSION=//p' .env | head -n 1)"
  version="${version:-2.0.0}"
fi

api_was_running=0
if ! running_services="$(docker compose ps --status running --services)"; then
  echo "Nie udalo sie sprawdzic stanu api: przerywam przed wykonaniem kopii." >&2
  exit 1
fi
if printf '%s\n' "$running_services" | grep -qx api; then
  api_was_running=1
fi

partial="$(mktemp "$BACKUP_DIR/$PREFIX-$stamp-XXXXXXXXXX")"
final="$partial.tar.gz"
name="$(basename "$final")"

restart_api() {
  if [ "$api_was_running" = "1" ] && [ "$mode" = "cold" ]; then
    docker compose start api >/dev/null
    echo "api uruchomione ponownie."
  fi
}
cleanup() {
  rm -f "$partial"
  restart_api
}
trap cleanup EXIT

if [ "$mode" = "cold" ] && [ "$api_was_running" = "1" ]; then
  echo "Zatrzymuje api na czas kopii (tryb zimny)..."
  docker compose stop api >/dev/null
elif [ "$mode" = "online" ]; then
  echo "UWAGA: tryb online, kopia DuckDB moze byc niespojna. Do odtworzenia uzywaj kopii zimnych." >&2
fi

# tar w kontenerze `maint` (root, bez sieci, te same wolumeny), strumien na stdout: archiwum powstaje na hoscie z prawami 0600.
# Pomijamy outbox (linki logowania trybu console) i pliki tymczasowe uploadow.
docker compose run --rm -T --entrypoint tar maint \
  --exclude='runtime/outbox' --exclude='*.tmp' \
  -C /data -czf - control runtime >"$partial"

gzip -t "$partial"
# Publikacja bez nadpisywania: nawet kolizja nazwy nie moze zastapic poprzedniej kopii.
ln -- "$partial" "$final"
(
  set -o noclobber
  cd "$BACKUP_DIR"
  sha256sum "$name" >"$name.sha256"
  {
    echo "created_utc=$stamp"
    echo "daas_version=$version"
    echo "mode=$mode"
  } >"$name.meta"
)

size="$(du -h "$final" | cut -f1)"
echo "Kopia: $final ($size)"
echo "SHA-256: $(cut -d' ' -f1 "$final.sha256")"

# Biezaca kopia zawsze pierwsza; pozostale po czasie pliku, nie po losowym sufiksie.
mapfile -d '' -t all < <(
  printf '%s\0' "$final"
  find "$BACKUP_DIR" -maxdepth 1 -type f -name "$PREFIX-*.tar.gz" ! -path "$final" -printf '%T@ %p\0' |
    sort -zrn | cut -z -d' ' -f2-
)
if [ "${#all[@]}" -gt "$KEEP" ]; then
  for old in "${all[@]:$KEEP}"; do
    # Zrodlo aktualnego odtworzenia zachowujemy dodatkowo do limitu KEEP.
    [ "$old" != "$final" ] || continue
    [ "$old" != "${BACKUP_KEEP_PATH:-}" ] || continue
    rm -f "$old" "$old.sha256" "$old.meta"
    echo "Usunieto stara kopie: $(basename "$old")"
  done
fi

# Jednoznaczny wynik dla restore.sh; pozostale linie sa przeznaczone dla operatora.
echo "BACKUP_ARCHIVE=$final"
