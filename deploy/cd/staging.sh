#!/usr/bin/env bash
# Staging efemeryczny na runnerze GitHub (Docker jest dostepny): dowod dzialania deploy/cd/deploy.sh PRZED produkcja.
# Uruchamiany z korzenia repo przez .github/workflows/cd-staging.yml. Bez sekretow, bez dostepu do hosta produkcyjnego;
# wszystkie dane syntetyczne (owner@example.test). Czas docelowy ⚠ <= 15 min.
#
#   deploy/cd/staging.sh                                    # budowa N lokalnie z checkoutu (pull_request, workflow_dispatch)
#   STAGING_VERSION=2.0.0 STAGING_API_DIGEST=sha256:... STAGING_WEB_DIGEST=sha256:... deploy/cd/staging.sh
#                                                           # N z rejestru po digestach (wywolanie z release.yml)
#
# Scenariusze (kazdy konczy sie asercja; pierwsza czerwona przerywa przebieg):
#   1. stos N-1 (ten sam kod, wersja 0.0.1 przez cienka warstwe) + organizacja syntetyczna
#   2. deploy.sh deploy N: transport lokalny, smoke lokalny i przez Caddy (profil edge, localhost, lokalne CA Caddy)
#   3. kopia backup.sh + sha256sum -c
#   4. tryb serwisowy on/off na zdrowym stosie
#   5. wstrzykniete uszkodzenie (obraz-pulapka konczy sie bledem): kod 10 i powrot do N; w trakcie drugi `deploy` o innych
#      argumentach dostaje kod 2 (blocked) - wspolbieznosc prawdziwych procesow, nie atrapa blokady
#   5b. allow_schema_change=1 bez haka kopii poza serwerem: kod 2, nic nie zmienione
#   5c. downgrade (DAAS_CD_FAKE_NEW_HEAD mniejszy niz rewizja bazy): kod 2, nic nie zmienione
#   6. symulacja zmiany schematu (DAAS_CD_FAKE_NEW_HEAD, tylko DAAS_CD_STAGING=1) + awaria kokpitu przy zdrowym api:
#      kod 11, bez rollbacku, tryb serwisowy wlaczony i potwierdzony w /api/v1/health, /ready = 200, dowody w katalogu incydentu
#   6b. powtorka po kodzie 11: ponowne `deploy` tej samej wersji przy zatrzymanym api i podrzuconym przestarzalym .env.prev:
#      kod 11 (nie 10 ani 12), .env nadal na tej wersji, tryb serwisowy wlaczony, .env.prev przeniesiony (P1-1)
#   7. smoke selfhost na tym samym obrazie api (jak job `docker` w ci.yml)
# Ograniczenie: "N-1" to ten sam kod z podmieniona wersja w metadanych, a zmiana schematu jest symulowana (DAAS_CD_FAKE_NEW_HEAD);
# prawdziwa migracja N-1 -> N z danymi bedzie sprawdzona dopiero, gdy w GHCR bedzie wydanie N-1 (patrz deploy/CD.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/deploy"
# shellcheck source=deploy/cd/lib.sh
. "$DEPLOY/cd/lib.sh"

# Skrypt usuwa deploy/.env i stan po sobie: tylko na efemerycznym runnerze (CI=true) albo za jawna zgoda.
if [ "${CI:-}" != "true" ] && [ "${DAAS_CD_STAGING_ALLOW:-}" != "1" ]; then
  printf 'staging.sh dziala tylko w CI (CI=true) albo z DAAS_CD_STAGING_ALLOW=1: usuwa deploy/.env po zakonczeniu.\n' >&2
  exit 2
fi
if [ -e "$DEPLOY/.env" ]; then
  printf 'deploy/.env juz istnieje: staging go nie nadpisuje ani nie usuwa.\n' >&2
  exit 2
fi

FAIL_VERSION="99.0.0" # tag obrazu-pulapki api: musi byc nowszy od N, zeby deploy nie byl odrzucony jako downgrade
WEB_FAIL_VERSION="99.0.1" # tag ze zdrowym api i uszkodzonym kokpitem (scenariusz kodu 11)
PREV_VERSION="0.0.1"  # tag obrazu N-1
WORK="$(mktemp -d)"
STACK_UP=0

step() { printf '\n== %s\n' "$*"; }
fail() {
  printf 'STAGING FAIL: %s\n' "$*" >&2
  exit 1
}
expect() { # expect "opis" "wartosc" "oczekiwana"
  if [ "$2" = "$3" ]; then printf 'ok    %s\n' "$1"; else fail "$1: jest '$2', oczekiwano '$3'"; fi
}

cleanup() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$STACK_UP" = "1" ]; then
    echo "--- diagnostyka (dane syntetyczne) ---"
    (cd "$DEPLOY" && docker compose --profile edge ps 2>&1 | tail -n 20) || true
    (cd "$DEPLOY" && docker compose --profile edge logs --no-color --tail 40 api web 2>&1 | tail -n 80) || true
    for f in "$DEPLOY"/cd/log/*.log; do [ -f "$f" ] && { echo "--- $(basename "$f")"; tail -n 60 "$f"; }; done
  fi
  docker rm -f daas-selfhost >/dev/null 2>&1 || true
  (cd "$DEPLOY" && docker compose --profile edge --profile tools down -v --remove-orphans >/dev/null 2>&1) || true
  rm -rf "$WORK" "$DEPLOY/.env" "$DEPLOY/.env.prev" "$DEPLOY"/.env.prev.* "$DEPLOY/.env.tmp."* "$DEPLOY/cd/offsite-backup.hook" \
    "$DEPLOY/cd/state" "$DEPLOY/cd/log" "$DEPLOY/backups"
  exit "$rc"
}
trap cleanup EXIT

# ---------------------------------------------------------------- obrazy

if [ -n "${STAGING_API_DIGEST:-}" ]; then
  MODE="registry"
  VERSION="${STAGING_VERSION:?STAGING_VERSION wymagane razem z digestami}"
  valid_version "$VERSION" && valid_digest "$STAGING_API_DIGEST" && valid_digest "${STAGING_WEB_DIGEST:-}" || fail "niepoprawne argumenty wydania"
  API_REPO="${DAAS_CD_API_REPO:-ghcr.io/emsior/daas-engine-api}"
  WEB_REPO="${DAAS_CD_WEB_REPO:-ghcr.io/emsior/daas-engine-web}"
  API_DIGEST="$STAGING_API_DIGEST"
  WEB_DIGEST="$STAGING_WEB_DIGEST"
else
  MODE="local"
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -n 1)"
  valid_version "$VERSION" || fail "nie ustalono wersji z pyproject.toml"
  API_REPO="daas-engine-api"
  WEB_REPO="daas-engine-web"
  API_DIGEST="local"
  WEB_DIGEST="local"
fi
export DAAS_CD_STAGING=1 DAAS_CD_API_REPO="$API_REPO" DAAS_CD_WEB_REPO="$WEB_REPO" DAAS_CD_DRAIN_TIMEOUT=60

step "Obrazy ($MODE, wersja $VERSION)"
if [ "$MODE" = "local" ]; then
  docker build -q -t "$API_REPO:$VERSION" "$ROOT" >/dev/null
  docker build -q --build-arg NEXT_PUBLIC_API_ORIGIN= -t "$WEB_REPO:$VERSION" "$ROOT/web" >/dev/null
  IMG_API="$API_REPO:$VERSION"
else
  docker pull -q "$API_REPO@$API_DIGEST" >/dev/null
  docker pull -q "$WEB_REPO@$WEB_DIGEST" >/dev/null
  docker tag "$API_REPO@$API_DIGEST" "$API_REPO:$VERSION"
  docker tag "$WEB_REPO@$WEB_DIGEST" "$WEB_REPO:$VERSION"
  IMG_API="$API_REPO@$API_DIGEST"
fi

# N-1: ten sam kod, ale /health ma raportowac inna wersje (health czyta metadane pakietu, nie tag obrazu),
# wiec cienka warstwa podmienia pole Version w METADATA zainstalowanego pakietu. ⚠ nieprzetestowane bez demona.
docker build -q -t "$API_REPO:$PREV_VERSION" - >/dev/null <<EOF
FROM $IMG_API
USER root
RUN python -c "import importlib.metadata as m, pathlib, re; p = pathlib.Path(m.distribution('daas-engine')._path) / 'METADATA'; p.write_text(re.sub(r'(?m)^Version: .*', 'Version: $PREV_VERSION', p.read_text(), count=1))"
USER daas
EOF
docker tag "$WEB_REPO:$VERSION" "$WEB_REPO:$PREV_VERSION"
# Obraz-pulapka: ten sam obraz, ale proces konczy sie od razu z bledem (api nigdy nie wstaje).
docker build -q -t "$API_REPO:$FAIL_VERSION" - >/dev/null <<EOF
FROM $IMG_API
CMD ["sh", "-c", "exit 1"]
EOF
docker tag "$WEB_REPO:$VERSION" "$WEB_REPO:$FAIL_VERSION"
# Scenariusz kodu 11: api zdrowe (ten sam obraz co N), kokpit pada. Lokalny smoke jest czerwony, ale api zyje,
# wiec da sie potwierdzic tryb serwisowy w /api/v1/health.
docker tag "$API_REPO:$VERSION" "$API_REPO:$WEB_FAIL_VERSION"
docker build -q -t "$WEB_REPO:$WEB_FAIL_VERSION" - >/dev/null <<DOCKERFILE
FROM $WEB_REPO:$VERSION
CMD ["sh", "-c", "exit 1"]
DOCKERFILE

# ---------------------------------------------------------------- 1. stos N-1

step "1. Stos N-1 ($PREV_VERSION) + organizacja syntetyczna"
(cd "$DEPLOY" && ./gen-env.sh --domain localhost >/dev/null)
env_set_many "$DEPLOY/.env" "DAAS_VERSION=$PREV_VERSION" "DAAS_API_IMAGE=$API_REPO" "DAAS_WEB_IMAGE=$WEB_REPO"
# `up --pull never` nie pobierze Caddy: robimy to jawnie
(cd "$DEPLOY" && docker compose --profile edge pull -q caddy)
printf '#!/usr/bin/env bash\ntest -s "$1"\n' >"$DEPLOY/cd/offsite-backup.hook" # udaje kopie poza serwerem
chmod +x "$DEPLOY/cd/offsite-backup.hook"
STACK_UP=1
(cd "$DEPLOY" && docker compose --profile edge up -d --no-build --pull never)
(cd "$DEPLOY" && ./smoke.sh --wait 120 --expect-version "$PREV_VERSION" >/dev/null)
echo "ok    N-1 stoi i smoke lokalny zielony"
(cd "$DEPLOY" && docker compose exec -T api python -m app.cli create-org --name "Staging Org" --owner-email owner@example.test >/dev/null)
echo "ok    create-org (dane syntetyczne; klucz API nie jest wypisywany)"

# Lokalne CA Caddy dla https://localhost: curl czyta CURL_CA_BUNDLE (smoke.sh bez zmian i bez -k).
for _ in $(seq 1 30); do
  (cd "$DEPLOY" && docker compose --profile edge cp caddy:/data/caddy/pki/authorities/local/root.crt "$WORK/caddy-root.crt" >/dev/null 2>&1) && break
  sleep 2
done
[ -s "$WORK/caddy-root.crt" ] || fail "nie udalo sie wyciagnac certyfikatu lokalnego CA z kontenera caddy"
export CURL_CA_BUNDLE="$WORK/caddy-root.crt" DAAS_CD_PUBLIC_URL="https://localhost"

# ---------------------------------------------------------------- 2. deploy N

run_deploy() { # run_deploy ... -> ustawia OUT i RC (podsumowanie to jedyna linia stdout)
  RC=0
  OUT="$("$DEPLOY/cd/deploy.sh" "$@")" || RC=$?
  printf '%s\n' "$OUT"
}
field() { printf '%s\n' "$OUT" | tr ' ' '\n' | sed -n "s/^$1=//p"; }

step "2. deploy.sh deploy $VERSION (smoke lokalny + przez Caddy)"
run_deploy deploy "$VERSION" "$API_DIGEST" "$WEB_DIGEST" "$(git -C "$ROOT" rev-parse HEAD)" 0
expect "kod wyjscia" "$RC" 0
expect "result" "$(field result)" ok
expect "previous" "$(field previous)" "$PREV_VERSION"
expect "schema" "$(field schema)" same
expect "edge" "$(field edge)" ok
(cd "$DEPLOY" && ./smoke.sh --wait 30 --expect-version "$VERSION" --public https://localhost >/dev/null)
echo "ok    smoke lokalny i przez Caddy (https://localhost, CURL_CA_BUNDLE)"
run_deploy deploy "$VERSION" "$API_DIGEST" "$WEB_DIGEST" "$(git -C "$ROOT" rev-parse HEAD)" 0
expect "idempotencja: result" "$(field result)" noop

# ---------------------------------------------------------------- 3. kopia

step "3. backup.sh + sha256sum -c"
BACKUP_OUT="$(cd "$DEPLOY" && ./backup.sh)"
ARCHIVE="$(printf '%s\n' "$BACKUP_OUT" | sed -n 's/^BACKUP_ARCHIVE=//p' | tail -n 1)"
[ -s "$ARCHIVE" ] || fail "backup.sh nie utworzyl archiwum"
(cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256")
(cd "$DEPLOY" && ./smoke.sh --wait 90 --expect-version "$VERSION" >/dev/null)
echo "ok    kopia zimna, suma SHA-256 zgodna, api wstalo ponownie"

# ---------------------------------------------------------------- 4. tryb serwisowy

step "4. Tryb serwisowy on/off"
OUT="$("$DEPLOY/cd/deploy.sh" maintenance on)"
expect "maintenance on" "$OUT" "DAAS_MAINTENANCE result=ok code=0 maintenance=on"
expect "health.maintenance=true" "$(curl -fsS http://127.0.0.1:8000/api/v1/health | sed -n 's/.*"maintenance" *: *\(true\|false\).*/\1/p')" true
OUT="$("$DEPLOY/cd/deploy.sh" maintenance off)"
expect "maintenance off" "$OUT" "DAAS_MAINTENANCE result=ok code=0 maintenance=off"
expect "health.maintenance=false" "$(curl -fsS http://127.0.0.1:8000/api/v1/health | sed -n 's/.*"maintenance" *: *\(true\|false\).*/\1/p')" false

# ---------------------------------------------------------------- 5. awaria -> rollback (kod 10) + wspolbieznosc

lock_held() { (exec 8>"$DEPLOY/cd/state/deploy.lock" && ! flock -n 8) 2>/dev/null; }
last_log() { { ls -1 "$DEPLOY"/cd/log/2*.log 2>/dev/null || true; } | sort | tail -n 1; }
ZERO_SHA=0000000000000000000000000000000000000000

step "5. Wstrzykniete uszkodzenie: obraz-pulapka $FAIL_VERSION -> kod 10, powrot do $VERSION (+ drugi deploy w trakcie -> kod 2)"
A_RC=0
"$DEPLOY/cd/deploy.sh" deploy "$FAIL_VERSION" local local "$ZERO_SHA" 0 >"$WORK/deploy_a.out" 2>&1 &
A_PID=$!
for _ in $(seq 1 60); do
  if lock_held; then break; fi
  sleep 1
done
lock_held || fail "pierwszy deploy nie przejal blokady"
run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 0
expect "drugi deploy w trakcie pierwszego: kod" "$RC" 2
expect "drugi deploy w trakcie pierwszego: result" "$(field result)" blocked
wait "$A_PID" || A_RC=$?
cat "$WORK/deploy_a.out"
OUT="$(cat "$WORK/deploy_a.out")"
expect "kod wyjscia" "$A_RC" 10
expect "result" "$(field result)" rollback
expect "wersja po rollbacku" "$(curl -fsS http://127.0.0.1:8000/api/v1/health | sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p')" "$VERSION"
expect ".env po rollbacku" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$VERSION"

step "5b. allow_schema_change=1 bez haka kopii poza serwerem -> kod 2"
mv "$DEPLOY/cd/offsite-backup.hook" "$WORK/offsite-backup.hook"
DAAS_CD_FAKE_NEW_HEAD=9999 run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 1
mv "$WORK/offsite-backup.hook" "$DEPLOY/cd/offsite-backup.hook"
expect "bez haka: kod" "$RC" 2
expect "bez haka: result" "$(field result)" blocked
grep -q 'allow_schema_change=1 wymaga haka' "$(last_log)" || fail "dziennik nie wskazuje brakujacego haka kopii poza serwerem"
expect "bez haka: .env bez zmian" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$VERSION"

step "5c. Downgrade (wydanie ze starszym schematem niz baza) -> kod 2"
DAAS_CD_FAKE_NEW_HEAD=0001 run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 0
expect "downgrade: kod" "$RC" 2
expect "downgrade: result" "$(field result)" blocked
grep -q 'downgrade' "$(last_log)" || fail "dziennik nie wskazuje downgrade'u schematu"
expect "downgrade: .env bez zmian" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$VERSION"

# ---------------------------------------------------------------- 6. zmiana schematu + awaria -> kod 11

step "6. Symulacja zmiany schematu + awaria kokpitu -> kod 11 (bez rollbacku, tryb serwisowy)"
DAAS_CD_FAKE_NEW_HEAD=9999 run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 0
expect "bez allow_schema_change: kod" "$RC" 2
expect "bez allow_schema_change: result" "$(field result)" blocked
expect "wersja bez zmian" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$VERSION"
DAAS_CD_FAKE_NEW_HEAD=9999 run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 1
expect "z allow_schema_change: kod" "$RC" 11
expect "z allow_schema_change: result" "$(field result)" failed
expect "schema" "$(field schema)" changed
expect "brak rollbacku (.env zostaje na wersji N+)" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$WEB_FAIL_VERSION"
expect "podsumowanie: maintenance" "$(field maintenance)" on
expect "podsumowanie: edge" "$(field edge)" ok
expect "health.maintenance=true" "$(curl -fsS http://127.0.0.1:8000/api/v1/health | sed -n 's/.*"maintenance" *: *\(true\|false\).*/\1/p')" true
expect "/ready = 200 w trybie serwisowym" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ready)" 200
ls "$DEPLOY"/cd/log/*-incident/logs.txt "$DEPLOY"/cd/log/*-incident/ps.txt >/dev/null 2>&1 || fail "brak plikow w katalogu incydentu"
echo "ok    katalog incydentu zawiera logs.txt i ps.txt"
[ ! -e "$DEPLOY/.env.prev" ] || fail ".env.prev przezyl kod 11 (powinien byc przeniesiony na .env.prev.<id>)"
ls "$DEPLOY"/.env.prev.2* >/dev/null 2>&1 || fail "brak .env.prev.<id> po kodzie 11"
echo "ok    .env.prev przeniesiony na .env.prev.<id>"

step "6b. Powtorka po kodzie 11: ponowne deploy tej samej wersji przy zatrzymanym api i przestarzalym .env.prev -> kod 11"
(umask 077 && cp -p "$DEPLOY/.env" "$DEPLOY/.env.prev")
env_set_many "$DEPLOY/.env.prev" "DAAS_VERSION=$VERSION" # podrzucony stan N: stary kod cofnalby sie na niego (P1-1)
(cd "$DEPLOY" && docker compose --profile edge stop api >/dev/null 2>&1)
run_deploy deploy "$WEB_FAIL_VERSION" local local "$ZERO_SHA" 0
expect "powtorka: kod (nie 10 ani 12)" "$RC" 11
expect "powtorka: result" "$(field result)" failed
expect "powtorka: .env nadal na wersji N+" "$(env_get "$DEPLOY/.env" DAAS_VERSION)" "$WEB_FAIL_VERSION"
expect "powtorka: maintenance" "$(field maintenance)" on
expect "powtorka: health.maintenance=true" "$(curl -fsS http://127.0.0.1:8000/api/v1/health | sed -n 's/.*"maintenance" *: *\(true\|false\).*/\1/p')" true
[ ! -e "$DEPLOY/.env.prev" ] || fail ".env.prev przezyl powtorke po kodzie 11"
echo "ok    po powtorce .env.prev przeniesiony"
OUT="$("$DEPLOY/cd/deploy.sh" maintenance off)"
expect "maintenance off po incydencie" "$OUT" "DAAS_MAINTENANCE result=ok code=0 maintenance=off"

# ---------------------------------------------------------------- 7. smoke selfhost

step "7. Smoke selfhost na tym samym obrazie api"
HDR="$WORK/selfhost.hdr"
SELFHOST_ENV="$WORK/selfhost.env"
SELFHOST_KEY="$(openssl rand -hex 24)"
(umask 077 && printf 'X-API-Key: %s\n' "$SELFHOST_KEY" >"$HDR" && printf 'DAAS_API_KEY=%s\n' "$SELFHOST_KEY" >"$SELFHOST_ENV")
SELFHOST_KEY=""
# Klucz trafia do kontenera przez --env-file (plik 0600), nie przez -e w argumentach procesu docker.
docker run -d --rm --name daas-selfhost \
  --env-file "$SELFHOST_ENV" -p 127.0.0.1:8002:8000 "$API_REPO:$VERSION" >/dev/null
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8002/health >/dev/null && break || sleep 1; done
curl -sf http://127.0.0.1:8002/health >/dev/null
expect "bez klucza 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8002/run -H 'Content-Type: application/json' -d '{"pipeline":"cs2_demo"}')" 401
curl -sf -X POST http://127.0.0.1:8002/run -H "@$HDR" -H "Content-Type: application/json" -d '{"pipeline":"cs2_demo"}' | grep -q '"SUCCESS"'
curl -sf -H "@$HDR" -F "file=@$ROOT/data/fixtures/client_export_pl.csv" http://127.0.0.1:8002/upload | grep -q '"ready":true'
echo "ok    selfhost: 401 bez klucza, run cs2_demo, upload fixture"

printf '\nSTAGING OK (%s, wersja %s)\n' "$MODE" "$VERSION"
