#!/usr/bin/env bash
# Przejsciowy licznik L-4 (SLO-2, alert MailSendFailing): liczy linie `magic link e-mail failed` w dzienniku api
# z ostatnich 30 min i 24 h i zapisuje je jako metryki textfile dla node_exportera. NIE wypisuje tresci dziennika:
# kontener `docker compose logs` jest czytany do prywatnego pliku tymczasowego (0700), liczony `grep -c`, plik kasowany.
# Znika po wdrozeniu L-4 (daas_mail_send_total w aplikacji): wtedy usun cron i regule source: interim.
#
# Cron operatora (uzytkownik z dostepem do dockera), co 5 minut:
#   */5 * * * * /opt/daas-engine/deploy/observability/textfile/mail-failures.sh
#
# Zmienne: DAAS_DEPLOY_DIR (domyslnie deploy/ tego repo), DAAS_TEXTFILE_DIR (domyslnie /var/lib/node_exporter/textfile).
# Metryki: daas_mail_failures_30m, daas_mail_failures_24h (gauge, okno przesuwne), daas_textfile_collect_success.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy_dir="${DAAS_DEPLOY_DIR:-$(cd "$here/../.." && pwd)}"
textfile_dir="${DAAS_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
final="$textfile_dir/daas_mail_failures.prom"
pattern='magic link e-mail failed'

work="$(mktemp -d)"
tmp_prom="$(mktemp "$textfile_dir/.daas_mail_failures.XXXXXX")"
trap 'rm -rf "$work"; rm -f "$tmp_prom"' EXIT

publish() {
  chmod 0644 "$tmp_prom"
  mv -f "$tmp_prom" "$final"
}

# Liczba linii z oknem czasowym $1 (np. 30m, 24h); wynik na stdout, brak wypisywania tresci.
count_window() {
  local since="$1" log="$work/api.log" n
  # `|| return 1` jest konieczne: w kontekscie `if !` set -e nie dziala, a pusty dziennik wygladalby jak "0 bledow".
  (cd "$deploy_dir" && docker compose logs --no-color --no-log-prefix --since "$since" api >"$log" 2>/dev/null) || return 1
  n="$(grep -cF -- "$pattern" "$log" || true)"
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$n"
  rm -f "$log"
}

if ! n30="$(count_window 30m)" || ! n24="$(count_window 24h)"; then
  cat >"$tmp_prom" <<PROM
# HELP daas_textfile_collect_success 1, gdy ostatni przebieg skryptu textfile sie udal.
# TYPE daas_textfile_collect_success gauge
daas_textfile_collect_success{collector="mail_failures"} 0
PROM
  publish
  echo "mail-failures: nie udalo sie odczytac dziennika api" >&2
  exit 1
fi

cat >"$tmp_prom" <<PROM
# HELP daas_mail_failures_30m Bledy wysylki linku logowania (linie dziennika api) w ostatnich 30 minutach.
# TYPE daas_mail_failures_30m gauge
daas_mail_failures_30m $n30
# HELP daas_mail_failures_24h Bledy wysylki linku logowania (linie dziennika api) w ostatnich 24 godzinach.
# TYPE daas_mail_failures_24h gauge
daas_mail_failures_24h $n24
# HELP daas_textfile_collect_success 1, gdy ostatni przebieg skryptu textfile sie udal.
# TYPE daas_textfile_collect_success gauge
daas_textfile_collect_success{collector="mail_failures"} 1
PROM
publish
