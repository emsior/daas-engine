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

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="$PREFIX-$stamp.tar.gz"
final="$BACKUP_DIR/$name"
partial="$final.partial"

version="2.0.0"
if [ -f .env ]; then
  version="$(sed -n 's/^DAAS_VERSION=//p' .env | head -n 1)"
  version="${version:-2.0.0}"
fi

api_was_running=0
if docker compose ps --status running --services 2>/dev/null | grep -qx api; then
  api_was_running=1
fi

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
mv "$partial" "$final"
(cd "$BACKUP_DIR" && sha256sum "$name" >"$name.sha256")
{
  echo "created_utc=$stamp"
  echo "daas_version=$version"
  echo "mode=$mode"
} >"$final.meta"

size="$(du -h "$final" | cut -f1)"
echo "Kopia: $final ($size)"
echo "SHA-256: $(cut -d' ' -f1 "$final.sha256")"

# retencja: zostaje KEEP najnowszych kopii z tym prefiksem (nazwy zawieraja date UTC, wiec sort = chronologia)
mapfile -t all < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$PREFIX-*.tar.gz" | sort -r)
if [ "${#all[@]}" -gt "$KEEP" ]; then
  for old in "${all[@]:$KEEP}"; do
    rm -f "$old" "$old.sha256" "$old.meta"
    echo "Usunieto stara kopie: $(basename "$old")"
  done
fi
