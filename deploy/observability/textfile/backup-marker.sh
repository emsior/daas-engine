#!/usr/bin/env bash
# Przejsciowy znacznik L-8 (SLO-4, alert BackupStale): zapisuje czas ostatniej UDANEJ kopii jako metryke textfile
# `daas_backup_last_success_timestamp_seconds{target="..."}`.
#
# Wolac WYLACZNIE po calym lancuchu sukcesu, jako ostatni krok przed heartbeatem (RUNBOOK §4.3):
#   backup.sh -> restic backup -> restic forget --prune -> sha256sum -c -> backup-marker.sh offsite -> heartbeat
# Skrypt sam niczego nie weryfikuje: znacznik ma znaczyc "kopia sprawdzona". Hak kopii poza serwerem to
# deploy/cd/offsite-backup.hook (wymaganie dla DevOps, raport 03_observability.md).
#
#   backup-marker.sh offsite     # kopia poza serwerem (SLO-4)
#   backup-marker.sh local       # kopia lokalna backup.sh (informacyjnie, bez alertu)
#
# Zmienna: DAAS_TEXTFILE_DIR (domyslnie /var/lib/node_exporter/textfile).
set -euo pipefail

target="${1:-}"
case "$target" in
  offsite | local) ;;
  *)
    echo "Uzycie: $0 offsite|local" >&2
    exit 2
    ;;
esac

textfile_dir="${DAAS_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
final="$textfile_dir/daas_backup_$target.prom"
now="$(date +%s)"

tmp="$(mktemp "$textfile_dir/.daas_backup_$target.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<PROM
# HELP daas_backup_last_success_timestamp_seconds Czas ostatniej udanej i sprawdzonej kopii (sekundy Unix).
# TYPE daas_backup_last_success_timestamp_seconds gauge
daas_backup_last_success_timestamp_seconds{target="$target"} $now
PROM
chmod 0644 "$tmp"
mv -f "$tmp" "$final"
trap - EXIT
