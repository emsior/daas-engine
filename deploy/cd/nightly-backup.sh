#!/usr/bin/env bash
# Nocna kopia (wolana z /etc/cron.d/daas-backup przez bootstrap.sh): backup.sh (zimna) + kontrola sumy + znacznik L-8 `local`
# (tylko z nakladka obserwowalnosci) + hak kopii poza serwerem deploy/cd/offsite-backup.hook, jesli istnieje.
# Hak odpowiada za wysylke, a po UDANEJ wysylce i `sha256sum -c` za `backup-marker.sh offsite` i heartbeat
# (przyklad: offsite-backup.hook.example). Nie wspolbiezy z wdrozeniem: ta sama blokada co deploy.sh.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy_dir="$(cd "$here/.." && pwd)"
cd "$deploy_dir"

[ -f .env ] || { echo "deploy/.env nie istnieje: pomijam kopie."; exit 0; }

mkdir -p "$here/state"
exec 9>"$here/state/deploy.lock"
flock -n 9 || { echo "trwa wdrozenie albo inna operacja: pomijam kopie nocna."; exit 0; }

out="$(./backup.sh)"
printf '%s\n' "$out"
archive="$(printf '%s\n' "$out" | sed -n 's/^BACKUP_ARCHIVE=//p' | tail -n 1)"
[ -f "$archive" ] || { echo "backup.sh nie zwrocil archiwum." >&2; exit 1; }
(cd "$(dirname "$archive")" && sha256sum -c --status "$(basename "$archive").sha256") || { echo "suma SHA-256 kopii nie zgadza sie." >&2; exit 1; }

marker="$deploy_dir/observability/textfile/backup-marker.sh"
if grep -qs '^COMPOSE_FILE=.*compose\.observability\.yml' .env && [ -x "$marker" ]; then
  "$marker" local || echo "UWAGA: nie zapisano znacznika kopii lokalnej." >&2
fi

hook="$here/offsite-backup.hook"
if [ -x "$hook" ]; then
  "$hook" "$archive"
fi
