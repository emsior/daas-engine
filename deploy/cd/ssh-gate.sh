#!/usr/bin/env bash
# Wymuszona komenda klucza SSH uzytkownika `deploy` (authorized_keys):
#
#   command="/usr/local/lib/daas-cd/ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...
#
# Klucz moze wykonac WYLACZNIE trzy komendy (SSH_ORIGINAL_COMMAND, dokladne dopasowanie, pojedyncze spacje):
#   deploy <X.Y.Z> <sha256:64hex> <sha256:64hex> <40hex> <0|1>
#   status
#   maintenance on|off      (tryb serwisowy; uzywane przez operatora, nie przez workflow wydania)
# Wszystko inne (inna komenda, dodatkowe argumenty, znaki sterujace, --force, pusta komenda = sesja interaktywna)
# konczy sie kodem 2 BEZ wykonania czegokolwiek. Nie ma `eval`, nie ma przekazywania tekstu do powloki:
# argumenty wyciagamy z dopasowania wyrazenia regularnego i podajemy deploy.sh jako osobne slowa.
# Uzytkownik `deploy` jest w grupie `docker` (rownowaznik roota); ta bramka jest jedyna zapora, wiec nie rozszerzaj whitelisty.
# Plik zyje w ZAUFANEJ KOPII root:root (/usr/local/lib/daas-cd/ssh-gate.sh; instaluje ja bootstrap.sh): uzytkownik `deploy` nie moze go zmienic,
# a authorized_keys wskazuje te kopie (deploy/CD.md). Obok lezy deploy.sh (tez kopia) i plik `daas-dir` ze sciezka klonu.
set -euo pipefail

# Allowlista srodowiska zamiast denylisty: pierwsze wejscie uruchamia SIEBIE ponownie z pustym srodowiskiem i tylko kilkoma
# zmiennymi (PATH, LANG, HOME, USER, SSH_ORIGINAL_COMMAND oraz XDG_RUNTIME_DIR dla `systemd-run --user`). Zadna zmienna
# z sesji (DAAS_CD_*, DOCKER_*, COMPOSE_*, GIT_*, CURL_*, BASH_ENV, LD_*, ...) nie dociera do deploy.sh. Flaga --gated chroni przed petla.
# (Ograniczenie: BASH_ENV z sesji dziala jeszcze w pierwszym interpreterze; sshd ustawia je tylko przy PermitUserEnvironment/AcceptEnv,
#  czego bootstrap.sh nie wlacza.)
if [ "${1:-}" != "--gated" ]; then
  xdg=()
  if [[ "${XDG_RUNTIME_DIR:-}" =~ ^/run/user/[0-9]+$ ]]; then xdg=("XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"); fi
  exec /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    HOME="${HOME:-/nonexistent}" USER="${USER:-deploy}" SSH_ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND:-}" "${xdg[@]}" \
    /bin/bash --noprofile --norc "$0" --gated "$@"
fi
shift
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
umask 077

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${SSH_ORIGINAL_COMMAND:-}"

refuse() {
  printf 'DAAS_DEPLOY result=failed code=2 version=- previous=none schema=unknown duration=0 maintenance=off edge=ok\n'
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
