# shellcheck shell=bash
# Wspolne funkcje dla deploy/cd/deploy.sh. Plik jest wczytywany (source), nie uruchamiany.
# Zadnych sekretow: funkcje czytajace .env zwracaja pojedyncze wartosci, ktorych nie wypisujemy do dziennika.

# --- walidacja argumentow (te same wzorce co w ssh-gate.sh) ---
RE_VERSION='^[0-9]+\.[0-9]+\.[0-9]+$'
RE_DIGEST='^sha256:[0-9a-f]{64}$'
RE_SHA='^[0-9a-f]{40}$'
RE_REPO='^[a-z0-9][a-z0-9._/:-]*$'

valid_version() { [[ "${1:-}" =~ $RE_VERSION ]]; }
valid_digest() { [[ "${1:-}" =~ $RE_DIGEST ]]; }
valid_sha() { [[ "${1:-}" =~ $RE_SHA ]]; }
valid_repo() { [[ "${1:-}" =~ $RE_REPO ]]; }

# --- dziennik: do pliku dziennika hosta (stdout procesu roboczego), z czasem UTC ---
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# --- .env: odczyt bez `source` (wartosc jest danymi, nie kodem) ---
# env_get PLIK KLUCZ -> wartosc bez cudzyslowow i CR; pusty wynik, gdy brak klucza.
env_get() {
  local file="$1" key="$2" value
  value="$(sed -n "s/^${key}=//p" "$file" | head -n 1 | tr -d '\r')" || true
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}

# env_has PLIK KLUCZ -> 0, gdy klucz wystepuje (nawet z pusta wartoscia)
env_has() { grep -q "^${2}=" "$1"; }

# env_set_many PLIK KLUCZ=WARTOSC ... : jedna atomowa podmiana (plik tymczasowy w tym samym katalogu + mv).
# Wartosci przechodza przez ENVIRON awk-a (bez interpretacji ukosnikow). Prawa 0600.
env_set_many() {
  local file="$1"
  shift
  local tmp pair key
  tmp="$(umask 077 && mktemp "${file}.tmp.XXXXXX")" || return 1
  local keys=""
  for pair in "$@"; do
    key="${pair%%=*}"
    keys="${keys}${keys:+ }${key}"
    export "ENVSET_${key}=${pair#*=}"
  done
  if ! ENVSET_KEYS="$keys" awk '
    BEGIN { n = split(ENVIRON["ENVSET_KEYS"], k, " "); for (i = 1; i <= n; i++) want[k[i]] = 1 }
    {
      eq = index($0, "=")
      name = (eq > 0) ? substr($0, 1, eq - 1) : ""
      if (name in want) next
      print
    }
    END { for (i = 1; i <= n; i++) print k[i] "=" ENVIRON["ENVSET_" k[i]] }
  ' "$file" >"$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  for pair in "$@"; do
    unset "ENVSET_${pair%%=*}"
  done
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

# --- Alembic: head z drzewa plikow (bez importu Alembica) ---
# rev_pairs_from_text: stdin = tresc pliku migracji; wypisuje "REV <id>" i "DOWN <id|None>"
rev_pairs_from_text() {
  sed -n -E "
    s/^revision *(: *[A-Za-z]+)? *= *['\"]([^'\"]+)['\"].*/REV \2/p
    s/^down_revision *(: *[^=]+)? *= *None.*/DOWN None/p
    s/^down_revision *(: *[^=]+)? *= *['\"]([^'\"]+)['\"].*/DOWN \2/p
  "
}

# alembic_head_from_pairs: stdin = polaczone wyniki rev_pairs_from_text; wypisuje jedyny head albo kod != 0.
# Wypisuje tez (na stderr nic) liste znanych rewizji w pliku wskazanym przez ALEMBIC_KNOWN_FILE, gdy ustawiony.
alembic_head_from_pairs() {
  local revs downs heads
  local input
  input="$(cat)"
  revs="$(printf '%s\n' "$input" | awk '$1 == "REV" { print $2 }' | sort -u)"
  downs="$(printf '%s\n' "$input" | awk '$1 == "DOWN" && $2 != "None" { print $2 }' | sort -u)"
  [ -n "$revs" ] || return 1
  if [ -n "${ALEMBIC_KNOWN_FILE:-}" ]; then printf '%s\n' "$revs" >"$ALEMBIC_KNOWN_FILE"; fi
  heads="$(comm -23 <(printf '%s\n' "$revs") <(printf '%s\n' "$downs"))"
  [ "$(printf '%s\n' "$heads" | grep -c .)" = "1" ] || return 1
  printf '%s' "$heads"
}

# --- czas ---
now_epoch() { date -u +%s; }
