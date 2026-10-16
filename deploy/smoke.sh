#!/usr/bin/env bash
# Test dymny po wdrozeniu: API (/health, /ready, /api/v1/health, 401 bez klucza) i kokpit (/login).
#
#   ./smoke.sh                                   # API http://127.0.0.1:8000, kokpit http://127.0.0.1:3000
#   ./smoke.sh --public https://raporty.example.com --wait 90 --expect-version 2.0.0
#
# Opcje: --api URL, --web URL, --public URL (dodatkowo przez proxy: TLS, nagłówki, ukryte /metrics, metadane OAuth, /mcp bez klucza),
#        --wait SEKUNDY (czeka na gotowosc API), --expect-version X.Y.Z (porownuje z /health).
#        --expect-mcp on|off|auto, --expect-oauth on|off|auto (domyslnie auto: porownanie proxy z --api).
# Kod wyjscia: 0 = wszystko dziala, 1 = co najmniej jedna kontrola nie przeszla.
set -uo pipefail

api="http://127.0.0.1:8000"
web="http://127.0.0.1:3000"
public=""
wait_sec=0
expect_version=""
expect_mcp="auto"
expect_oauth="auto"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --api) api="${2:?}"; shift 2 ;;
    --web) web="${2:?}"; shift 2 ;;
    --public) public="${2:?}"; shift 2 ;;
    --wait) wait_sec="${2:?}"; shift 2 ;;
    --expect-version) expect_version="${2:?}"; shift 2 ;;
    --expect-mcp | --expect-oauth)
      case "${2:-}" in
        on | off | auto) ;;
        *) echo "Bledna wartosc $1: oczekiwano on|off|auto." >&2; exit 2 ;;
      esac
      if [ "$1" = "--expect-mcp" ]; then expect_mcp="$2"; else expect_oauth="$2"; fi
      shift 2
      ;;
    -h | --help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Nieznany argument: $1" >&2
      exit 2
      ;;
  esac
done

command -v curl >/dev/null 2>&1 || {
  echo "Brak polecenia curl." >&2
  exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fail=0

# check NAZWA URL OCZEKIWANY_KOD [WZORZEC_W_TRESCI]
check() {
  local name="$1" url="$2" want="$3" pattern="${4:-}" code
  code="$(curl -sS -o "$tmp/body" -w '%{http_code}' --max-time 15 "$url" 2>"$tmp/err" || true)"
  if [ "$code" != "$want" ]; then
    echo "FAIL  $name: $url -> HTTP ${code:-brak odpowiedzi} (oczekiwano $want) $(tr -d '\n' <"$tmp/err")"
    fail=1
    return
  fi
  if [ -n "$pattern" ] && ! grep -Eq "$pattern" "$tmp/body"; then
    echo "FAIL  $name: $url -> HTTP $code, ale tresc nie pasuje do: $pattern"
    fail=1
    return
  fi
  echo "ok    $name ($code)"
}

# oczekiwanie na gotowosc API
if [ "$wait_sec" -gt 0 ]; then
  echo "Czekam do ${wait_sec}s na $api/ready ..."
  end=$((SECONDS + wait_sec))
  until curl -sf --max-time 5 -o /dev/null "$api/ready"; do
    if [ "$SECONDS" -ge "$end" ]; then
      echo "FAIL  API nie jest gotowe po ${wait_sec}s"
      exit 1
    fi
    sleep 2
  done
fi

check "api /health" "$api/health" 200 '"status" *: *"ok"'
check "api /ready" "$api/ready" 200 '"status" *: *"ready"'
check "api /api/v1/health" "$api/api/v1/health" 200 '"mode" *: *"cloud"'
check "api /api/v1/me bez klucza" "$api/api/v1/me" 401
check "kokpit /login" "$web/login" 200

if [ -n "$expect_version" ]; then
  version="$(curl -s --max-time 10 "$api/health" | sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p')"
  if [ "$version" = "$expect_version" ]; then
    echo "ok    wersja $version"
  else
    echo "FAIL  wersja: jest '${version:-?}', oczekiwano '$expect_version'"
    fail=1
  fi
fi

if [ -n "$public" ]; then
  public="${public%/}"
  check "proxy /api/v1/health" "$public/api/v1/health" 200 '"status" *: *"ok"'
  check "proxy /login" "$public/login" 200
  check "proxy /api/v1/metrics ukryte" "$public/api/v1/metrics" 404
  if curl -sSI --max-time 15 "$public/login" | tr -d '\r' | grep -qi '^strict-transport-security:'; then
    echo "ok    nagłówek Strict-Transport-Security"
  else
    echo "FAIL  brak nagłówka Strict-Transport-Security na $public/login"
    fail=1
  fi

  # Flagowe funkcje 2.0: samo 404 nie dowodzi wylaczenia funkcji.
  # auto wymaga zgodnosci z bezposrednim API; on/off to jawne oczekiwanie operatora.
  check_feature() {
    local name="$1" path="$2" mode="$3" enabled="$4" option="$5" pattern="${6:-}"
    local proxy_code api_code want="$enabled" detail
    local -a request=()
    if [ "$path" = "/mcp" ]; then
      request=(-X POST -H 'content-type: application/json' --data '{}')
    fi
    proxy_code="$(curl -sS -o "$tmp/body" -w '%{http_code}' --max-time 15 "${request[@]}" "$public$path" 2>/dev/null)" || proxy_code="000"
    detail="proxy=$proxy_code"
    if [ "$mode" = "auto" ]; then
      api_code="$(curl -sS -o "$tmp/api-body" -w '%{http_code}' --max-time 15 "${request[@]}" "${api%/}$path" 2>/dev/null)" || api_code="000"
      detail="$detail, API=$api_code"
      if { [ "$api_code" != "$enabled" ] && [ "$api_code" != "404" ]; } ||
         { [ "$api_code" = "$enabled" ] && [ -n "$pattern" ] && ! grep -q "$pattern" "$tmp/api-body"; }; then
        echo "FAIL  $name ($detail): brak dowodu oczekiwanego stanu: podaj $option on|off (API: ${api%/}$path)"
        fail=1
        return
      fi
      want="$api_code"
    elif [ "$mode" = "off" ]; then
      want="404"
    fi

    if [ "$proxy_code" != "$want" ]; then
      if [ "$want" = "404" ] && [ "$proxy_code" = "$enabled" ]; then
        echo "FAIL  $name ($detail): funkcja wlaczona, choc miala byc wylaczona"
      else
        echo "FAIL  $name ($detail): $public$path (oczekiwano $want)"
      fi
      fail=1
      return
    fi
    if [ "$want" = "$enabled" ] && [ -n "$pattern" ] && ! grep -q "$pattern" "$tmp/body"; then
      echo "FAIL  $name ($detail): brak $pattern w odpowiedzi proxy"
      fail=1
      return
    fi
    if [ "$want" = "404" ]; then
      echo "ok    $name ($detail; funkcja wylaczona, dowod: $mode)"
    else
      echo "ok    $name ($detail)"
    fi
  }

  check_feature "proxy metadane OAuth" "/.well-known/oauth-authorization-server" "$expect_oauth" 200 --expect-oauth '"issuer"'
  check_feature "proxy /mcp bez klucza" "/mcp" "$expect_mcp" 401 --expect-mcp
fi

if [ "$fail" = "0" ]; then
  echo "SMOKE OK"
else
  echo "SMOKE FAIL" >&2
fi
exit "$fail"
