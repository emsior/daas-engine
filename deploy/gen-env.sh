#!/usr/bin/env bash
# Generuje deploy/.env z losowymi sekretami (openssl rand). NIE nadpisuje istniejacego pliku.
#
#   ./gen-env.sh --domain raporty.example.com
#   ./gen-env.sh                     # bez domeny: localhost (proby lokalne)
#
# Plik ma prawa 0600 i jest ignorowany przez git. Sekretow nie wypisuje na ekran.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${DAAS_ENV_FILE:-$here/.env}"
domain="localhost"

usage() {
  echo "Uzycie: $0 [--domain DOMENA]" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain)
      [ "$#" -ge 2 ] || usage
      domain="$2"
      shift 2
      ;;
    -h | --help) usage ;;
    *) usage ;;
  esac
done

if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Niepoprawna domena: $domain" >&2
  exit 2
fi

if [ -e "$target" ]; then
  echo "$target juz istnieje: nic nie zmieniam (aby wygenerowac od nowa, przenies stary plik poza repozytorium)." >&2
  exit 0
fi

command -v openssl >/dev/null 2>&1 || {
  echo "Brak polecenia openssl." >&2
  exit 1
}

version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$here/../pyproject.toml" | head -n 1)"
version="${version:-2.0.0}"

if [ "$domain" = "localhost" ]; then
  origin="https://localhost"
else
  origin="https://$domain"
fi

umask 077
secret_key="$(openssl rand -hex 32)"
metrics_token="$(openssl rand -hex 24)"

cat >"$target" <<ENVFILE
# DaaS Engine Cloud: konfiguracja wdrozenia. Wygenerowano $(date -u +%Y-%m-%dT%H:%M:%SZ) przez deploy/gen-env.sh.
# Plik zawiera sekrety: NIE wkladaj do repozytorium, trzymaj kopie w menedzerze sekretow.
# Zmiana DAAS_SECRET_KEY wylogowuje wszystkich i uniewaznia sekrety podpisu webhookow.

# --- Wdrozenie ---
DAAS_VERSION=$version
DAAS_DOMAIN=$domain
DAAS_COCKPIT_ORIGIN=$origin
# Puste = ten sam origin co kokpit (Caddy). Zmiana wymaga: docker compose build web
NEXT_PUBLIC_API_ORIGIN=

# --- Sekrety (min. 32 znaki) ---
DAAS_SECRET_KEY=$secret_key
# Token dla GET /metrics (Authorization: Bearer ...). Metryki sa osiagalne tylko na 127.0.0.1:8000.
DAAS_METRICS_TOKEN=$metrics_token

# --- Logowanie e-mailem (magic link) ---
# console = link tylko w runtime/outbox/ (w produkcji nie ma go w dzienniku): wylacznie proby. Produkcja: smtp.
# API odmawia startu przy console + APP_ENV=prod bez jawnej zgody ponizej; przy smtp usun DAAS_ALLOW_CONSOLE_MAIL.
DAAS_EMAIL_BACKEND=console
DAAS_ALLOW_CONSOLE_MAIL=true
# DAAS_EMAIL_BACKEND=smtp
# DAAS_EMAIL_FROM=DaaS Engine <raporty@$domain>
# DAAS_SMTP_HOST=smtp.example.com
# DAAS_SMTP_PORT=587
# DAAS_SMTP_USER=
# DAAS_SMTP_PASSWORD=
# DAAS_SMTP_STARTTLS=true

# --- Limity (domyslne wartosci: docs/DEPLOY.md) ---
# DAAS_MAX_CONCURRENT_RUNS=2
# DAAS_UPLOAD_QUOTA_BYTES=1073741824
# DAAS_RETENTION_DAYS=90
ENVFILE

chmod 600 "$target"
echo "Utworzono $target (0600). Nastepny krok:"
echo "  docker compose --profile edge up -d --build"
echo "  docker compose exec api python -m app.cli create-org --name \"Nazwa\" --owner-email wlasciciel@example.com"
