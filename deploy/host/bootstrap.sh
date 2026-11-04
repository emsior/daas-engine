#!/usr/bin/env bash
# Przygotowanie hosta DaaS Engine Cloud (Ubuntu 24.04 / Debian 12) jako kod: implementuje RUNBOOK §1.1-1.5, kopie lokalna
# z §4.2 oraz uzytkownika `deploy` z wymuszona komenda (deploy/cd/ssh-gate.sh). Neutralne wobec dostawcy.
#
# IDEMPOTENTNY: mozna uruchamiac wielokrotnie (kolejne uruchomienie nic nie psuje i naprawia dryf konfiguracji).
# Ten sam skrypt wola cloud-init.yaml przy pierwszym starcie, a recznie mozna go uruchomic na istniejacym serwerze:
#
#   sudo OPERATOR_SSH_PUBLIC_KEY="ssh-ed25519 AAAA..." DEPLOY_SSH_PUBLIC_KEY="ssh-ed25519 AAAA..." bash bootstrap.sh
#   # albo klucze z plikow: OPERATOR_SSH_PUBLIC_KEY_FILE=/sciezka/operator.pub DEPLOY_SSH_PUBLIC_KEY_FILE=/sciezka/deploy.pub
#
# Zmienne (wszystkie opcjonalne poza kluczami): OPERATOR_USER (domyslnie operator), DAAS_REPO_URL, DAAS_REPO_REF (domyslnie main),
# DAAS_DIR (domyslnie /opt/daas-engine).
#
# NIE ZAWIERA I NIE GENERUJE SEKRETOW. Klucze publiczne nie sa sekretami. Plik `.env` (sekrety aplikacji) powstaje
# osobno na hoscie: `sudo -u deploy ./gen-env.sh` (deploy/CD.md), a dane SMTP wpisuje operator przez SSH.
#
# Uwaga R-11 (Docker a zapora): porty PUBLIKOWANE przez Dockera omijaja reguly ufw (Docker wpisuje wlasne reguly iptables).
# Dlatego deploy/compose.yml wiaze porty aplikacji (8000, 3000) wylacznie z 127.0.0.1, a publicznie sa tylko 80/443 Caddy.
# Nie publikuj zadnego nowego portu na 0.0.0.0 i nie polegaj na ufw jako jedynej zaporze. Zapore panelu dostawcy ustaw tak samo.
set -euo pipefail

OPERATOR_USER="${OPERATOR_USER:-operator}"
DAAS_DIR="${DAAS_DIR:-/opt/daas-engine}"
DAAS_REPO_URL="${DAAS_REPO_URL:-https://github.com/emsior/daas-engine.git}"
DAAS_REPO_REF="${DAAS_REPO_REF:-main}"
DEPLOY_USER="deploy"
export DEBIAN_FRONTEND=noninteractive

log() { printf '[bootstrap] %s\n' "$*"; }
die() {
  printf '[bootstrap] BLAD: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" = "0" ] || die "uruchom jako root (sudo)"
[[ "$OPERATOR_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "OPERATOR_USER niepoprawny"
command -v apt-get >/dev/null 2>&1 || die "wymagany system z apt (Ubuntu/Debian)"
# shellcheck source=/dev/null
. /etc/os-release
case "${ID:-}" in ubuntu | debian) ;; *) die "nieobslugiwany system: ${ID:-?}" ;; esac

# --- klucze publiczne (zmienna albo plik); pojedyncza linia, bez opcji, tylko ed25519 ---
read_key() { # read_key NAZWA_ZMIENNEJ -> wypisuje klucz
  local var="$1" file_var="${1}_FILE" key=""
  if [ -n "${!var:-}" ]; then
    key="${!var}"
  elif [ -n "${!file_var:-}" ] && [ -f "${!file_var}" ]; then
    key="$(head -n 1 "${!file_var}")"
  fi
  [[ "$key" != *"<<"* ]] || die "$var zawiera nieuzupelniony placeholder"
  [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/]+=*(\ [A-Za-z0-9._@:+-]+)?$ ]] || die "$var: oczekiwano jednej linii klucza publicznego ssh-ed25519"
  printf '%s' "$key"
}
OPERATOR_KEY="$(read_key OPERATOR_SSH_PUBLIC_KEY)"
DEPLOY_KEY="$(read_key DEPLOY_SSH_PUBLIC_KEY)"

# write_file SCIEZKA TRYB WLASCICIEL:GRUPA  (tresc ze stdin; zapis tylko przy zmianie)
write_file() {
  local path="$1" mode="$2" owner="$3" tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then
    rm -f "$tmp"
  else
    install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$path"
    rm -f "$tmp"
    log "zapisano $path"
  fi
  chmod "$mode" "$path"
  chown "$owner" "$path"
}

# --- 1. pakiety bazowe (RUNBOOK 1.2-1.4) ---
log "pakiety bazowe"
apt-get update -qq
apt-get install -y -qq sudo ufw unattended-upgrades git curl openssl cron jq restic bind9-dnsutils ca-certificates logrotate >/dev/null
systemctl enable --now cron >/dev/null 2>&1 || true

# --- 2. konto operatora i SSH tylko kluczem (RUNBOOK 1.1) ---
if ! id "$OPERATOR_USER" >/dev/null 2>&1; then
  log "tworze konto operatora $OPERATOR_USER"
  adduser --disabled-password --gecos "" "$OPERATOR_USER" >/dev/null
fi
usermod -aG sudo "$OPERATOR_USER"
# Konto bez hasla (logowanie wylacznie kluczem), wiec sudo bez hasla; jedynym czynnikiem jest klucz SSH operatora.
write_file "/etc/sudoers.d/90-$OPERATOR_USER" 0440 root:root <<EOF
$OPERATOR_USER ALL=(ALL) NOPASSWD:ALL
EOF
visudo -cf "/etc/sudoers.d/90-$OPERATOR_USER" >/dev/null || die "sudoers niepoprawny"
install -d -m 700 -o "$OPERATOR_USER" -g "$OPERATOR_USER" "/home/$OPERATOR_USER/.ssh"
write_file "/home/$OPERATOR_USER/.ssh/authorized_keys" 0600 "$OPERATOR_USER:$OPERATOR_USER" <<EOF
$OPERATOR_KEY
EOF

# Dopiero PO zapisaniu klucza operatora wylaczamy haslo i roota (plik 10-..., bo sshd bierze pierwsza znaleziona wartosc).
write_file /etc/ssh/sshd_config.d/10-hardening.conf 0644 root:root <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sshd -t || die "konfiguracja sshd niepoprawna"
systemctl reload ssh >/dev/null 2>&1 || systemctl reload sshd >/dev/null 2>&1 || true
# Domyslne konto obrazu chmurowego (z kluczem dostawcy) zostaje: zablokuj je recznie po sprawdzeniu logowania operatora (deploy/CD.md).

# --- 3. zapora: 22, 80, 443 (RUNBOOK 1.2; zobacz uwage R-11 na gorze pliku) ---
log "ufw"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null # HTTP/3: compose.yml publikuje tez 443/udp
ufw --force enable >/dev/null

# --- 4. automatyczne aktualizacje bezpieczenstwa (RUNBOOK 1.3), bez interaktywnego dpkg-reconfigure ---
write_file /etc/apt/apt.conf.d/20auto-upgrades 0644 root:root <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# --- 5. swap 2 GB, czas UTC (RUNBOOK 1.4) ---
if [ -z "$(swapon --show --noheadings 2>/dev/null)" ] && [ ! -e /swapfile ]; then
  log "swap 2 GB"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
fi
if [ -f /swapfile ] && ! grep -qs '^/swapfile ' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi
write_file /etc/sysctl.d/99-swappiness.conf 0644 root:root <<'EOF'
vm.swappiness=10
EOF
sysctl --system >/dev/null 2>&1 || true
timedatectl set-timezone UTC || true
timedatectl set-ntp true 2>/dev/null || { apt-get install -y -qq systemd-timesyncd >/dev/null && timedatectl set-ntp true || true; }

# --- 6. Docker Engine i Compose v2 z oficjalnego repozytorium (RUNBOOK 1.5, bez curl | sh) ---
log "docker"
install -m 0755 -d /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
fi
chmod a+r /etc/apt/keyrings/docker.asc
write_file /etc/apt/sources.list.d/docker.list 0644 root:root <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable
EOF
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
systemctl enable --now docker >/dev/null 2>&1 || true
# Grupa `docker` = w praktyce root. Nalezy do niej TYLKO uzytkownik `deploy` (z wymuszona komenda); operator uzywa `sudo -u deploy`.

# --- 7. uzytkownik deploy z wymuszona komenda ---
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  log "tworze uzytkownika $DEPLOY_USER"
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
usermod -p '*' "$DEPLOY_USER" # haslo niedostepne, ale konto nie jest zablokowane (sshd przyjmie klucz)
command -v loginctl >/dev/null 2>&1 && loginctl enable-linger "$DEPLOY_USER" 2>/dev/null || true # systemd-run --user w deploy.sh
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
write_file "/home/$DEPLOY_USER/.ssh/authorized_keys" 0600 "$DEPLOY_USER:$DEPLOY_USER" <<EOF
command="$DAAS_DIR/deploy/cd/ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $DEPLOY_KEY
EOF

# --- 8. kod w /opt/daas-engine (publiczne repo), katalogi stanu i kopii ---
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DAAS_DIR"
if [ ! -d "$DAAS_DIR/.git" ]; then
  [ -z "$(ls -A "$DAAS_DIR")" ] || die "$DAAS_DIR nie jest repozytorium git i nie jest pusty"
  log "klonuje repozytorium do $DAAS_DIR"
  sudo -u "$DEPLOY_USER" git clone --quiet "$DAAS_REPO_URL" "$DAAS_DIR"
  sudo -u "$DEPLOY_USER" git -C "$DAAS_DIR" checkout --quiet "$DAAS_REPO_REF"
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DAAS_DIR"
for d in deploy/cd/state deploy/cd/log deploy/backups; do
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DAAS_DIR/$d"
done
[ -x "$DAAS_DIR/deploy/cd/ssh-gate.sh" ] || die "brak $DAAS_DIR/deploy/cd/ssh-gate.sh (zly DAAS_REPO_REF? wymagane wydanie z deploy/cd)"

# --- 9. kopia lokalna (RUNBOOK 4.2): codziennie 03:15 UTC, dziennik z rotacja; bez wysylki poza serwer ---
touch /var/log/daas-backup.log
chown "$DEPLOY_USER:$DEPLOY_USER" /var/log/daas-backup.log
chmod 600 /var/log/daas-backup.log
write_file /etc/logrotate.d/daas-backup 0644 root:root <<'EOF'
/var/log/daas-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
write_file /etc/cron.d/daas-backup 0644 root:root <<EOF
# Kopia zimna (api stoi kilka sekund). Pomija sie, dopoki nie istnieje deploy/.env (przed gen-env.sh).
15 3 * * * $DEPLOY_USER cd $DAAS_DIR/deploy && [ -f .env ] && ./backup.sh >> /var/log/daas-backup.log 2>&1
EOF

log "gotowe. Nastepne kroki (deploy/CD.md): zaloguj sie jako $OPERATOR_USER w DRUGIM oknie, potem:"
log "  sudo -u $DEPLOY_USER bash -c 'cd $DAAS_DIR/deploy && ./gen-env.sh --domain <domena>'   # .env z sekretami powstaje tu, nie w user-data"
log "  uzupelnij dane SMTP w $DAAS_DIR/deploy/.env (sudo -u $DEPLOY_USER), potem wykonaj pierwsze wydanie z GitHub Actions"
