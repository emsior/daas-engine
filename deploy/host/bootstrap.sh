#!/usr/bin/env bash
# Przygotowanie hosta DaaS Engine Cloud (Ubuntu 24.04 / Debian 12) jako kod: implementuje RUNBOOK §1.1-1.5, kopie lokalna
# z §4.2 oraz uzytkownika `deploy` z wymuszona komenda (deploy/cd/ssh-gate.sh). Neutralne wobec dostawcy.
#
# IDEMPOTENTNY: mozna uruchamiac wielokrotnie (kolejne uruchomienie nic nie psuje i naprawia dryf konfiguracji).
# Ten sam skrypt wola cloud-init.yaml przy pierwszym starcie, a recznie mozna go uruchomic na istniejacym serwerze:
#
#   sudo DAAS_REPO_REF=v2.0.0 OPERATOR_SSH_PUBLIC_KEY="ssh-ed25519 AAAA..." DEPLOY_SSH_PUBLIC_KEY="ssh-ed25519 AAAA..." bash bootstrap.sh
#   # albo klucze z plikow: OPERATOR_SSH_PUBLIC_KEY_FILE=/sciezka/operator.pub DEPLOY_SSH_PUBLIC_KEY_FILE=/sciezka/deploy.pub
#
# Zmienne: OPERATOR_USER (domyslnie operator), DAAS_REPO_URL, DAAS_DIR (domyslnie /opt/daas-engine) oraz OBOWIAZKOWA
# DAAS_REPO_REF = tag wydania vX.Y.Z albo pelny SHA commitu (zero klonowania ruchomej galezi). `main` tylko z jawna flaga --allow-main.
#
# Tryby (kazdy dziala sam, bez kluczy, i konczy prace):
#   sudo bash bootstrap.sh --refresh-cd                   # odswiez zaufana kopie skryptow CD (patrz nizej) z czystego klonu na otagowanym wydaniu
#   sudo bash bootstrap.sh --lock-provider-user <nazwa>   # zablokuj domyslne konto obrazu dostawcy (po potwierdzeniu logowania operatora)
#
# ZAUFANA KOPIA SKRYPTOW CD: ssh-gate.sh, deploy.sh i lib.sh sa kopiowane do /usr/local/lib/daas-cd/ (root:root 0755) razem z plikiem
# `daas-dir` (sciezka klonu), a authorized_keys uzytkownika `deploy` wskazuje te kopie. Uzytkownik `deploy` jest wlascicielem klonu
# (i katalogu deploy/cd), wiec samo chown plikow w klonie nic by nie dalo: moglby je usunac i utworzyc od nowa. Kopia nie zmienia sie
# razem z wydaniem; po wydaniu, ktore zmienia te trzy pliki, operator uruchamia --refresh-cd (deploy.sh ostrzega w dzienniku).
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
DAAS_REPO_REF="${DAAS_REPO_REF:-}"
DEPLOY_USER="deploy"
TRUSTED_DIR="/usr/local/lib/daas-cd"
DOCKER_KEY_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88" # oficjalny odcisk klucza GPG repozytorium Docker
export DEBIAN_FRONTEND=noninteractive

log() { printf '[bootstrap] %s\n' "$*"; }
die() {
  printf '[bootstrap] BLAD: %s\n' "$*" >&2
  exit 1
}

ALLOW_MAIN=0 MODE=full LOCK_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-main) ALLOW_MAIN=1 ;;
    --refresh-cd) MODE=refresh ;;
    --lock-provider-user)
      [ $# -ge 2 ] || die "--lock-provider-user wymaga nazwy konta"
      MODE=lock
      LOCK_USER="$2"
      shift
      ;;
    -h | --help)
      sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "nieznany argument: $1" ;;
  esac
  shift
done

[ "$(id -u)" = "0" ] || die "uruchom jako root (sudo)"
[[ "$OPERATOR_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "OPERATOR_USER niepoprawny"
[[ "$DAAS_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "DAAS_DIR niepoprawny"
command -v apt-get >/dev/null 2>&1 || die "wymagany system z apt (Ubuntu/Debian)"
# shellcheck source=/dev/null
. /etc/os-release
case "${ID:-}" in ubuntu | debian) ;; *) die "nieobslugiwany system: ${ID:-?}" ;; esac

# Zaufana kopia skryptow CD (root:root): uzytkownik deploy nie moze zmienic bramki SSH ani deploy.sh, ktore wykonuje klucz CI.
# Zrodlo: czysty klon (bez zmian sledzonych plikow); w trybie --refresh-cd dodatkowo dokladnie na tagu wydania (chyba ze --allow-main).
install_cd_copy() {
  local src="$DAAS_DIR/deploy/cd" f
  [ -d "$DAAS_DIR/.git" ] && [ -f "$src/deploy.sh" ] || die "brak klonu repozytorium w $DAAS_DIR (uruchom pelny bootstrap)"
  [ -z "$(sudo -u "$DEPLOY_USER" git -C "$DAAS_DIR" status --porcelain --untracked-files=no)" ] ||
    die "klon w $DAAS_DIR ma niezatwierdzone zmiany: nie kopiuje skryptow CD do katalogu zaufanego"
  if [ "$MODE" = "refresh" ] && [ "$ALLOW_MAIN" != "1" ]; then
    sudo -u "$DEPLOY_USER" git -C "$DAAS_DIR" describe --exact-match --tags HEAD >/dev/null 2>&1 ||
      die "HEAD klonu nie jest otagowanym wydaniem (przelacz na tag vX.Y.Z albo uzyj --allow-main)"
  fi
  install -d -m 0755 -o root -g root "$TRUSTED_DIR"
  for f in ssh-gate.sh deploy.sh lib.sh; do
    [ -f "$src/$f" ] || die "brak $src/$f"
    write_file "$TRUSTED_DIR/$f" 0755 root:root <"$src/$f"
  done
  printf '%s\n' "$DAAS_DIR" | write_file "$TRUSTED_DIR/daas-dir" 0644 root:root
  log "zaufana kopia skryptow CD: $TRUSTED_DIR (root:root)"
}

lock_provider_user() {
  local u="$1"
  [[ "$u" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "niepoprawna nazwa konta"
  case "$u" in root | "$OPERATOR_USER" | "$DEPLOY_USER") die "tego konta nie wolno zablokowac: $u" ;; esac
  [ "${SUDO_USER:-}" != "$u" ] || die "to konto prowadzi biezaca sesje; zaloguj sie jako $OPERATOR_USER"
  id "$u" >/dev/null 2>&1 || die "brak konta $u"
  [ -s "/home/$OPERATOR_USER/.ssh/authorized_keys" ] || die "operator nie ma klucza SSH (najpierw pelny bootstrap i test logowania)"
  grep -qs "^AllowUsers .*$OPERATOR_USER" /etc/ssh/sshd_config.d/10-hardening.conf || die "brak AllowUsers w sshd (najpierw pelny bootstrap)"
  usermod --lock --expiredate 1 --shell /usr/sbin/nologin "$u"
  log "konto $u zablokowane (haslo, wygasniecie, powloka nologin); logowanie administracyjne tylko jako $OPERATOR_USER"
}

case "$MODE" in
  refresh)
    install_cd_copy
    exit 0
    ;;
  lock)
    lock_provider_user "$LOCK_USER"
    exit 0
    ;;
esac

# --- pelny bootstrap: przypiety ref jest obowiazkowy ---
if [ -z "$DAAS_REPO_REF" ]; then
  [ "$ALLOW_MAIN" = "1" ] || die "DAAS_REPO_REF jest obowiazkowy: tag wydania vX.Y.Z albo pelny SHA commitu (main tylko z --allow-main)"
  DAAS_REPO_REF="main"
fi
[[ "$DAAS_REPO_REF" != *"<<"* ]] || die "DAAS_REPO_REF zawiera nieuzupelniony placeholder"
if [ "$DAAS_REPO_REF" = "main" ]; then
  [ "$ALLOW_MAIN" = "1" ] || die "ref main wymaga jawnej flagi --allow-main (ruchoma galaz nie jest przypieta)"
elif ! [[ "$DAAS_REPO_REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && ! [[ "$DAAS_REPO_REF" =~ ^[0-9a-f]{40}$ ]]; then
  die "DAAS_REPO_REF: oczekiwano tagu vX.Y.Z albo pelnego SHA (40 znakow hex)"
fi

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

# --- 1. pakiety bazowe (RUNBOOK 1.2-1.4) ---
log "pakiety bazowe"
apt-get update -qq
apt-get install -y -qq sudo ufw unattended-upgrades git curl openssl cron jq restic bind9-dnsutils ca-certificates logrotate gnupg >/dev/null
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
# AllowUsers: SSH tylko dla operatora i deploy (domyslne konto obrazu dostawcy traci dostep; gdyby klucz operatora byl bledny,
# wejscie jest tylko przez konsole dostawcy). MaxAuthTries 3 ogranicza zgadywanie.
write_file /etc/ssh/sshd_config.d/10-hardening.conf 0644 root:root <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowUsers $OPERATOR_USER $DEPLOY_USER
MaxAuthTries 3
EOF
sshd -t || die "konfiguracja sshd niepoprawna"
systemctl reload ssh >/dev/null 2>&1 || systemctl reload sshd >/dev/null 2>&1 || true
# Domyslne konto obrazu chmurowego nie ma juz dostepu SSH (AllowUsers). Po sprawdzeniu logowania operatora mozna je zablokowac w calosci:
#   sudo bash bootstrap.sh --lock-provider-user <nazwa>   (deploy/CD.md)

# --- 3. zapora: 22, 80, 443 (RUNBOOK 1.2; zobacz uwage R-11 na gorze pliku) ---
log "ufw"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw delete allow 22/tcp >/dev/null 2>&1 || true # stara regula z wczesniejszego uruchomienia
ufw limit 22/tcp >/dev/null # ograniczenie szybkosci polaczen (6 w 30 s z jednego adresu)
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
# Odcisk glownego klucza musi byc oficjalnym odciskiem Docker (nie ufamy samemu pobraniu przez HTTPS).
docker_key_ok() { # docker_key_ok PLIK
  gpg --show-keys --with-colons --fingerprint "$1" 2>/dev/null |
    awk -F: '$1 == "pub" { p = 1 } $1 == "fpr" && p == 1 { print $10; p = 0 }' | grep -qx "$DOCKER_KEY_FPR"
}
if [ -s /etc/apt/keyrings/docker.asc ] && ! docker_key_ok /etc/apt/keyrings/docker.asc; then
  log "klucz /etc/apt/keyrings/docker.asc ma nieoczekiwany odcisk: pobieram ponownie"
  rm -f /etc/apt/keyrings/docker.asc
fi
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
  key_tmp="$(mktemp)"
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o "$key_tmp"
  docker_key_ok "$key_tmp" || { rm -f "$key_tmp"; die "odcisk klucza GPG repozytorium Docker rozni sie od oficjalnego ($DOCKER_KEY_FPR)"; }
  install -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc
  rm -f "$key_tmp"
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
command="$TRUSTED_DIR/ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $DEPLOY_KEY
EOF

# --- 8. kod w /opt/daas-engine (publiczne repo), katalogi stanu i kopii ---
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DAAS_DIR"
if [ ! -d "$DAAS_DIR/.git" ]; then
  [ -z "$(ls -A "$DAAS_DIR")" ] || die "$DAAS_DIR nie jest repozytorium git i nie jest pusty"
  log "klonuje repozytorium do $DAAS_DIR"
  sudo -u "$DEPLOY_USER" git clone --quiet "$DAAS_REPO_URL" "$DAAS_DIR"
  sudo -u "$DEPLOY_USER" git -C "$DAAS_DIR" rev-parse --verify --quiet "${DAAS_REPO_REF}^{commit}" >/dev/null ||
    die "ref $DAAS_REPO_REF nie istnieje w $DAAS_REPO_URL"
  sudo -u "$DEPLOY_USER" git -C "$DAAS_DIR" checkout --quiet "$DAAS_REPO_REF"
else
  log "klon w $DAAS_DIR juz istnieje: ref nie jest zmieniany (wydania wdraza deploy.sh)"
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DAAS_DIR"
for d in deploy/cd/state deploy/cd/log deploy/backups; do
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DAAS_DIR/$d"
done
[ -x "$DAAS_DIR/deploy/cd/ssh-gate.sh" ] || die "brak $DAAS_DIR/deploy/cd/ssh-gate.sh (zly DAAS_REPO_REF? wymagane wydanie z deploy/cd)"
install_cd_copy

# --- 9. kopia nocna (RUNBOOK 4.2): codziennie 03:15 UTC, dziennik z rotacja; wysylka poza serwer tylko przez hak hosta ---
touch /var/log/daas-backup.log
chown "$DEPLOY_USER:$DEPLOY_USER" /var/log/daas-backup.log
chmod 600 /var/log/daas-backup.log
write_file /etc/logrotate.d/daas-backup 0644 root:root <<'EOF2'
/var/log/daas-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF2
write_file /etc/cron.d/daas-backup 0644 root:root <<EOF2
# Kopia zimna (api stoi kilka sekund) + hak kopii poza serwerem, jesli istnieje. Pomija sie bez deploy/.env.
15 3 * * * $DEPLOY_USER [ -x $DAAS_DIR/deploy/cd/nightly-backup.sh ] && $DAAS_DIR/deploy/cd/nightly-backup.sh >> /var/log/daas-backup.log 2>&1
EOF2

# --- 10. integracja z nakladka obserwowalnosci (deploy/compose.observability.yml; wlaczana przez COMPOSE_FILE w .env) ---
# Katalog textfile dla node_exportera istnieje zawsze (Docker bind-mount zalozylby go jako root-owy katalog bez prawa zapisu dla `deploy`).
# root:deploy 0775: skrypty textfile (backup-marker.sh z haka kopii i nocnej kopii, mail-failures.sh z crona) dzialaja jako `deploy`.
install -d -m 0775 -o root -g "$DEPLOY_USER" /var/lib/node_exporter/textfile
# Przejsciowy licznik L-4 (blad wysylki linku logowania), co 5 min; po cichu nic nie robi, dopóki nakladka nie jest wlaczona.
write_file /etc/cron.d/daas-observability 0644 root:root <<EOF2
*/5 * * * * $DEPLOY_USER grep -qs '^COMPOSE_FILE=.*compose\.observability\.yml' $DAAS_DIR/deploy/.env && [ -x $DAAS_DIR/deploy/observability/textfile/mail-failures.sh ] && $DAAS_DIR/deploy/observability/textfile/mail-failures.sh >/dev/null 2>&1
EOF2

log "po wydaniu, ktore zmienia deploy/cd/{deploy,ssh-gate,lib}.sh: sudo bash $DAAS_DIR/deploy/host/bootstrap.sh --refresh-cd (z klonu na tagu wydania)"
log "gotowe. Nastepne kroki (deploy/CD.md): zaloguj sie jako $OPERATOR_USER w DRUGIM oknie, potem:"
log "  sudo -u $DEPLOY_USER bash -c 'cd $DAAS_DIR/deploy && ./gen-env.sh --domain <domena>'   # .env z sekretami powstaje tu, nie w user-data"
log "  uzupelnij dane SMTP w $DAAS_DIR/deploy/.env (sudo -u $DEPLOY_USER), potem wykonaj pierwsze wydanie z GitHub Actions"
