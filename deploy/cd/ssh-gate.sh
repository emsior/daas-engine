#!/usr/bin/env bash
# Wymuszona komenda klucza SSH uzytkownika `deploy` (authorized_keys):
#
#   command="/opt/daas-engine/deploy/cd/ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...
#
# Klucz moze wykonac WYLACZNIE trzy komendy (SSH_ORIGINAL_COMMAND, dokladne dopasowanie, pojedyncze spacje):
#   deploy <X.Y.Z> <sha256:64hex> <sha256:64hex> <40hex> <0|1>
#   status
#   maintenance on|off      (tryb serwisowy; uzywane przez operatora, nie przez workflow wydania)
# Wszystko inne (inna komenda, dodatkowe argumenty, znaki sterujace, --force, pusta komenda = sesja interaktywna)
# konczy sie kodem 2 BEZ wykonania czegokolwiek. Nie ma `eval`, nie ma przekazywania tekstu do powloki:
# argumenty wyciagamy z dopasowania wyrazenia regularnego i podajemy deploy.sh jako osobne slowa.
# Uzytkownik `deploy` jest w grupie `docker` (rownowaznik roota); ta bramka jest jedyna zapora, wiec nie rozszerzaj whitelisty.
set -euo pipefail

# Czyszczenie srodowiska: zadna zmienna sterujaca stagingiem/rejestrem nie moze przyjsc z sesji SSH.
for var in $(compgen -v | grep -E '^(DAAS_CD_|DOCKER_|COMPOSE_|BASH_ENV$|ENV$|CURL_|GIT_)' || true); do
  unset "$var"
done
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
umask 077

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${SSH_ORIGINAL_COMMAND:-}"

refuse() {
  printf 'DAAS_DEPLOY result=failed code=2 version=- previous=none schema=unknown duration=0 maintenance=off\n'
  exit 2
}

[ -n "$cmd" ] || refuse
[[ "$cmd" != *[[:cntrl:]]* ]] || refuse

re_deploy='^deploy ([0-9]+\.[0-9]+\.[0-9]+) (sha256:[0-9a-f]{64}) (sha256:[0-9a-f]{64}) ([0-9a-f]{40}) ([01])$'

if [[ "$cmd" == "status" ]]; then
  exec "$here/deploy.sh" status
elif [[ "$cmd" =~ ^maintenance\ (on|off)$ ]]; then
  exec "$here/deploy.sh" maintenance "${BASH_REMATCH[1]}"
elif [[ "$cmd" =~ $re_deploy ]]; then
  exec "$here/deploy.sh" deploy "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}" "${BASH_REMATCH[5]}"
fi
refuse
