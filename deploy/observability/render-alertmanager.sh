#!/usr/bin/env bash
# Renderuje lokalna konfiguracje Alertmanagera z szablonu alertmanager.yml (podstawia placeholdery <<...>>).
# Uruchamiane NA HOSCIE przez operatora; wynik (observability/local/alertmanager.yml) jest ignorowany przez git.
#
#   ALERT_SMTP_HOSTPORT=<host>:587 ALERT_SMTP_USER=<login> ALERT_FROM=<nadawca> \
#     ALERT_TO=<odbiorca> ./observability/render-alertmanager.sh
#
# Nakladka compose montuje wlasnie ten plik (fail-closed: bez niego `docker compose up` konczy sie bledem).
# Potem `docker compose up -d alertmanager`. Hasla SMTP tu nie ma: jest w observability/secrets/smtp_password.
# ALERT_OUT_DIR (opcjonalnie) zmienia katalog docelowy; uzywa go CI (check.sh), operator zostawia domyslny.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="$here/alertmanager.yml"
out_dir="${ALERT_OUT_DIR:-$here/local}"
out="$out_dir/alertmanager.yml"

usage() {
  echo "Uzycie: ALERT_SMTP_HOSTPORT=host:port ALERT_SMTP_USER=... ALERT_FROM=... ALERT_TO=... $0" >&2
  exit 2
}

hostport="${ALERT_SMTP_HOSTPORT:-}"
smtp_user="${ALERT_SMTP_USER:-}"
mail_from="${ALERT_FROM:-}"
mail_to="${ALERT_TO:-}"
[ -n "$hostport" ] && [ -n "$smtp_user" ] && [ -n "$mail_from" ] && [ -n "$mail_to" ] || usage

# Bezpieczny zestaw znakow: wartosci trafiaja do YAML, wiec bez cudzyslowow, spacji, ukosnikow i znakow specjalnych.
safe='^[A-Za-z0-9._@:+-]+$'
for value in "$hostport" "$smtp_user" "$mail_from" "$mail_to"; do
  [[ "$value" =~ $safe ]] || {
    echo "Niedozwolone znaki w jednej z wartosci (dozwolone: litery, cyfry i . _ @ : + -)." >&2
    exit 2
  }
done
[[ "$hostport" =~ ^[A-Za-z0-9.-]+:[0-9]{1,5}$ ]] || {
  echo "ALERT_SMTP_HOSTPORT musi miec postac host:port." >&2
  exit 2
}
[ -r "$template" ] || {
  echo "Brak szablonu $template." >&2
  exit 1
}

# Podstawienie tylko w liniach nie bedacych komentarzami (komentarze szablonu opisuja placeholdery i zostaja jak sa).
content=""
while IFS= read -r line || [ -n "$line" ]; do
  if [[ ! "$line" =~ ^[[:space:]]*# ]]; then
    line="${line//<<SMTP_HOST:587>>/$hostport}"
    line="${line//<<SMTP_USER>>/$smtp_user}"
    line="${line//<<ALERT_FROM>>/$mail_from}"
    line="${line//<<ALERT_TO>>/$mail_to}"
    if [[ "$line" == *"<<"*">>"* ]]; then
      echo "W szablonie zostal nierozwiazany placeholder." >&2
      exit 1
    fi
  fi
  content+="$line"$'\n'
done <"$template"

umask 022
mkdir -p "$out_dir"
tmp="$(mktemp "$out_dir/.alertmanager.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf '%s' "$content" >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out"
trap - EXIT
echo "Zapisano $out. Zrestartuj alertmanager (docker compose up -d alertmanager)."
