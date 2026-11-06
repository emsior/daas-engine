#!/usr/bin/env bash
# Minimalny stos do asercji obserwowalnosci: api + Prometheus + Alertmanager + node_exporter z nakladki
# (bez web, Caddy i Grafany). Sprawdza: cele api/node/alertmanager maja up == 1, istnieje daas_build_info,
# Watchdog plonie, a API zarzadzania Prometheusa (--web.enable-lifecycle) jest wylaczone (403).
#
# UWAGA: skrypt TWORZY deploy/.env (gen-env.sh), atrapy sekretow i wyrenderowana konfiguracje Alertmanagera,
# buduje obraz api i po zakonczeniu KASUJE te pliki oraz wolumeny (docker compose down -v). Przeznaczony dla CI
# albo czystego katalogu roboczego; odmawia pracy, gdy deploy/.env juz istnieje (kod 2).
# Atrapy sekretow sa jawne (0644) tylko dlatego, ze kontenery dzialaja jako nobody na efemerycznym runnerze.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deploy_dir="$(cd "$here/.." && pwd)"
cd "$deploy_dir"

if [ -e .env ]; then
  echo "stack-check.sh: deploy/.env istnieje: nie nadpisuje i nie kasuje (uzyj czystego katalogu)." >&2
  exit 2
fi
command -v docker >/dev/null 2>&1 || {
  echo "stack-check.sh: brak polecenia docker" >&2
  exit 1
}

work="$(mktemp -d)"
export COMPOSE_FILE=compose.yml:compose.observability.yml
export DAAS_TEXTFILE_DIR="$work/textfile"

cleanup() {
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work" .env observability/local \
    observability/secrets/metrics_token observability/secrets/smtp_password observability/secrets/heartbeat_url
}
trap cleanup EXIT

mkdir -p "$work/textfile"
chmod 0755 "$work" "$work/textfile"

./gen-env.sh --domain localhost >/dev/null
umask 022
sed -n 's/^DAAS_METRICS_TOKEN=//p' .env | tr -d '\r\n' >observability/secrets/metrics_token
printf '%s' 'ci-dummy-password' >observability/secrets/smtp_password
printf '%s' 'http://127.0.0.1:9/x' >observability/secrets/heartbeat_url
ALERT_SMTP_HOSTPORT=smtp.test.invalid:587 ALERT_SMTP_USER=ci ALERT_FROM=ci-from ALERT_TO=ci-to \
  ./observability/render-alertmanager.sh >/dev/null

docker compose up -d --build api prometheus alertmanager node_exporter

# Wartosc pierwszej serii zapytania PromQL (pusty wynik = brak serii albo Prometheus jeszcze nie odpowiada).
prom_value() {
  curl -fsS --max-time 5 --get --data-urlencode "query=$1" http://127.0.0.1:9090/api/v1/query 2>/dev/null |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "")' 2>/dev/null || true
}

checks=(
  'up{job="api"}'
  'up{job="node"}'
  'up{job="alertmanager"}'
  'count(daas_build_info)'
  'ALERTS{alertname="Watchdog",alertstate="firing"}'
)

all_ok=0
for _ in $(seq 1 36); do # do ok. 6 min: budowa api jest przed petla, tu czekamy na scrape (30 s) i ocene regul (30 s)
  all_ok=1
  for expr in "${checks[@]}"; do
    [ "$(prom_value "$expr")" = "1" ] || all_ok=0
  done
  [ "$all_ok" = "1" ] && break
  sleep 10
done

if [ "$all_ok" != "1" ]; then
  echo "stack-check.sh: ASERCJA NIEUDANA. Wyniki zapytan:" >&2
  for expr in "${checks[@]}"; do
    echo "  [$(prom_value "$expr")] $expr" >&2
  done
  docker compose ps >&2 || true
  curl -fsS --max-time 5 http://127.0.0.1:9090/api/v1/targets 2>/dev/null |
    python3 -c 'import json,sys
for t in json.load(sys.stdin)["data"]["activeTargets"]:
    print("  cel", t["labels"].get("job"), t["health"], t.get("lastError", ""))' >&2 || true
  for svc in prometheus alertmanager node_exporter; do
    echo "--- logi $svc (ostatnie 20 linii)" >&2
    docker compose logs --no-color --tail 20 "$svc" >&2 || true
  done
  exit 1
fi
echo "OK: cele api, node, alertmanager maja up == 1; daas_build_info obecne; Watchdog plonie."

code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -X POST http://127.0.0.1:9090/-/reload || true)"
if [ "$code" != "403" ]; then
  echo "stack-check.sh: POST /-/reload zwrocil $code zamiast 403 (lifecycle API powinno byc wylaczone)." >&2
  exit 1
fi
echo "OK: lifecycle API wylaczone (403)."
