#!/usr/bin/env bash
# Uruchamia pełny stos e2e: backend (DAAS_MODE=cloud, e-mail `console`) + kokpit (standalone) + proxy jednego originu.
#   web/e2e/stack.sh start   # buduje kokpit (jeśli brak .next) i startuje trzy procesy w tle
#   web/e2e/stack.sh stop    # zatrzymuje tylko procesy z pliku PID
# Zmienne: E2E_RUNTIME (katalog danych, kasowany przy starcie), API_PORT, WEB_PORT, PROXY_PORT, PYTHON.
# Po starcie: E2E_BASE_URL=http://127.0.0.1:3100 E2E_OUTBOX_DIR=$E2E_RUNTIME/outbox npm run e2e
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB="$ROOT/web"
RT="${E2E_RUNTIME:-${TMPDIR:-/tmp}/daas-e2e}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
PROXY_PORT="${PROXY_PORT:-3100}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
PIDS="$RT/pids"

wait_for() { # url, nazwa
  for _ in $(seq 1 90); do
    if curl -fs -o /dev/null "$1"; then return 0; fi
    sleep 1
  done
  echo "timeout: $2 ($1)" >&2
  return 1
}

stop() {
  if [ -f "$PIDS" ]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
    rm -f "$PIDS"
  fi
}

start() {
  stop
  rm -rf "$RT"; mkdir -p "$RT/reports"
  if [ ! -f "$WEB/.next/standalone/server.js" ]; then
    # pusty origin API = ten sam origin co kokpit (proxy odtwarza Caddy)
    (cd "$WEB" && NEXT_PUBLIC_API_ORIGIN= npm run build)
  fi
  mkdir -p "$WEB/.next/standalone/.next"
  rm -rf "$WEB/.next/standalone/public" && cp -r "$WEB/public" "$WEB/.next/standalone/public"
  rm -rf "$WEB/.next/standalone/.next/static" && cp -r "$WEB/.next/static" "$WEB/.next/standalone/.next/static"

  (
    cd "$ROOT"
    export DAAS_MODE=cloud APP_ENV=dev DAAS_EMAIL_BACKEND=console
    DAAS_SECRET_KEY="$(openssl rand -hex 32)"; export DAAS_SECRET_KEY
    export DAAS_COCKPIT_ORIGIN="http://127.0.0.1:$PROXY_PORT"
    export DAAS_CONTROL_DB_URL="sqlite+aiosqlite:///$RT/control.db"
    export DUCKDB_PATH="$RT/daas.duckdb" REPORTS_DIR="$RT/reports"
    # limity logowania wyłączone: seria testów loguje się z jednego adresu IP
    export DAAS_RATE_LIMIT_MAGIC_IP_PER_MIN=0 DAAS_RATE_LIMIT_MAGIC_EMAIL_PER_MIN=0 DAAS_RATE_LIMIT_IP_PER_MIN=0
    nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" > "$RT/api.log" 2>&1 < /dev/null &
    echo $! >> "$PIDS"
  )
  (
    cd "$WEB/.next/standalone"
    export PORT="$WEB_PORT" HOSTNAME=127.0.0.1 API_INTERNAL_ORIGIN="http://127.0.0.1:$API_PORT"
    nohup node server.js > "$RT/web.log" 2>&1 < /dev/null &
    echo $! >> "$PIDS"
  )
  (
    cd "$WEB"
    export E2E_API_ORIGIN="http://127.0.0.1:$API_PORT" E2E_WEB_ORIGIN="http://127.0.0.1:$WEB_PORT" E2E_PROXY_PORT="$PROXY_PORT"
    nohup node e2e/proxy.mjs > "$RT/proxy.log" 2>&1 < /dev/null &
    echo $! >> "$PIDS"
  )

  wait_for "http://127.0.0.1:$API_PORT/ready" "API"
  wait_for "http://127.0.0.1:$PROXY_PORT/login" "kokpit przez proxy"
  echo "gotowe: E2E_BASE_URL=http://127.0.0.1:$PROXY_PORT E2E_OUTBOX_DIR=$RT/outbox (logi: $RT/*.log)"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  *) echo "użycie: $0 start|stop" >&2; exit 2 ;;
esac
