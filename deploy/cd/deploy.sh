#!/usr/bin/env bash
# Wdrozenie i wycofanie wersji DaaS Engine Cloud na hoscie (recreate + kopia + smoke + automatyczny rollback obrazu).
# Ten sam skrypt dziala na produkcji (wywolywany przez ssh-gate.sh z runnera GitHub) i w stagingu (staging.sh, lokalnie).
#
#   deploy.sh deploy <X.Y.Z> <sha256:api> <sha256:web> <sha1-commitu-40hex> <0|1 allow_schema_change> [--force]
#   deploy.sh rollback [--force]       # reczny rollback obrazu do poprzedniej wersji (ta sama brama schematu)
#   deploy.sh status                   # ostatni stan: faza albo wynik
#   deploy.sh maintenance on|off       # reczne wlaczenie/wylaczenie trybu serwisowego (DAAS_MAINTENANCE_MODE); odtwarza tylko `api`
#
# Praca biegnie ODLACZONA od sesji SSH (systemd-run --user, a gdy niedostepne: setsid nohup) z plikiem stanu
# deploy/cd/state/<id>.json. Zerwanie SSH niczego nie przerywa; ponowne `deploy` z tymi samymi argumentami dolacza
# do trwajacej operacji albo (gdy wersja juz stoi i smoke jest zielony) konczy sie bez zmian (noop).
# Pelny dziennik zostaje na hoscie (deploy/cd/log/<id>.log). Na stdout wraca WYLACZNIE jedna linia:
#   DAAS_DEPLOY result=<ok|noop|rollback|blocked|failed> code=<n> version=<X.Y.Z> previous=<X.Y.Z|none> schema=<same|changed|unknown> duration=<s> maintenance=<on|off|unverified>
# (maintenance=on: przy kodzie 11 lokalny smoke byl czerwony, wiec skrypt wlaczyl tryb serwisowy i potwierdzil to w /api/v1/health;
#  unverified: .env ustawiono, ale /health nie potwierdzil; off: tryb nie byl wlaczany przez ten przebieg)
# (stdout jest czytany w publicznym logu GitHub Actions: zero sekretow, zero adresow).
#
# Kody wyjscia (kontrakt dla SRE i obserwowalnosci):
#   0   wdrozone i smoke zielony (albo noop)           -> stan: wersja N
#   2   preflight lub brama nie przeszly               -> stan: bez zmian (wersja N-1)
#   10  smoke N nie przeszedl, rollback do N-1 udany   -> stan: wersja N-1
#   11  smoke N nie przeszedl, rollback zabroniony     -> INCYDENT (RUNBOOK §9, README wariant B); zmiana schematu albo pierwsze wdrozenie.
#                                                         Czerwony LOKALNY smoke: zapis logow do deploy/cd/log/<id>-incident/ + tryb serwisowy
#                                                         wlaczony automatycznie. Czerwony tylko --public (Caddy/DNS/TLS): tylko kod 11, tryb bez zmian.
#                                                         Powrot z trybu serwisowego wylacznie recznie: `deploy.sh maintenance off`.
#   12  rollback podjety i nieudany                    -> INCYDENT P1, stan nieokreslony
#   3   (tylko `status`) operacja trwa;  4  (tylko `status`) brak stanu
#
# Zmienne srodowiska (domyslne wartosci produkcyjne; ssh-gate.sh czyści wszystkie DAAS_CD_* i CURL_CA_BUNDLE):
#   DAAS_CD_STAGING=1          tryb stagingu: bez git fetch/checkout, digest `local` (obraz juz zbudowany lokalnie),
#                              honoruje DAAS_CD_FAKE_NEW_HEAD (symulacja nowej migracji do testu bramy schematu)
#   DAAS_CD_API_REPO / DAAS_CD_WEB_REPO   repozytoria obrazow (domyslnie ghcr.io/emsior/daas-engine-api|web)
#   DAAS_CD_PUBLIC_URL         adres do `smoke.sh --public` (domyslnie https://$DAAS_DOMAIN, gdy to nie localhost)
#   DAAS_CD_DRAIN_TIMEOUT      limit drenazu zadan `running` w sekundach (domyslnie 600)
# Hak kopii poza serwerem: wykonywalny plik deploy/cd/offsite-backup.hook (wolany z jednym argumentem: sciezka archiwum).
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
CD_DIR="$(dirname "$SELF")"
# shellcheck source=deploy/cd/lib.sh
. "$CD_DIR/lib.sh"

DEPLOY_DIR="$(cd "$CD_DIR/.." && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
STATE_DIR="$CD_DIR/state"
LOG_DIR="$CD_DIR/log"
LOCK_FILE="$STATE_DIR/deploy.lock"
ENV_FILE="$DEPLOY_DIR/.env"
ENV_PREV="$DEPLOY_DIR/.env.prev"
OFFSITE_HOOK="$CD_DIR/offsite-backup.hook"

STAGING="${DAAS_CD_STAGING:-0}"
API_REPO="${DAAS_CD_API_REPO:-ghcr.io/emsior/daas-engine-api}"
WEB_REPO="${DAAS_CD_WEB_REPO:-ghcr.io/emsior/daas-engine-web}"
DRAIN_TIMEOUT="${DAAS_CD_DRAIN_TIMEOUT:-600}"
MIN_FREE_KB=$((3 * 1024 * 1024)) # 3 GB wolnego miejsca na obrazy i kopie
OUTER_TIMEOUT=$((50 * 60))       # jak dlugo `deploy` czeka na wynik operacji odlaczonej

# Stan biezacej operacji (zapisywany do pliku stanu przez state_write)
ACTION="" RUN_ID="" REQUEST="" VERSION="-" PREV_VERSION="none" PHASE="start"
SCHEMA="unknown" RESULT="failed" CODE="2" START_EPOCH="$(now_epoch)"
API_DIGEST="" WEB_DIGEST="" SHA="" ALLOW="0" FORCE="0"
API_PORT="8000" WEB_PORT="3000"
PROFILE_ARGS=()
PUBLIC_URL=""
DB_REV="" NEW_HEAD="" PREV_COMMIT="" BACKUP_ARCHIVE=""
ENV_SWAPPED=0 CHECKED_OUT=0 PREV_SAVED=0 FIRST_DEPLOY=0 FINALIZED=0
RUNNING_VERSION="" KNOWN_REVS_FILE="" MAINT_STATE="off"

# ---------------------------------------------------------------- stan i podsumowanie

state_write() { # state_write 0|1  (1 = operacja zakonczona)
  local done_flag="$1" tmp
  tmp="$(umask 077 && mktemp "$STATE_DIR/.state.XXXXXX")"
  {
    printf '{\n'
    printf '  "run": "%s",\n' "$RUN_ID"
    printf '  "action": "%s",\n' "$ACTION"
    printf '  "request": "%s",\n' "$REQUEST"
    printf '  "version": "%s",\n' "$VERSION"
    printf '  "previous": "%s",\n' "$PREV_VERSION"
    printf '  "phase": "%s",\n' "$PHASE"
    printf '  "schema": "%s",\n' "$SCHEMA"
    printf '  "maintenance": "%s",\n' "$MAINT_STATE"
    printf '  "result": "%s",\n' "$RESULT"
    printf '  "code": "%s",\n' "$CODE"
    printf '  "started": "%s",\n' "$START_EPOCH"
    printf '  "updated": "%s",\n' "$(now_epoch)"
    printf '  "done": "%s"\n' "$done_flag"
    printf '}\n'
  } >"$tmp"
  mv -f "$tmp" "$STATE_DIR/$RUN_ID.json"
}

# state_get PLIK KLUCZ -> wartosc (pole tekstowe w jednej linii)
state_get() { sed -n "s/^  \"$2\": \"\\(.*\\)\",\\{0,1\\}\$/\\1/p" "$1" | head -n 1; }

set_phase() {
  PHASE="$1"
  state_write 0
  log "faza: $PHASE"
}

summary_line() { # result code version previous schema duration [maintenance]
  printf 'DAAS_DEPLOY result=%s code=%s version=%s previous=%s schema=%s duration=%s maintenance=%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "${7:-off}"
}

# last_state -> sciezka najnowszego pliku stanu (nazwa = czas UTC, wiec sortowanie leksykograficzne)
last_state() { { find "$STATE_DIR" -maxdepth 1 -type f -name '2*.json' 2>/dev/null || true; } | sort | tail -n 1; }

lock_is_held() { (exec 8>"$LOCK_FILE" && ! flock -n 8) 2>/dev/null; }

# ---------------------------------------------------------------- narzedzia hosta

compose() { (cd "$DEPLOY_DIR" && docker compose "${PROFILE_ARGS[@]}" "$@"); }

current_version() { # wersja z dzialajacego API; pusta, gdy API nie odpowiada
  local out
  out="$(curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/api/v1/health" 2>/dev/null |
    sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p' | head -n 1)" || true
  printf '%s' "$out"
}

load_env_settings() {
  local v
  v="$(env_get "$ENV_FILE" DAAS_API_PORT)"
  [[ "$v" =~ ^[0-9]{2,5}$ ]] && API_PORT="$v"
  v="$(env_get "$ENV_FILE" DAAS_WEB_PORT)"
  [[ "$v" =~ ^[0-9]{2,5}$ ]] && WEB_PORT="$v"
  # Profil `edge` (Caddy) domyslnie wlaczony; host ze wspolnym proxy wpisuje do .env COMPOSE_PROFILES= (nawet puste).
  if env_has "$ENV_FILE" COMPOSE_PROFILES; then PROFILE_ARGS=(); else PROFILE_ARGS=(--profile edge); fi
  PUBLIC_URL="${DAAS_CD_PUBLIC_URL:-}"
  if [ -z "$PUBLIC_URL" ] && [ "${#PROFILE_ARGS[@]}" -gt 0 ]; then
    v="$(env_get "$ENV_FILE" DAAS_DOMAIN)"
    if [[ "$v" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] && [ "$v" != "localhost" ]; then PUBLIC_URL="https://$v"; fi
  fi
  return 0
}

run_smoke() { # run_smoke X.Y.Z  -> kod smoke.sh (lokalnie, a przy PUBLIC_URL takze przez proxy)
  local -a args=(--api "http://127.0.0.1:${API_PORT}" --web "http://127.0.0.1:${WEB_PORT}" --wait 90 --expect-version "$1")
  [ -z "$PUBLIC_URL" ] || args+=(--public "$PUBLIC_URL")
  (cd "$DEPLOY_DIR" && ./smoke.sh "${args[@]}")
}

run_smoke_local() { # tylko API i kokpit na 127.0.0.1 (bez --public): rozroznia awarie aplikacji od awarii krawedzi
  (cd "$DEPLOY_DIR" && ./smoke.sh --api "http://127.0.0.1:${API_PORT}" --web "http://127.0.0.1:${WEB_PORT}" --wait 30)
}

# Tryb serwisowy (DAAS_MAINTENANCE_MODE): atomowa zmiana .env, odtworzenie TYLKO usługi api, potwierdzenie w /api/v1/health.
# Tryb wstrzymuje executor, harmonogram, retencje i powiadomienia, a uruchomienia zadan, zaproszenia, linki logowania
# i webhooki dostaja 503 `maintenance`; /ready i odczyt raportow dzialaja dalej (nie oczekuj czerwonego /ready).
do_maintenance() { # do_maintenance on|off
  local want="$1" value h
  if [ "$want" = "on" ]; then value="true"; else value="false"; fi
  env_set_many "$ENV_FILE" "DAAS_MAINTENANCE_MODE=$value" || { log "nie udalo sie zapisac .env"; return 1; }
  (cd "$DEPLOY_DIR" && timeout 300 docker compose "${PROFILE_ARGS[@]}" up -d --no-build --pull never --no-deps --force-recreate api) ||
    { log "docker compose up api (tryb serwisowy) nie powiodl sie"; return 1; }
  for _ in $(seq 1 30); do
    h="$(curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/api/v1/health" 2>/dev/null |
      sed -n 's/.*"maintenance" *: *\(true\|false\).*/\1/p' | head -n 1)" || true
    if [ "$h" = "$value" ]; then
      log "tryb serwisowy: $want (potwierdzone w /api/v1/health)"
      return 0
    fi
    sleep 2
  done
  log "tryb serwisowy: /api/v1/health nie potwierdzil stanu '$want'"
  return 1
}

# Dowody incydentu do katalogu 0700 na hoscie (logi kontenerow moga zawierac adresy klientow: nigdy do GitHub).
capture_incident() {
  local dir="$LOG_DIR/${RUN_ID}-incident"
  mkdir -p "$dir" && chmod 700 "$dir" || return 1
  compose logs --no-color --timestamps api web >"$dir/logs.txt" 2>&1 || true
  compose ps >"$dir/ps.txt" 2>&1 || true
  chmod 600 "$dir"/* 2>/dev/null || true
  log "zapisano dowody incydentu: $dir"
}

# ---------------------------------------------------------------- brama schematu (ADR-005)

# new_head z kodu wydania: z obiektu gita (bez checkoutu) albo, w stagingu, z katalogu roboczego.
compute_new_head() {
  local pairs="" f
  if [ "$STAGING" = "1" ]; then
    for f in "$REPO_DIR"/alembic/versions/*.py; do
      [ -f "$f" ] || continue
      pairs+="$(rev_pairs_from_text <"$f")"$'\n'
    done
  else
    while IFS= read -r f; do
      [[ "$f" == alembic/versions/*.py ]] || continue
      pairs+="$(git -C "$REPO_DIR" show "$SHA:$f" | rev_pairs_from_text)"$'\n'
    done < <(git -C "$REPO_DIR" ls-tree -r --name-only "$SHA" -- alembic/versions)
  fi
  KNOWN_REVS_FILE="$(umask 077 && mktemp "$STATE_DIR/.known.XXXXXX")"
  NEW_HEAD="$(printf '%s' "$pairs" | ALEMBIC_KNOWN_FILE="$KNOWN_REVS_FILE" alembic_head_from_pairs)" || return 1
  if [ "$STAGING" = "1" ] && [ -n "${DAAS_CD_FAKE_NEW_HEAD:-}" ]; then
    NEW_HEAD="$DAAS_CD_FAKE_NEW_HEAD" # tylko staging: symulacja nowej migracji
    printf '%s\n' "$NEW_HEAD" >>"$KNOWN_REVS_FILE"
  fi
  [[ "$NEW_HEAD" =~ ^[0-9]+$ ]]
}

# Skrypt odczytu rewizji z control.db. Kopia pliku (+ WAL) do katalogu tymczasowego kontenera: nigdy nie otwieramy
# zywej bazy (tryb WAL utworzylby pliki -shm nalezace do roota w wolumenie aplikacji). Kilka prob na wypadek kopii w trakcie zapisu.
read -r -d '' PY_DBREV <<'PYEOF' || true
import os, shutil, sqlite3, sys, tempfile
src = "/data/control/control.db"
if not os.path.exists(src):
    print("REV=NONE")
    sys.exit(0)
result = "ERROR"
for _ in range(3):
    d = tempfile.mkdtemp()
    try:
        shutil.copyfile(src, d + "/c.db")
        if os.path.exists(src + "-wal"):
            shutil.copyfile(src + "-wal", d + "/c.db-wal")
        con = sqlite3.connect(d + "/c.db")
        try:
            rows = con.execute("select version_num from alembic_version").fetchall()
        finally:
            con.close()
        result = "REV=" + rows[0][0] if len(rows) == 1 else "REV=UNKNOWN"
        break
    except sqlite3.OperationalError as exc:
        result = "REV=EMPTY" if "no such table" in str(exc) else "ERROR"
        if result == "REV=EMPTY":
            break
    except Exception:
        result = "ERROR"
print(result)
PYEOF

read_db_rev() { # wypisuje NONE | EMPTY | UNKNOWN | ERROR | <rewizja>; obraz maint = NOWY obraz (jest pobrany i otagowany)
  local out
  out="$(cd "$DEPLOY_DIR" && DAAS_API_IMAGE="$API_REPO" DAAS_VERSION="$VERSION" \
    docker compose run --rm -T --no-deps --pull never --entrypoint python maint -c "$PY_DBREV" 2>/dev/null |
    sed -n 's/^REV=//p' | tail -n 1)" || true
  printf '%s' "${out:-ERROR}"
}

# gate_decision: ustawia SCHEMA (same|changed|unknown) i zwraca 0 (dozwolone) albo 1 (blokada, kod 2)
gate_decision() {
  DB_REV="$(read_db_rev)"
  log "brama schematu: db_rev=$DB_REV new_head=$NEW_HEAD allow=$ALLOW"
  case "$DB_REV" in
    NONE)
      # Brak control.db = pierwsze wdrozenie na pustych wolumenach. Jesli juz wdrazalismy, to anomalia (zly wolumen).
      if [ -f "$STATE_DIR/current.json" ]; then
        SCHEMA="unknown"
        log "BLOKADA: brak control.db, choc wczesniej wdrazalismy (zly wolumen?)"
        return 1
      fi
      SCHEMA="changed"
      FIRST_DEPLOY=1
      return 0
      ;;
    ERROR | EMPTY | UNKNOWN)
      SCHEMA="unknown"
      log "BLOKADA: nie da sie ustalic rewizji bazy ($DB_REV)"
      return 1
      ;;
  esac
  if ! [[ "$DB_REV" =~ ^[0-9]+$ ]] || ! grep -qx -- "$DB_REV" "$KNOWN_REVS_FILE"; then
    SCHEMA="unknown"
    log "BLOKADA: rewizja bazy nieznana kodowi wydania (rozjazd historii)"
    return 1
  fi
  if ((10#$NEW_HEAD < 10#$DB_REV)); then
    SCHEMA="unknown"
    log "BLOKADA: wydanie ma starszy schemat niz baza (downgrade)"
    return 1
  elif ((10#$NEW_HEAD == 10#$DB_REV)); then
    SCHEMA="same"
    return 0
  fi
  SCHEMA="changed"
  if [ "$ALLOW" != "1" ]; then
    log "BLOKADA: wydanie zmienia schemat ($DB_REV -> $NEW_HEAD); wymagane allow_schema_change=1"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------- kroki wdrozenia

pf_env() {
  [ -f "$ENV_FILE" ] || { log ".env nie istnieje (uruchom deploy/gen-env.sh)"; return 1; }
  [ "$(stat -c %a "$ENV_FILE")" = "600" ] || { log ".env ma prawa inne niz 0600"; return 1; }
  [ "$(stat -c %u "$ENV_FILE")" = "$(id -u)" ] || { log ".env nalezy do innego uzytkownika"; return 1; }
}

pf_disk() {
  local root free dir
  root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
  [ -d "${root:-/nonexistent}" ] || root="$DEPLOY_DIR"
  for dir in "$root" "$DEPLOY_DIR"; do
    free="$(df -Pk "$dir" 2>/dev/null | awk 'NR == 2 { print $4 }')"
    [[ "${free:-}" =~ ^[0-9]+$ ]] || { log "nie da sie odczytac wolnego miejsca"; return 1; }
    if [ "$free" -lt "$MIN_FREE_KB" ]; then
      log "za malo wolnego miejsca: ${free} KB (wymagane >= ${MIN_FREE_KB} KB)"
      return 1
    fi
  done
}

pf_git() {
  [ "$STAGING" = "1" ] && return 0
  local tag_commit
  if [ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]; then
    log "katalog repozytorium na hoscie ma niezatwierdzone zmiany"
    return 1
  fi
  PREV_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
  timeout 120 git -C "$REPO_DIR" fetch --quiet --tags --force origin || { log "git fetch nie powiodl sie"; return 1; }
  tag_commit="$(git -C "$REPO_DIR" rev-parse --verify --quiet "refs/tags/v${VERSION}^{commit}")" || { log "brak tagu v$VERSION"; return 1; }
  if [ "$tag_commit" != "$SHA" ]; then
    log "commit tagu v$VERSION nie zgadza sie z przekazanym SHA"
    return 1
  fi
}

pull_verify() { # repo digest
  local repo="$1" digest="$2"
  if [ "$digest" = "local" ]; then # tylko staging: obraz zbudowany lokalnie i otagowany
    docker image inspect "$repo:$VERSION" >/dev/null 2>&1
    return
  fi
  timeout 900 docker pull --quiet "$repo@$digest" >/dev/null || { log "docker pull nie powiodl sie: $repo"; return 1; }
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$repo@$digest" 2>/dev/null |
    grep -Fxq -- "$repo@$digest" || { log "RepoDigest nie zgadza sie z digestem wydania: $repo"; return 1; }
  docker tag "$repo@$digest" "$repo:$VERSION"
}

pf_images() {
  pull_verify "$API_REPO" "$API_DIGEST" && pull_verify "$WEB_REPO" "$WEB_DIGEST"
}

# Poprzednia wersja nie moze byc nowsza od wdrazanej (downgrade tylko przez `rollback`).
version_not_older() { # running new
  [ -z "$1" ] && return 0
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1)" = "$1" ]
}

drain_jobs() {
  [ -n "$RUNNING_VERSION" ] || return 0 # API nie dziala: nie ma zadan w locie
  local token hdr deadline body running
  token="$(env_get "$ENV_FILE" DAAS_METRICS_TOKEN)"
  if [ -z "$token" ]; then
    log "brak DAAS_METRICS_TOKEN: nie da sie odczytac zadan w toku"
    [ "$FORCE" = "1" ] && return 0
    return 1
  fi
  # Token tylko w pliku naglowka 0600 (curl -H @plik): nie trafia do argumentow procesow (ps).
  hdr="$(umask 077 && mktemp "$STATE_DIR/.hdr.XXXXXX")"
  printf 'Authorization: Bearer %s\n' "$token" >"$hdr"
  token=""
  deadline=$((SECONDS + DRAIN_TIMEOUT))
  while :; do
    if ! body="$(curl -fsS --max-time 10 -H "@$hdr" "http://127.0.0.1:${API_PORT}/metrics" 2>/dev/null)"; then
      rm -f "$hdr"
      log "nie da sie odczytac /metrics"
      [ "$FORCE" = "1" ] && return 0
      return 1
    fi
    running="$(printf '%s\n' "$body" | sed -n 's/^daas_jobs{status="running"} \([0-9][0-9]*\).*/\1/p' | head -n 1)"
    if ! [[ "${running:-}" =~ ^[0-9]+$ ]]; then
      rm -f "$hdr"
      log "w /metrics brak daas_jobs{status=\"running\"}"
      [ "$FORCE" = "1" ] && return 0
      return 1
    fi
    if [ "$running" = "0" ]; then
      rm -f "$hdr"
      log "drenaz: brak zadan w toku"
      return 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      rm -f "$hdr"
      log "drenaz: po ${DRAIN_TIMEOUT}s nadal $running zadan w toku"
      [ "$FORCE" = "1" ] && { log "--force: kontynuuje, zadania w locie zakoncza sie jako failed/abandoned"; return 0; }
      return 1
    fi
    sleep 10
  done
}

do_backup() { # kopia zimna + opcjonalny hak poza serwerem; wymagany przy allow_schema_change=1
  local out
  if [ "$FIRST_DEPLOY" = "1" ]; then
    log "pierwsze wdrozenie: brak danych do skopiowania"
    BACKUP_ARCHIVE=""
    return 0
  fi
  out="$(cd "$DEPLOY_DIR" && ./backup.sh 2>&1)" || { log "backup.sh nie powiodl sie"; printf '%s\n' "$out"; return 1; }
  BACKUP_ARCHIVE="$(printf '%s\n' "$out" | sed -n 's/^BACKUP_ARCHIVE=//p' | tail -n 1)"
  [ -f "$BACKUP_ARCHIVE" ] || { log "backup.sh nie zwrocil archiwum"; return 1; }
  (cd "$(dirname "$BACKUP_ARCHIVE")" && sha256sum -c --status "$(basename "$BACKUP_ARCHIVE").sha256") || { log "suma SHA-256 kopii nie zgadza sie"; return 1; }
  log "kopia lokalna gotowa: $(basename "$BACKUP_ARCHIVE")"
}

run_offsite_hook() {
  if [ -x "$OFFSITE_HOOK" ]; then
    if [ -z "$BACKUP_ARCHIVE" ]; then return 0; fi
    log "hak kopii poza serwerem: uruchamiam"
    "$OFFSITE_HOOK" "$BACKUP_ARCHIVE" || { log "hak kopii poza serwerem zakonczyl sie bledem"; return 1; }
    log "hak kopii poza serwerem: OK"
  elif [ "$ALLOW" = "1" ] && [ "$FIRST_DEPLOY" != "1" ]; then
    log "BLOKADA: allow_schema_change=1 wymaga haka deploy/cd/offsite-backup.hook (kopia poza serwerem)"
    return 1
  else
    log "brak haka kopii poza serwerem (wymagany tylko przy zmianie schematu)"
  fi
}

pull_aux_images() { # obrazy pomocnicze z compose (np. Caddy): `up --pull never` ich nie pobierze
  local images img
  images="$(cd "$DEPLOY_DIR" && DAAS_API_IMAGE="$API_REPO" DAAS_VERSION="$VERSION" docker compose "${PROFILE_ARGS[@]}" config --images 2>/dev/null)" || {
    log "docker compose config --images nie powiodl sie"
    return 1
  }
  while IFS= read -r img; do
    [ -n "$img" ] || continue
    [ "$img" = "$API_REPO:$VERSION" ] || [ "$img" = "$WEB_REPO:$VERSION" ] && continue
    if ! timeout 600 docker pull --quiet "$img" >/dev/null 2>&1; then
      if docker image inspect "$img" >/dev/null 2>&1; then
        log "ostrzezenie: nie pobrano $img, uzywam obrazu lokalnego"
      else
        log "brak obrazu pomocniczego $img i pobranie nie powiodlo sie"
        return 1
      fi
    fi
  done <<<"$images"
}

do_apply() {
  local prev_env_version
  if [ "$STAGING" != "1" ]; then
    git -C "$REPO_DIR" checkout --quiet --detach "$SHA" || return 1
    CHECKED_OUT=1
  fi
  pull_aux_images || return 1
  prev_env_version="$(env_get "$ENV_FILE" DAAS_VERSION)"
  if [ "$prev_env_version" != "$VERSION" ]; then
    (umask 077 && cp -p "$ENV_FILE" "$ENV_PREV.tmp") && chmod 600 "$ENV_PREV.tmp" && mv -f "$ENV_PREV.tmp" "$ENV_PREV" || return 1
    PREV_SAVED=1
  fi
  env_set_many "$ENV_FILE" "DAAS_VERSION=$VERSION" "DAAS_API_IMAGE=$API_REPO" "DAAS_WEB_IMAGE=$WEB_REPO" || return 1
  ENV_SWAPPED=1
}

revert_apply() { # przed `up`: przywroc kod i .env (nic nie dziala jeszcze na nowej wersji)
  if [ "$ENV_SWAPPED" = "1" ] && [ "$PREV_SAVED" = "1" ]; then
    (umask 077 && cp -p "$ENV_PREV" "$ENV_FILE.tmp") && chmod 600 "$ENV_FILE.tmp" && mv -f "$ENV_FILE.tmp" "$ENV_FILE" || log "UWAGA: nie udalo sie przywrocic .env"
  fi
  if [ "$CHECKED_OUT" = "1" ] && [ -n "$PREV_COMMIT" ]; then
    git -C "$REPO_DIR" checkout --quiet --detach "$PREV_COMMIT" || log "UWAGA: nie udalo sie przywrocic kodu"
  fi
}

compose_up() { (cd "$DEPLOY_DIR" && timeout 600 docker compose "${PROFILE_ARGS[@]}" up -d --no-build --pull never); }

# Rollback wariant A: .env.prev + kod poprzedniego commitu + ponowny smoke z wersja poprzednia. 0 = udany.
rollback_image() { # rollback_image <commit-poprzedni|"">
  local target_commit="$1" prev_ver
  [ -f "$ENV_PREV" ] || { log "brak .env.prev: nie ma do czego wracac"; return 1; }
  prev_ver="$(env_get "$ENV_PREV" DAAS_VERSION)"
  valid_version "$prev_ver" || { log ".env.prev nie zawiera poprawnej wersji"; return 1; }
  (umask 077 && cp -p "$ENV_PREV" "$ENV_FILE.tmp") && chmod 600 "$ENV_FILE.tmp" && mv -f "$ENV_FILE.tmp" "$ENV_FILE" || return 1
  if [ "$STAGING" != "1" ] && [ -n "$target_commit" ]; then
    git -C "$REPO_DIR" checkout --quiet --detach "$target_commit" || return 1
  fi
  load_env_settings
  compose_up || { log "docker compose up (rollback) nie powiodl sie"; return 1; }
  run_smoke "$prev_ver" || { log "smoke po rollbacku nie przeszedl"; return 1; }
}

prune_old_images() { # zostaw tagi N, N-1, N-2 (po tagu; nigdy `prune -a`)
  local repo tags tag n
  for repo in "$API_REPO" "$WEB_REPO"; do
    tags="$(docker image ls --format '{{.Tag}}' "$repo" 2>/dev/null | grep -E "$RE_VERSION" | sort -uV -r || true)"
    n=0
    while IFS= read -r tag; do
      [ -n "$tag" ] || continue
      n=$((n + 1))
      [ "$n" -le 3 ] && continue
      [ "$tag" = "$VERSION" ] && continue
      docker image rm "$repo:$tag" >/dev/null 2>&1 || log "nie usunieto $repo:$tag (w uzyciu?)"
    done <<<"$tags"
  done
}

prune_logs() { # zostaw 30 ostatnich dziennikow i plikow stanu
  local f
  for f in $(find "$LOG_DIR" -maxdepth 1 -type f -name '2*.log' | sort | head -n -30); do rm -f "$f"; done
  for f in $(find "$STATE_DIR" -maxdepth 1 -type f -name '2*.json' | sort | head -n -30); do rm -f "$f"; done
  for f in $(find "$LOG_DIR" -maxdepth 1 -type d -name '2*-incident' | sort | head -n -10); do rm -rf "$f"; done
}

# ---------------------------------------------------------------- przebieg: deploy

record_success() { # current.json / previous.json (czytane przez rollback reczny)
  local tmp
  if [ "$PREV_SAVED" = "1" ] && valid_version "$PREV_VERSION"; then
    tmp="$(umask 077 && mktemp "$STATE_DIR/.prev.XXXXXX")"
    printf '{\n  "version": "%s",\n  "commit": "%s",\n  "schema_head": "%s"\n}\n' "$PREV_VERSION" "$PREV_COMMIT" "$DB_REV" >"$tmp"
    mv -f "$tmp" "$STATE_DIR/previous.json"
  fi
  tmp="$(umask 077 && mktemp "$STATE_DIR/.cur.XXXXXX")"
  printf '{\n  "version": "%s",\n  "commit": "%s",\n  "api_digest": "%s",\n  "web_digest": "%s",\n  "schema_head": "%s"\n}\n' \
    "$VERSION" "$SHA" "$API_DIGEST" "$WEB_DIGEST" "$NEW_HEAD" >"$tmp"
  mv -f "$tmp" "$STATE_DIR/current.json"
}

abort_pre() { # abort_pre blocked|failed "komunikat"  -> kod 2, nic nie zmieniono
  RESULT="$1"
  CODE=2
  log "KONIEC ($PHASE): $2"
}

run_deploy() {
  local cur_json="$STATE_DIR/current.json" cur_ver cur_api cur_web cur_sha

  set_phase preflight
  pf_env || { abort_pre failed "preflight .env"; return 0; }
  load_env_settings
  pf_disk || { abort_pre failed "preflight dysk"; return 0; }
  RUNNING_VERSION="$(current_version)"
  [ -z "$RUNNING_VERSION" ] || PREV_VERSION="$RUNNING_VERSION"
  if [ "$PREV_VERSION" = "none" ] && [ -f "$cur_json" ]; then
    PREV_VERSION="$(state_get_flat "$cur_json" version)"
  fi

  # Idempotencja: ta sama wersja juz stoi.
  if [ "$RUNNING_VERSION" = "$VERSION" ]; then
    cur_ver="$(state_get_flat "$cur_json" version 2>/dev/null || true)"
    cur_api="$(state_get_flat "$cur_json" api_digest 2>/dev/null || true)"
    cur_web="$(state_get_flat "$cur_json" web_digest 2>/dev/null || true)"
    cur_sha="$(state_get_flat "$cur_json" commit 2>/dev/null || true)"
    if [ "$cur_ver" != "$VERSION" ] || [ "$cur_api" != "$API_DIGEST" ] || [ "$cur_web" != "$WEB_DIGEST" ] ||
      { [ "$STAGING" != "1" ] && [ "$cur_sha" != "$SHA" ]; }; then
      abort_pre blocked "wersja $VERSION juz stoi z innym artefaktem (zakaz nadpisania wersji; wydaj nowa)"
      return 0
    fi
    if run_smoke "$VERSION" >/dev/null 2>&1; then
      RESULT="noop"
      CODE=0
      SCHEMA="same"
      log "wersja $VERSION juz wdrozona i smoke zielony: bez zmian"
      return 0
    fi
    log "wersja $VERSION stoi, ale smoke jest czerwony: ponawiam wdrozenie (naprawa)"
  fi
  version_not_older "$RUNNING_VERSION" "$VERSION" || { abort_pre blocked "wersja $VERSION jest starsza niz dzialajaca $RUNNING_VERSION (uzyj rollback)"; return 0; }

  pf_git || { abort_pre failed "preflight git"; return 0; }
  pf_images || { abort_pre failed "preflight obrazy"; return 0; }

  set_phase gate
  compute_new_head || { SCHEMA="unknown"; abort_pre blocked "nie da sie ustalic new_head z alembic/versions"; return 0; }
  gate_decision || { abort_pre blocked "brama schematu"; return 0; }

  set_phase drain
  drain_jobs || { abort_pre blocked "drenaz zadan (uzyj --force tylko swiadomie)"; return 0; }

  set_phase backup
  do_backup || { abort_pre failed "kopia zapasowa"; return 0; }
  run_offsite_hook || { abort_pre blocked "kopia poza serwerem"; return 0; }

  set_phase apply
  if ! do_apply; then
    revert_apply
    abort_pre failed "przygotowanie wersji (checkout/obrazy/.env); przywrocono poprzedni stan"
    return 0
  fi

  set_phase up
  local up_ok=0 smoke_ok=0
  if compose_up; then up_ok=1; else log "docker compose up nie powiodl sie"; fi
  if [ "$up_ok" = "1" ]; then
    set_phase smoke
    if run_smoke "$VERSION"; then smoke_ok=1; fi
  fi

  if [ "$smoke_ok" = "1" ]; then
    RESULT="ok"
    CODE=0
    record_success
    set_phase cleanup
    prune_old_images
    return 0
  fi

  # --- porazka wersji N ---
  log "wersja $VERSION nie przeszla smoke"
  if [ "$SCHEMA" = "changed" ]; then
    RESULT="failed"
    CODE=11
    log "INCYDENT: rollback obrazu zabroniony (schemat zmieniony lub pierwsze wdrozenie). RUNBOOK §9, README wariant B."
    # Q10: czerwony LOKALNY smoke = awaria aplikacji -> dowody + tryb serwisowy; czerwony tylko --public = awaria krawedzi -> bez trybu.
    if run_smoke_local; then
      log "lokalny smoke zielony: awaria dotyczy krawedzi (--public), tryb serwisowy bez zmian"
    else
      capture_incident || log "UWAGA: nie zapisano dowodow incydentu"
      if do_maintenance on; then MAINT_STATE="on"; else MAINT_STATE="unverified"; fi
    fi
    return 0
  fi
  set_phase rollback
  if rollback_image "$PREV_COMMIT"; then
    RESULT="rollback"
    CODE=10
    rm -f "$STATE_DIR/previous.json" "$ENV_PREV" # nie ma juz dalszego cofania; kolejny deploy odtworzy oba pliki
    log "rollback do $(env_get "$ENV_FILE" DAAS_VERSION) udany"
  else
    RESULT="failed"
    CODE=12
    log "INCYDENT P1: rollback nieudany"
  fi
  return 0
}

# state_get_flat PLIK KLUCZ: pola current.json/previous.json (wciecie 2 spacje, bez przecinka na ostatniej pozycji)
state_get_flat() { [ -f "$1" ] && sed -n "s/^  \"$2\": \"\\(.*\\)\",\\{0,1\\}\$/\\1/p" "$1" | head -n 1; }

# ---------------------------------------------------------------- przebieg: rollback reczny

run_rollback() {
  local prev_json="$STATE_DIR/previous.json" prev_ver prev_commit prev_head
  set_phase preflight
  pf_env || { abort_pre failed "preflight .env"; return 0; }
  load_env_settings
  pf_disk || { abort_pre failed "preflight dysk"; return 0; }
  RUNNING_VERSION="$(current_version)"
  [ -z "$RUNNING_VERSION" ] || PREV_VERSION="$RUNNING_VERSION"
  [ -f "$prev_json" ] && [ -f "$ENV_PREV" ] || { abort_pre failed "brak danych poprzedniej wersji (previous.json/.env.prev)"; return 0; }
  prev_ver="$(state_get_flat "$prev_json" version)"
  prev_commit="$(state_get_flat "$prev_json" commit)"
  prev_head="$(state_get_flat "$prev_json" schema_head)"
  valid_version "$prev_ver" || { abort_pre failed "previous.json: niepoprawna wersja"; return 0; }
  [ "$(env_get "$ENV_PREV" DAAS_VERSION)" = "$prev_ver" ] || { abort_pre failed ".env.prev nie zgadza sie z previous.json"; return 0; }
  VERSION="$prev_ver"
  if [ "$STAGING" != "1" ]; then
    valid_sha "$prev_commit" || { abort_pre failed "previous.json: niepoprawny commit"; return 0; }
    [ -z "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ] || { abort_pre failed "repozytorium z niezatwierdzonymi zmianami"; return 0; }
  fi

  set_phase gate
  DB_REV="$(read_db_rev)"
  if ! [[ "$DB_REV" =~ ^[0-9]+$ ]] || ! [[ "$prev_head" =~ ^[0-9]+$ ]]; then
    SCHEMA="unknown"
    abort_pre blocked "nie da sie porownac schematu (db_rev=$DB_REV, poprzednia=$prev_head)"
    return 0
  fi
  if ((10#$DB_REV != 10#$prev_head)); then
    SCHEMA="changed"
    abort_pre blocked "schemat bazy ($DB_REV) rozni sie od schematu poprzedniej wersji ($prev_head): rollback obrazu zabroniony, wariant B"
    return 0
  fi
  SCHEMA="same"

  set_phase drain
  drain_jobs || { abort_pre blocked "drenaz zadan"; return 0; }
  set_phase backup
  FIRST_DEPLOY=0
  do_backup || { abort_pre failed "kopia zapasowa"; return 0; }

  set_phase rollback
  if rollback_image "$prev_commit"; then
    RESULT="rollback"
    CODE=0
    cp -p "$prev_json" "$STATE_DIR/current.json"
    rm -f "$prev_json" "$ENV_PREV"
    log "rollback reczny do $prev_ver udany"
  else
    RESULT="failed"
    CODE=12
    log "INCYDENT P1: rollback reczny nieudany"
  fi
}

# ---------------------------------------------------------------- proces roboczy (odlaczony)

# Niespodziewane zakonczenie procesu roboczego (blad powloki, SIGTERM): domknij plik stanu, zeby `status` nie klamal.
worker_exit() {
  [ "$FINALIZED" = "1" ] && return 0
  RESULT="failed"
  case "$PHASE" in
    start | lock | preflight | gate | drain | backup) CODE=2 ;;
    *) CODE=12 ;;
  esac
  log "NIEOCZEKIWANE zakonczenie w fazie $PHASE"
  state_write 1 || true
}

worker() { # worker deploy|rollback RUN_ID [argumenty...]
  ACTION="$1"
  RUN_ID="$2"
  shift 2
  START_EPOCH="$(now_epoch)"
  umask 077
  mkdir -p "$STATE_DIR" "$LOG_DIR"
  exec >>"$LOG_DIR/$RUN_ID.log" 2>&1
  if [ "$ACTION" = "deploy" ]; then
    VERSION="$1" API_DIGEST="$2" WEB_DIGEST="$3" SHA="$4" ALLOW="$5"
    shift 5
    REQUEST="$VERSION|$SHA|$API_DIGEST|$WEB_DIGEST"
  else
    REQUEST="rollback"
  fi
  [ "${1:-}" != "--force" ] || FORCE=1
  log "start: akcja=$ACTION run=$RUN_ID staging=$STAGING"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    PHASE="lock"
    abort_pre blocked "inna operacja wdrozeniowa trwa"
    state_write 1
    return 0
  fi
  trap worker_exit EXIT
  state_write 0
  if [ "$ACTION" = "deploy" ]; then run_deploy || true; else run_rollback || true; fi
  PHASE="done"
  FINALIZED=1
  state_write 1
  [ -z "${KNOWN_REVS_FILE:-}" ] || rm -f "$KNOWN_REVS_FILE"
  prune_logs
  log "koniec: result=$RESULT code=$CODE schema=$SCHEMA"
}

# ---------------------------------------------------------------- warstwa zewnetrzna (wywolywana przez SSH)

launch_detached() { # launch_detached RUN_ID akcja [argumenty...]
  local run_id="$1" var
  shift
  local -a setenv=()
  for var in DAAS_CD_STAGING DAAS_CD_FAKE_NEW_HEAD DAAS_CD_API_REPO DAAS_CD_WEB_REPO DAAS_CD_PUBLIC_URL DAAS_CD_DRAIN_TIMEOUT CURL_CA_BUNDLE; do
    [ -z "${!var:-}" ] || setenv+=("--setenv=$var=${!var}")
  done
  if command -v systemd-run >/dev/null 2>&1 &&
    systemd-run --user --quiet --collect --unit "daas-deploy-$run_id" "${setenv[@]}" -- "$SELF" __worker "$@" "$run_id" >/dev/null 2>&1; then
    return 0
  fi
  setsid nohup "$SELF" __worker "$@" "$run_id" >/dev/null 2>&1 </dev/null &
}

wait_for_run() { # wait_for_run RUN_ID  -> drukuje podsumowanie i zwraca jego kod
  local run_id="$1" file deadline=$((SECONDS + OUTER_TIMEOUT)) started=$SECONDS
  file="$STATE_DIR/$run_id.json"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ -f "$file" ] && [ "$(state_get "$file" "done")" = "1" ]; then
      print_final "$file"
      return $?
    fi
    # proces roboczy zniknal bez zapisu wyniku (np. zabity): nie wiemy, co sie stalo
    if [ $((SECONDS - started)) -gt 15 ] && ! lock_is_held && { [ ! -f "$file" ] || [ "$(state_get "$file" "done")" != "1" ]; }; then
      local phase="start"
      [ ! -f "$file" ] || phase="$(state_get "$file" phase)"
      case "$phase" in
        start | lock | preflight | gate | drain | backup) summary_line failed 2 "$VERSION" "$PREV_VERSION" unknown $((SECONDS - started)); return 2 ;;
        *) summary_line failed 12 "$VERSION" "$PREV_VERSION" unknown $((SECONDS - started)); return 12 ;;
      esac
    fi
    sleep 3
  done
  summary_line failed 12 "$VERSION" "$PREV_VERSION" unknown $((SECONDS - started))
  return 12
}

print_final() { # print_final PLIK_STANU -> podsumowanie + kod
  local f="$1" code
  code="$(state_get "$f" code)"
  summary_line "$(state_get "$f" result)" "$code" "$(state_get "$f" version)" "$(state_get "$f" previous)" \
    "$(state_get "$f" schema)" "$(($(state_get "$f" updated) - $(state_get "$f" started)))" "$(state_get "$f" maintenance)"
  return "$code"
}

new_run_id() { printf '%s-%s' "$(date -u +%Y%m%dT%H%M%SZ)" "$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"; }

# Argumenty `deploy` (te same wzorce co w ssh-gate.sh); w stagingu dopuszczalny digest `local` i dowolny SHA.
deploy_args_ok() { # ver api web sha allow extra
  valid_version "$1" || return 1
  valid_digest "$2" || { [ "$STAGING" = "1" ] && [ "$2" = "local" ]; } || return 1
  valid_digest "$3" || { [ "$STAGING" = "1" ] && [ "$3" = "local" ]; } || return 1
  valid_sha "$4" || [ "$STAGING" = "1" ] || return 1
  [[ "$5" =~ ^[01]$ ]] || return 1
  [ -z "$6" ] || [ "$6" = "--force" ] || return 1
}

cmd_deploy() {
  local ver="${1:-}" api="${2:-}" web="${3:-}" sha="${4:-}" allow="${5:-}" extra="${6:-}"
  VERSION="${ver:--}"
  if ! deploy_args_ok "$ver" "$api" "$web" "$sha" "$allow" "$extra"; then
    summary_line failed 2 "-" none unknown 0
    return 2
  fi
  [[ "$sha" =~ $RE_SHA ]] || sha="0000000000000000000000000000000000000000"
  valid_repo "$API_REPO" && valid_repo "$WEB_REPO" || { summary_line failed 2 "$ver" none unknown 0; return 2; }
  [[ "$DRAIN_TIMEOUT" =~ ^[0-9]+$ ]] || { summary_line failed 2 "$ver" none unknown 0; return 2; }
  mkdir -p "$STATE_DIR" "$LOG_DIR"
  chmod 700 "$STATE_DIR" "$LOG_DIR" 2>/dev/null || true
  local request="$ver|$sha|$api|$web" last run_id
  # Ta sama operacja juz trwa (np. po zerwaniu SSH): dolacz zamiast uruchamiac druga.
  if lock_is_held; then
    last="$(last_state)"
    if [ -n "$last" ] && [ "$(state_get "$last" "done")" != "1" ] && [ "$(state_get "$last" request)" = "$request" ]; then
      wait_for_run "$(state_get "$last" run)"
      return $?
    fi
    summary_line blocked 2 "$ver" none unknown 0
    return 2
  fi
  run_id="$(new_run_id)"
  launch_detached "$run_id" deploy "$ver" "$api" "$web" "$sha" "$allow" ${extra:+"$extra"}
  wait_for_run "$run_id"
}

cmd_rollback() {
  [ -z "${1:-}" ] || [ "${1:-}" = "--force" ] || { summary_line failed 2 "-" none unknown 0; return 2; }
  mkdir -p "$STATE_DIR" "$LOG_DIR"
  chmod 700 "$STATE_DIR" "$LOG_DIR" 2>/dev/null || true
  if lock_is_held; then
    summary_line blocked 2 "-" none unknown 0
    return 2
  fi
  local run_id
  run_id="$(new_run_id)"
  launch_detached "$run_id" rollback ${1:+"$1"}
  wait_for_run "$run_id"
}

cmd_maintenance() {
  local want="${1:-}" logf
  if ! [[ "$want" =~ ^(on|off)$ ]]; then
    printf 'DAAS_MAINTENANCE result=failed code=2 maintenance=unverified\n'
    return 2
  fi
  trap '' HUP PIPE # zerwanie SSH w trakcie nie moze zostawic api w polowie odtwarzania
  umask 077
  mkdir -p "$STATE_DIR" "$LOG_DIR"
  exec 7>"$LOCK_FILE"
  if ! flock -n 7; then
    printf 'DAAS_MAINTENANCE result=blocked code=2 maintenance=unverified\n'
    return 2
  fi
  logf="$LOG_DIR/$(new_run_id)-maintenance.log"
  if { log "tryb serwisowy: zadanie '$want'" && pf_env && load_env_settings && do_maintenance "$want"; } >>"$logf" 2>&1; then
    printf 'DAAS_MAINTENANCE result=ok code=0 maintenance=%s\n' "$want"
    return 0
  fi
  printf 'DAAS_MAINTENANCE result=failed code=2 maintenance=unverified\n'
  return 2
}

cmd_status() {
  local last
  mkdir -p "$STATE_DIR"
  last="$(last_state)"
  if [ -z "$last" ]; then
    printf 'DAAS_STATUS state=none\n'
    return 4
  fi
  if [ "$(state_get "$last" "done")" = "1" ]; then
    printf 'DAAS_STATUS run=%s state=done action=%s\n' "$(state_get "$last" run)" "$(state_get "$last" action)"
    print_final "$last"
    return $?
  fi
  printf 'DAAS_STATUS run=%s state=running action=%s phase=%s\n' "$(state_get "$last" run)" "$(state_get "$last" action)" "$(state_get "$last" phase)"
  return 3
}

main() {
  local cmd="${1:-}"
  [ "$#" -eq 0 ] || shift
  case "$cmd" in
    deploy) cmd_deploy "$@" ;;
    rollback) cmd_rollback "$@" ;;
    status) cmd_status ;;
    maintenance) cmd_maintenance "$@" ;;
    __worker) # wewnetrzne: argumenty: akcja [argumenty akcji...] RUN_ID (RUN_ID zawsze ostatni)
      local -a all=("$@")
      local run_id="${all[$((${#all[@]} - 1))]}"
      local action="${all[0]}"
      local -a rest=("${all[@]:1:$((${#all[@]} - 2))}")
      worker "$action" "$run_id" "${rest[@]}"
      ;;
    -h | --help | help | "")
      sed -n '2,12p' "$SELF" | sed 's/^# \{0,1\}//'
      [ -n "$cmd" ] || return 2
      ;;
    *)
      printf 'Nieznane polecenie.\n' >&2
      return 2
      ;;
  esac
}

main "$@"
exit $?
