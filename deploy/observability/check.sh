#!/usr/bin/env bash
# Statyczne kontrole obserwowalnosci przez kontenery oficjalnych obrazow (bez lokalnych binariow promtool/amtool):
#   1. promtool check config   2. promtool check rules   3. promtool test rules   4. amtool check-config
#   (na konfiguracji WYRENDEROWANEJ z atrapami: render-alertmanager.sh)   5. dashboard Grafany: poprawny JSON.
# Obrazy bierze z compose.observability.yml (jedno zrodlo tagow). Nic nie zapisuje w repozytorium.
# Uzycie: bash deploy/observability/check.sh   (wymaga Dockera; kod 0 = wszystko zielone)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose="$here/../compose.observability.yml"

fail() {
  echo "check.sh: $*" >&2
  exit 1
}
command -v docker >/dev/null 2>&1 || fail "brak polecenia docker"

image_of() {
  sed -n "s|^[[:space:]]*image:[[:space:]]*\\($1:[^[:space:]]*\\)[[:space:]]*\$|\\1|p" "$compose" | head -n 1
}
prom_image="$(image_of 'prom/prometheus')"
am_image="$(image_of 'prom/alertmanager')"
[ -n "$prom_image" ] && [ -n "$am_image" ] || fail "nie znaleziono obrazow prometheus/alertmanager w compose.observability.yml"
echo "Obrazy: $prom_image, $am_image"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
user="$(id -u):$(id -g)" # pliki z workspace i z $work czytelne dla uzytkownika runnera

echo "== promtool check config"
docker run --rm --user "$user" \
  -v "$here/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$here/rules:/etc/prometheus/rules:ro" \
  -v "$here/secrets/metrics_token.example:/etc/prometheus/secrets/metrics_token:ro" \
  --entrypoint promtool "$prom_image" check config /etc/prometheus/prometheus.yml

echo "== promtool check rules"
docker run --rm --user "$user" -v "$here/rules:/rules:ro" \
  --entrypoint promtool "$prom_image" check rules /rules/recording.yml /rules/alerts.yml

echo "== promtool test rules"
mapfile -t tests < <(cd "$here/tests" && printf '%s\n' ./*.test.yml | sed 's|^\./||')
[ "${#tests[@]}" -gt 0 ] || fail "brak plikow tests/*.test.yml"
docker run --rm --user "$user" -v "$here:/work:ro" -w /work/tests \
  --entrypoint promtool "$prom_image" test rules "${tests[@]}"

echo "== amtool check-config (konfiguracja wyrenderowana z atrapami)"
mkdir -p "$work/secrets"
printf '%s' 'ci-dummy-password' >"$work/secrets/smtp_password"
printf '%s' 'http://127.0.0.1:9/x' >"$work/secrets/heartbeat_url"
ALERT_OUT_DIR="$work/am" ALERT_SMTP_HOSTPORT=smtp.test.invalid:587 ALERT_SMTP_USER=ci \
  ALERT_FROM=ci-from ALERT_TO=ci-to bash "$here/render-alertmanager.sh" >/dev/null
docker run --rm --user "$user" \
  -v "$work/am/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  -v "$work/secrets:/etc/alertmanager/secrets:ro" \
  --entrypoint amtool "$am_image" check-config /etc/alertmanager/alertmanager.yml

echo "== dashboard Grafany"
python3 - "$here/grafana/dashboards/daas-overview.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["schemaVersion"] >= 39, "schemaVersion < 39"
ids = [p["id"] for p in d["panels"]]
assert len(ids) == len(set(ids)), "powtorzone id paneli"
for p in d["panels"]:
    if p["type"] != "row":
        assert p["datasource"]["uid"] == "prometheus", p["title"]
print("dashboard OK:", len(d["panels"]), "paneli")
PY
echo "check.sh: OK"
