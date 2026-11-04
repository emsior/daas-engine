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

# Twarde limity czasu (ta sama blokada co deploy.sh: zawieszona kopia albo wysylka zablokowalaby wszystkie wdrozenia).
backup_timeout="${DAAS_CD_BACKUP_TIMEOUT:-900}"
hook_timeout="${DAAS_CD_HOOK_TIMEOUT:-1200}"
rc=0
out="$(timeout -k 30 "$backup_timeout" ./backup.sh)" || rc=$?
printf '%s\n' "$out"
if [ "$rc" != "0" ]; then
  if [ "$rc" = "124" ] || [ "$rc" = "137" ]; then
    echo "backup.sh przekroczyl limit czasu ${backup_timeout}s i zostal przerwany (sprawdz: docker compose ps, czy api dziala)." >&2
  else
    echo "backup.sh nie powiodl sie (kod $rc)." >&2
  fi
  exit 1
fi
archive="$(printf '%s\n' "$out" | sed -n 's/^BACKUP_ARCHIVE=//p' | tail -n 1)"
[ -f "$archive" ] || { echo "backup.sh nie zwrocil archiwum." >&2; exit 1; }
(cd "$(dirname "$archive")" && sha256sum -c --status "$(basename "$archive").sha256") || { echo "suma SHA-256 kopii nie zgadza sie." >&2; exit 1; }

marker="$deploy_dir/observability/textfile/backup-marker.sh"
if grep -qs '^COMPOSE_FILE=.*compose\.observability\.yml' .env && [ -x "$marker" ]; then
  "$marker" local || echo "UWAGA: nie zapisano znacznika kopii lokalnej." >&2
fi

hook="$here/offsite-backup.hook"
if [ -x "$hook" ]; then
  rc=0
  timeout -k 30 "$hook_timeout" "$hook" "$archive" || rc=$?
  if [ "$rc" = "124" ] || [ "$rc" = "137" ]; then
    echo "hak kopii poza serwerem przekroczyl limit czasu ${hook_timeout}s i zostal przerwany (blokada wdrozen zwolniona)." >&2
    exit 1
  elif [ "$rc" != "0" ]; then
    echo "hak kopii poza serwerem zakonczyl sie bledem (kod $rc)." >&2
    exit 1
  fi
fi
