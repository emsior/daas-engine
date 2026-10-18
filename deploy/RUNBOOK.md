# Runbook pilota: DaaS Engine Cloud na jednym VPS

Część ogólna, publiczna. Opisuje, jak postawić, utrzymać i zamknąć pilota na jednym serwerze. Zakres: jedna instancja `cloud`, jeden klient pilota, VPS ok. 2 vCPU / 4 GB RAM. Dotyczy wersji z gałęzi PR „2.0.0”; przed scaleniem w miejsce `<tag>` podaj commit lub tag, który wdrażasz (po wydaniu: tag wersji).

Czego tu nie ma: wyboru dostawców, kosztów, danych pilota i decyzji prawnych. To zostaje po stronie operatora, poza repozytorium. Instalację, aktualizację, rollback, kopie i zmienne opisuje [`README.md`](README.md); ten plik go nie powiela, tylko uzupełnia o to, czego pilot na produkcji jeszcze potrzebuje.

Polecenia systemowe są przykładami dla Ubuntu 24.04 LTS albo Debiana 12. Placeholdery: `<domena>`, `<ip>`, `<user>` (konto operatora na serwerze), `<tag>`, `<wersja>` (np. `2.0.0`, bez `v`), `<org_id>`, `<adres heartbeat>`. Adresy `example.com` są tylko przykładami.

Każda sekcja kończy się linią „Dowód:”: co sprawdzić i jaki wynik oznacza, że krok się udał.

## 0. Zanim zaczniesz

Konta i usługi (typy; konkretnych dostawców wybiera operator):

- [ ] VPS w regionie UE, ok. 2 vCPU / 4 GB RAM, Ubuntu 24.04 LTS albo Debian 12, z publicznym IPv4 (IPv6 opcjonalnie)
- [ ] domena (albo subdomena) i dostęp do jej strefy DNS (np. rejestrator lub osobny hosting DNS)
- [ ] usługa poczty transakcyjnej z SMTP na porcie 587 z STARTTLS i uwierzytelnianiem domeny nadawcy (np. przekaźnik SMTP); port 465 (TLS od razu) nie jest obsługiwany
- [ ] magazyn obiektowy w UE na kopie poza serwerem, najlepiej z niezmiennością lub wersjonowaniem (np. bucket zgodny z S3)
- [ ] monitoring zewnętrzny z kontrolą HTTP oraz usługa „heartbeat” (adres, który kopia wywołuje po sukcesie; alarm, gdy wywołanie nie przyjdzie)
- [ ] menedżer haseł na `.env`, `org_id`, klucz API, hasło repozytorium kopii i adresy usług
- [ ] drugi, tani serwer lub maszyna wirtualna do miesięcznego testu odtworzenia (może być na godziny)
- [ ] nowa para kluczy SSH tylko do tego serwera

Decyzje przed pierwszym uruchomieniem:

- [ ] domena pilota i okno serwisowe (kopia zimna zatrzymuje `api` na czas wykonania `tar`, README: kilka sekund; patrz sekcja 4)
- [ ] retencja organizacji (domyślnie 90 dni, `DAAS_RETENTION_DAYS`) i okres trzymania kopii; oba muszą zgadzać się z tym, co obiecujesz klientowi w umowie powierzenia (`../docs/DPA_v1_PL.md` §6)
- [ ] umowa powierzenia: szkic do przeglądu prawnego (DPA v1); lista podprocesorów musi być podana przed uruchomieniem produkcyjnym (§5). Usługi z tej listy (serwer, poczta, magazyn kopii, monitoring) mogą wymagać wpisu; ustal to z osobą prawniczą
- [ ] streszczenie przez model (LLM) zostaje wyłączone (domyślnie); nie dodawaj `COMPOSE_FILE=compose.yml:compose.llm.yml` bez osobnej decyzji
- [ ] kto i jak szybko reaguje na alarm (kanał powiadomień, zastępca) oraz kto kontaktuje się z klientem w incydencie

Dowód: każdy punkt odhaczony, a dane kont leżą w menedżerze haseł, nie w repozytorium.

## 1. Przygotowanie hosta

Wykonaj raz, przed instalacją aplikacji. Najpierw tworzysz konto operatora, potem zamykasz logowanie hasłem i roota.

### 1.1 Konto operatora i SSH tylko kluczem

Jako root, jednorazowo (na Debianie 12 najpierw `apt-get install -y sudo`):

```bash
adduser <user>                      # hasło służy tylko do sudo; do SSH będzie wyłączone
usermod -aG sudo <user>
```

Z własnej stacji roboczej wgraj klucz i sprawdź logowanie w **drugim** oknie, zanim cokolwiek zamkniesz:

```bash
ssh-copy-id -i ~/.ssh/<klucz>.pub <user>@<ip>
ssh <user>@<ip>
```

Na serwerze, jako `<user>`:

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) '
```

Plik nazywa się `10-...`, bo `sshd` bierze pierwszą znalezioną wartość, a obrazy chmurowe często dokładają własny `50-cloud-init.conf` z hasłem włączonym. Nie zamykaj sesji, dopóki nowe logowanie kluczem w osobnym oknie nie zadziała.

### 1.2 Zapora: 22, 80, 443

```bash
sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp              # HTTP/3: compose.yml publikuje też 443/udp
sudo ufw enable
sudo ufw status verbose
```

Uwaga: porty publikowane przez Dockera omijają reguły UFW. Porty aplikacji (8000, 3000) chroni to, że `compose.yml` wiąże je wyłącznie z `127.0.0.1`; nie zmieniaj tego (README, „Operacje”). Jeśli dostawca VPS ma zaporę w panelu, ustaw te same trzy porty także tam.

### 1.3 Automatyczne aktualizacje bezpieczeństwa

```bash
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades      # odpowiedz: Yes
cat /etc/apt/apt.conf.d/20auto-upgrades
sudo unattended-upgrade --dry-run -d 2>&1 | tail -n 5
```

To obejmuje pakiety systemu, nie obejmuje pakietów z repozytorium Dockera (aktualizujesz je ręcznie, sekcja 7). Restart serwera po aktualizacji jądra jest ręczny: tygodniowo sprawdzaj `/var/run/reboot-required`.

### 1.4 Swap, czas i narzędzia

```bash
swapon --show                                         # jeśli coś wypisze, pomiń trzy kolejne linie
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null && sudo sysctl --system >/dev/null

sudo timedatectl set-timezone UTC                     # cron i nazwy kopii mają się zgadzać z UTC
sudo timedatectl set-ntp true                         # jeśli błąd: sudo apt-get install -y systemd-timesyncd

sudo apt-get install -y git curl openssl cron jq restic bind9-dnsutils ca-certificates
sudo systemctl enable --now cron
sudo install -d -o <user> -g <user> /opt/daas-engine  # katalog z README, krok 1
```

Swap w tej postaci zakłada system plików ext4. Limity pamięci kontenerów są w `compose.yml` (`api` 1,5 GB).

### 1.5 Docker Engine i Compose v2 z oficjalnego repozytorium

Bez skryptu `curl | sh`: klucz i repozytorium dodajesz jawnie. Dla Ubuntu. Dla Debiana zamień `ubuntu` na `debian` w obu adresach URL (nazwę wydania, np. `bookworm`, i tak daje `$VERSION_CODENAME`):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Uprawnienia: członek grupy `docker` ma w praktyce uprawnienia roota na serwerze (może zamontować dowolny katalog w kontenerze). Dodaj do niej tylko `<user>`, zabezpieczonego kluczem SSH, i nikogo więcej:

```bash
sudo usermod -aG docker <user>        # wyloguj się i zaloguj ponownie
docker --version                      # README wymaga Docker Engine 25+
docker compose version                # Compose v2 (README: testowane na v5.5)
```

Dowód: `sudo sshd -T` pokazuje `passwordauthentication no` i `permitrootlogin no`; `sudo ufw status` wypisuje tylko 22, 80, 443/tcp i 443/udp; `timedatectl` mówi `System clock synchronized: yes` i `Time zone: Etc/UTC`; `free -h` pokazuje swap 2G; `docker --version` to 25 lub nowszy, a `docker compose version` zwraca wersję 2 lub nowszą; `ls -ld /opt/daas-engine` pokazuje właściciela `<user>`.

## 2. DNS i poczta

### 2.1 DNS

U operatora strefy dodaj rekord `A` domeny `<domena>` na `<ip>`. Rekord `AAAA` dodaj tylko wtedy, gdy serwer ma IPv6 i sprawdzisz go (`curl -6 -sSI https://<domena>/login`); zły `AAAA` psuje wydanie certyfikatu. Przed uruchomieniem ustaw TTL 300 s, po tygodniu stabilnej pracy podnieś do 3600 s. Caddy wystawi certyfikat sam, ale tylko gdy DNS wskazuje na ten serwer, a porty 80 i 443 są otwarte (`Caddyfile`, nagłówek).

```bash
dig +short A <domena>               # ma zwrócić <ip>
dig +short AAAA <domena>            # puste albo adres serwera
```

### 2.2 Poczta: tryb `smtp` w `.env`

Link logowania jest jedyną drogą wejścia do kokpitu, więc poczta jest krytyczna. Domyślny `console` po `gen-env.sh` służy tylko do prób (README, „Poczta”). Po `./gen-env.sh` (sekcja 3) edytuj `.env` edytorem, nie przez `echo` (hasło SMTP nie może wylądować w historii powłoki). Docelowe wartości:

```
DAAS_EMAIL_BACKEND=smtp
DAAS_EMAIL_FROM=DaaS Engine <raporty@<domena>>
DAAS_SMTP_HOST=smtp.example.com
DAAS_SMTP_PORT=587
DAAS_SMTP_USER=<login SMTP>
DAAS_SMTP_PASSWORD='<hasło SMTP>'
DAAS_SMTP_STARTTLS=true
```

Nawiasy ostre wokół adresu w `DAAS_EMAIL_FROM` są częścią wartości, wartość zostaje bez cudzysłowów (jak w szablonie `gen-env.sh`). Hasło daj w pojedynczych cudzysłowach, bo Compose rozwija `$` w wartościach `.env` bez cudzysłowów i w podwójnych; hasło nie może zawierać znaku `'`. Usuń z `.env` linię `DAAS_ALLOW_CONSOLE_MAIL=true`. Aplikacja łączy się przez STARTTLS (port 587); nie ma trybu TLS od razu (465). Podgląd bez ujawniania hasła:

```bash
grep -E '^DAAS_(EMAIL|SMTP|ALLOW)' .env | sed 's/^\(DAAS_SMTP_PASSWORD=\).*/\1***/'
timeout 5 bash -c '</dev/tcp/smtp.example.com/587' && echo "port 587 osiągalny"
```

### 2.3 SPF, DKIM, DMARC

Rekordy ustawia się w domenie z adresu nadawcy (`DAAS_EMAIL_FROM`). Dokładne wartości SPF i DKIM podaje usługa pocztowa. Przykładowy kształt:

```
<domena>.          TXT  "v=spf1 include:spf.smtp.example.com -all"
sel1._domainkey.<domena>.  TXT/CNAME  (wartość od usługi pocztowej)
_dmarc.<domena>.   TXT  "v=DMARC1; p=none; rua=mailto:dmarc@example.com"
```

Zacznij od `p=none` i zbieraj raporty; po kilku tygodniach bez fałszywych odrzuceń zaostrz politykę (`quarantine`). Sprawdzenie:

```bash
dig +short TXT <domena>
dig +short TXT _dmarc.<domena>
```

### 2.4 Test doręczenia linku logowania

Po starcie aplikacji (sekcja 3), z hosta lub ze stacji:

```bash
curl -sS -X POST https://<domena>/api/v1/auth/magic-link \
  -H 'Content-Type: application/json' -d '{"email":"wlasciciel@example.com"}'
cd /opt/daas-engine/deploy && docker compose logs --since 5m api | grep -c 'magic link e-mail failed'
```

Odpowiedź API to `202` także wtedy, gdy wysyłka się nie uda (brak wyroczni dla adresów); jedynym innym typowym kodem jest `429` po przekroczeniu limitu (3 prośby na minutę na adres, `DAAS_RATE_LIMIT_MAGIC_EMAIL_PER_MIN`, i 10 na IP, `DAAS_RATE_LIMIT_MAGIC_IP_PER_MIN`). Jedyny ślad błędu wysyłki to wpis `magic link e-mail failed` w dzienniku `api`. Dlatego test ma dwa elementy: liczba z `grep -c` ma być `0`, a wiadomość ma dojść do skrzynki **u innego dostawcy niż nadawca**. W nagłówkach otrzymanej wiadomości (źródło) szukaj wyników SPF, DKIM i DMARC równych `pass`. Link jest ważny 15 minut (`DAAS_MAGIC_LINK_TTL_MINUTES`). Powtarzaj ten test po każdej zmianie poczty i raz w miesiącu (sekcja 7).

Dowód: `dig +short A <domena>` zwraca `<ip>`; rekordy SPF i DMARC są widoczne w `dig`; wiadomość z linkiem doszła do zewnętrznej skrzynki w ciągu minut, z SPF/DKIM/DMARC `pass`, a `grep -c` w dzienniku `api` zwraca `0`.

## 3. Pierwsze wdrożenie

Wykonaj kroki 1-5 z [`README.md`](README.md), „Instalacja krok po kroku”, jako `<user>`. Zamiast `v2.0.0` użyj `<tag>`, a w `--expect-version` wersji `<wersja>`. Poniżej tylko to, czego tam brakuje.

**Przed `docker compose build`.** W kroku 2 ustaw w `.env` tryb `smtp` według sekcji 2.2. Sprawdź, co wygenerował `gen-env.sh` (`DAAS_VERSION`, `DAAS_DOMAIN`, `DAAS_COCKPIT_ORIGIN`, `DAAS_SECRET_KEY`, `DAAS_METRICS_TOKEN`):

```bash
stat -c '%a %n' .env                                  # 600 .env
grep -E '^(DAAS_VERSION|DAAS_DOMAIN|DAAS_COCKPIT_ORIGIN|DAAS_EMAIL_BACKEND)=' .env
```

Natychmiast zapisz cały `.env` w menedżerze haseł. Kopia zapasowa go nie zawiera, a bez `DAAS_SECRET_KEY` po awarii wszystkie sesje i podpisy webhooków przepadają (sekcja 4.1).

**Start z Caddy.** Każde `up` na tym serwerze robisz z profilem `edge`:

```bash
docker compose build
docker compose --profile edge up -d
docker compose ps                                     # api i web: healthy, caddy: running
```

Uruchomienie bez `--profile edge` nie startuje Caddy; ze wspólnym proxy (README, „Wspólne proxy na VPS”) to samo robisz celowo, w pilocie na jednym serwerze nie.

**Organizacja i klucz (krok 4 README).** Polecenie `create-org` wypisuje `org_id` i `api_key` jeden raz; klucz nie jest nigdzie zapisany. Skopiuj oba od razu do menedżera haseł (wpis „org_id”, wpis „klucz API”). Zgubiony klucz = nowy klucz (owner w kokpicie albo `POST /api/v1/orgs/{org_id}/api-keys`); nie da się go odczytać. Adres `--owner-email` to adres osoby, która będzie właścicielem organizacji.

**Test dymny przez publiczny adres (krok 5 README).** `smoke.sh` robi kontrole lokalne (`/health`, `/ready`, `/api/v1/health`, 401 bez klucza na `/api/v1/me`, `/login` kokpitu), a `--public` dokłada do nich kontrolę przez proxy: `/api/v1/health`, `/login`, ukryty `/api/v1/metrics` (404) i nagłówek HSTS. Kończy się `SMOKE OK` i kodem 0. Dodatkowo, już poza README:

```bash
sudo ss -tlnp | grep -E ':(80|443|8000|3000) '        # 8000 i 3000 tylko na 127.0.0.1
echo | openssl s_client -connect <domena>:443 -servername <domena> 2>/dev/null | openssl x509 -noout -issuer -enddate
```

Ze stacji spoza serwera `curl -m 5 http://<ip>:8000/api/v1/health` ma się nie połączyć. Na koniec wykonaj test poczty (sekcja 2.4), pierwszą kopię i test odtworzenia (sekcje 4 i 5) **zanim klient wgra pierwsze dane**.

Dowód: `./smoke.sh --public https://<domena> --wait 90 --expect-version <wersja>` kończy się `SMOKE OK` (kod wyjścia 0; wersję `--expect-version` porównuje z `/health` na `127.0.0.1:8000`); `ss` pokazuje 8000 i 3000 wyłącznie na `127.0.0.1`; `.env` ma prawa 600 i leży w menedżerze haseł razem z `org_id` i kluczem API; certyfikat ma `notAfter` za ok. 90 dni.

## 4. Kopie zapasowe

### 4.1 Co jest w kopii, a co nie

W archiwum `daas-<data UTC>.tar.gz` (`backup.sh`) są wolumeny `control` (control.db) i `runtime` (organizacje: DuckDB, uploady, raporty, audyt). Obok leżą `.sha256` i `.meta`. Poza kopią zostają:

- `.env`, w tym `DAAS_SECRET_KEY` i `DAAS_METRICS_TOKEN`: trzymaj w menedżerze haseł i aktualizuj po każdej zmianie;
- `caddy_data` (certyfikaty i konto ACME): Caddy wystawia je od nowa po starcie, o ile DNS wskazuje na serwer;
- `runtime/outbox/` (linki logowania trybu `console`) i pliki `*.tmp` uploadów;
- obrazy Dockera (budujesz je z `<tag>`), konfiguracja hosta (SSH, UFW, cron) i dzienniki kontenerów;
- klucze API w postaci jawnej (baza trzyma tylko skróty SHA-256).

Kopia zawiera dane klienta. Retencja organizacji jej nie obejmuje (`../docs/SECURITY.md`, „Data handling”), więc okres trzymania kopii ustalasz osobno (sekcja 0).

### 4.2 Kopia lokalna: cron i tryb zimny

Tryb domyślny jest zimny: `backup.sh` zatrzymuje **tylko `api`** na czas `tar`, potem uruchamia go ponownie (kokpit i Caddy działają dalej, wywołania `/api/*` dostają w tym czasie błąd z Caddy). Skutki: zadania w toku kończą się jako `failed` z kodem `abandoned`, a monitoring może zgłosić krótką niedostępność. Dlatego kopię planuj na noc, poza oknem pracy klienta, a w monitoringu wymagaj 2-3 kolejnych błędów przed alarmem. Tryb `--online` nie zatrzymuje `api`, ale kopia DuckDB może być niespójna: tylko awaryjnie, a do odtworzenia używaj kopii zimnych.

Dziennik kopii i rotacja:

```bash
sudo touch /var/log/daas-backup.log && sudo chown <user>: /var/log/daas-backup.log
sudo tee /etc/logrotate.d/daas-backup >/dev/null <<'EOF'
/var/log/daas-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
```

### 4.3 Kopia poza serwerem, szyfrowana, z heartbeatem

Plik archiwum na serwerze nie jest kopią na wypadek utraty serwera. Poniżej przykład z `restic` do magazynu obiektowego w UE (zamiennie `rclone` z szyfrowanym zdalnym magazynem); to konfiguracja hosta, nie część repozytorium. Dane dostępowe w pliku `0600`. Wartości są w pojedynczych cudzysłowach (powłoka nie rozwinie w nich `$`), poza `RESTIC_PASSWORD_FILE`, który potrzebuje `$HOME` i dlatego jest w podwójnych:

```bash
mkdir -p ~/.config
install -m 600 /dev/null ~/.config/daas-backup.env
install -m 600 /dev/null ~/.config/daas-restic-password
$EDITOR ~/.config/daas-restic-password              # długie hasło repozytorium; zapisz je też w menedżerze haseł
$EDITOR ~/.config/daas-backup.env
```

Zawartość `~/.config/daas-backup.env`:

```
export RESTIC_REPOSITORY='s3:https://<endpoint magazynu>/<bucket>'
export RESTIC_PASSWORD_FILE="$HOME/.config/daas-restic-password"
export AWS_ACCESS_KEY_ID='<klucz dostępu>'
export AWS_SECRET_ACCESS_KEY='<sekret>'
HEARTBEAT_URL='<adres heartbeat>'
```

Zgubione hasło repozytorium oznacza kopie nie do odczytania. Inicjalizacja jest jednorazowa:

```bash
( . ~/.config/daas-backup.env && restic init )
```

Skrypt nocny (host-lokalny, `backup.sh` zostaje bez zmian). Heartbeat jest ostatni, więc wywoła się tylko po udanej kopii **i** wysyłce:

```bash
cat > ~/daas-nightly.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
. "$HOME/.config/daas-backup.env"
cd /opt/daas-engine/deploy
./backup.sh
restic backup --tag daas /opt/daas-engine/deploy/backups
restic forget --tag daas --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune
curl -fsS -m 10 --retry 3 -o /dev/null "$HEARTBEAT_URL"
EOF
chmod 700 ~/daas-nightly.sh
( crontab -l 2>/dev/null; echo '15 3 * * * $HOME/daas-nightly.sh >> /var/log/daas-backup.log 2>&1' ) | crontab -
```

Wartości `--keep-*` są przykładem: dopasuj je do okresu z umowy (sekcja 0). Bez wysyłki poza serwer wystarczy w cronie sama linia `cd /opt/daas-engine/deploy && ./backup.sh >> /var/log/daas-backup.log 2>&1 && curl -fsS -m 10 -o /dev/null <adres heartbeat>`, ale to nie chroni przed utratą serwera. Uwaga: niezmienność magazynu (Object Lock) może kolidować z `--prune` i z plikami blokad `restic`; sprawdź to w dokumentacji `restic` i dostawcy albo wybierz wersjonowanie zamiast blokady. W usłudze heartbeat ustaw okres 1 dzień i tolerancję rzędu 2 godzin.

### 4.4 Rotacja lokalna i kontrola

`backup.sh` zostawia `KEEP` najnowszych archiwów (domyślnie 14; `KEEP=<n>` przed poleceniem zmienia to). Kontrola ręczna:

```bash
cd /opt/daas-engine/deploy
ls -lh backups/ | tail -n 6
stat -c '%a %n' backups/daas-*.tar.gz | tail -n 3          # 600
( cd backups && sha256sum -c "$(ls -1 daas-*.tar.gz.sha256 | tail -n 1)" )
cat "backups/$(ls -1 backups | grep '\.meta$' | tail -n 1)"   # created_utc, daas_version, mode=cold
( . ~/.config/daas-backup.env && restic snapshots --tag daas | tail -n 5 )
```

Dowód: po pierwszym ręcznym `~/daas-nightly.sh` pojawia się archiwum z `.sha256` i `.meta` (`mode=cold`), `sha256sum -c` zwraca `OK`, `restic snapshots` pokazuje nową migawkę, a usługa heartbeat odnotowuje wywołanie. Następnego dnia po 03:15 UTC ten sam obraz bez udziału człowieka.

## 5. Restore drill and isolated recovery (monthly)

Run the monthly drill on a **separate approved host** in the same region and under the same data-handling conditions as production. Prepare the matching application images, Compose, `nft`, `curl`, the archive and its `.sha256`, and the private deployment configuration **before isolation**. All commands below run from `deploy/`; no image pulls, builds or package installations belong inside the isolated phase. Keep the pre-restore archive and checksum: they contain the previous state, not an automatically reconciled database.

### 5.1 Close ingress and prove the egress block

Close every public ingress path first. With the bundled proxy, run `docker compose --profile edge stop caddy`; with a shared proxy, put the DaaS routes into maintenance and verify that public access is closed. Use the provider console for the following host-wide firewall change: it also blocks SSH replies and other services' outgoing traffic. Do not rely on stopping Caddy to block outbound messages.

Use operator-controlled HTTP probe endpoints with reachable **literal IPv4 and IPv6 addresses**, not DNS names. Set `PROBE_V4`, `PROBE_V6` and `PROBE_PORT` to those endpoints. Before adding rules, each applicable probe below must succeed; retain the results and time. The container probe uses the same network configuration as the API without starting the application:

```bash
probe_container() {
  docker compose run --rm --pull never --no-deps --entrypoint python api -c \
    'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5).close()' \
    "$1" "$PROBE_PORT"
}
curl --noproxy '*' -4 -fsS --connect-timeout 5 --max-time 10 -o /dev/null "http://$PROBE_V4:$PROBE_PORT/"
curl --noproxy '*' -6 -fsS --connect-timeout 5 --max-time 10 -o /dev/null "http://[$PROBE_V6]:$PROBE_PORT/"
probe_container "$PROBE_V4"
probe_container "$PROBE_V6"
```

If a family is disabled, record interface, per-interface disable settings and route evidence from the host **and every relevant container network namespace** before marking its probes not applicable. A timeout or missing route alone is not proof of isolation. If a family is enabled and its positive control cannot be established, stop. These commands cover the standard Linux bridge deployment; any additional network driver or namespace needs the same verified isolation before recovery.

Add a dedicated `inet` table, with no interface restriction and no established-connection bypass. Its `output` hook blocks host traffic and its `forward` hook blocks Docker traffic, for both address families. Host loopback remains available. Inspect an existing table with this name instead of replacing it; never flush the host ruleset.

```bash
if sudo nft list table inet daas_restore_guard >/dev/null 2>&1; then
  echo 'Existing restore guard: inspect it before proceeding.' >&2
  exit 1
fi
sudo nft -f - <<'NFT'
table inet daas_restore_guard {
  chain output {
    type filter hook output priority -300; policy drop;
    oifname "lo" accept
    meta nfproto ipv4 counter drop
    meta nfproto ipv6 counter drop
  }
  chain forward {
    type filter hook forward priority -300; policy drop;
    meta nfproto ipv4 counter drop
    meta nfproto ipv6 counter drop
  }
}
NFT
sudo nft -a list table inet daas_restore_guard
```

Repeat **each applicable positive-control command unchanged**: each must now fail. Compare the rule counters before and after: host probes must increment the matching `output` counter and container probes the matching `forward` counter. Save both snapshots. A failed request without a matching counter increase does not prove the block. Stop on any unexpected success or missing evidence. Keep the rules for the entire recovery; do not reboot or change networking without reinstalling and rechecking the block. The rules above are not a persistent boot configuration.

### 5.2 Restore without starting services, then quarantine

Persist `DAAS_MAINTENANCE_MODE=true` in the private deployment configuration and export it in the operator shell. An inline assignment alone does not protect a later Compose recreation from another shell. Confirm the required images are already local; set `RESTORE_ARCHIVE` to the selected archive path, `EXPECTED_VERSION` to its compatible image version and `PUBLIC_ORIGIN` to the deployment's HTTPS origin. Then:

```bash
export DAAS_MAINTENANCE_MODE=true
./restore.sh "$RESTORE_ARCHIVE" --yes --no-start
DAAS_MAINTENANCE_MODE=true docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 90 api
docker compose exec -T api python -c \
  'import json,urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health"))["maintenance"] is True'
docker compose stop api
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine --dry-run --revoke-all
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine --revoke-all
```

Any failed command stops this procedure; keep ingress and egress closed. `restore.sh` refuses a failed pre-restore backup, failed stop, still-running API/web or failed SQLite integrity/foreign-key checks. Record its unique pre-restore archive path. Use `--no-pre-backup` only when the current data cannot be backed up; it explicitly removes the reconciliation source and requires the conservative reset. The backup may stop and restart the previous API, which is why the firewall is installed first. `--no-start` leaves the restored API and web stopped.

Run the CLI with API stopped so no sign-in or authorization request races the quarantine transaction. Dry-run changes neither rows nor audit events. Apply removes all sessions, consumes unused magic links and expires pending/approved OAuth codes. `--revoke-all` also revokes stored organization API keys, OAuth grants/tokens and dynamic clients, removes all unaccepted invitations, disables registered webhooks and schedule reminders, and clears schedule chat-webhook destinations. Output contains counts only; applied changes are audited as `restore.*`. This does not rotate deployment secrets or the self-hosted environment API key.

**D21b reconciliation is not available in this release. Use `--revoke-all` even when a pre-restore archive exists.** Before release, organization owners must confirm and correct restored accounts, memberships and roles using independent current records. Review deleted organizations/accounts, invitations, uploads/reports and outstanding deletion requests; remove any resurrected data through an approved recovery procedure. Review and cancel stale `queued` jobs before leaving maintenance. Also review automatic runs, run e-mail notifications and every notification destination; `notify_on_finish` and `auto_run_on_upload` are not reset by quarantine. If these checks or corrections cannot be completed, stay in maintenance with both network gates closed. Quarantine alone is not proof that restored authorization or data is current.

### 5.3 Local smoke test and release gates

Start only the API in maintenance and test from its own loopback; the host firewall intentionally blocks host-to-container and container-to-container traffic. Do not submit a sample job or send mail:

```bash
DAAS_MAINTENANCE_MODE=true docker compose up -d --no-build --pull never --wait --wait-timeout 90 api
docker compose exec -T api python - <<'PY'
import json
from urllib.error import HTTPError
from urllib.request import urlopen

base = "http://127.0.0.1:8000"
for path in ("/health", "/ready", "/api/v1/health"):
    with urlopen(base + path, timeout=5) as response:
        payload = json.load(response)
        assert response.status == 200
        if path == "/api/v1/health":
            assert payload["maintenance"] is True
try:
    urlopen(base + "/api/v1/me", timeout=5)
except HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("Unauthenticated access was accepted")
print("Maintenance API smoke OK")
PY
```

For a **drill**, stop here: retain isolation until the approved test host and its data are destroyed. Record elapsed restore time, image version, checksum, pre-restore path, firewall controls/counters, quarantine counts/audit and the smoke result. This is not a production release.

For **production recovery**, only after the owner/data/queue checks above and the maintenance smoke succeed, persist `DAAS_MAINTENANCE_MODE=false` in the deployment configuration. Recreate the API so the running process reads the new value; verify it before removing isolation. Then remove only this temporary table, start web, run the full local smoke, and finally reopen ingress:

```bash
export DAAS_MAINTENANCE_MODE=false
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 90 api
docker compose exec -T api python -c \
  'import json,urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health"))["maintenance"] is False'
sudo nft delete table inet daas_restore_guard
docker compose up -d --no-build --pull never web
./smoke.sh --wait 90 --expect-version "$EXPECTED_VERSION"
docker compose --profile edge up -d --no-build --pull never caddy
./smoke.sh --public "$PUBLIC_ORIGIN" --wait 90 --expect-version "$EXPECTED_VERSION"
```

With a shared proxy, replace the Caddy command with reopening the previously closed routes. Stop before reopening ingress if the local smoke fails. Record approval and the release time; users sign in again, and new keys/connections are issued only after their access has been confirmed.

## 6. Monitoring

Z zewnątrz (usługa monitoringu), co 5 minut, alarm po 2-3 kolejnych błędach:

| Kontrola | Adres | Oczekiwanie |
|---|---|---|
| gotowość API (baza i katalog danych) | `https://<domena>/api/v1/ready` | 200 i treść zawiera `ready` (503 = awaria) |
| żywotność API (to samo, co sprawdza `smoke.sh`) | `https://<domena>/api/v1/health` | 200 i treść zawiera `"status":"ok"` |
| kokpit | `https://<domena>/login` | 200 |
| certyfikat TLS | `<domena>:443` | alarm na 14 dni przed wygaśnięciem |
| kopie | `<adres heartbeat>` | wywołanie raz na dobę |

Przez Caddy publicznie działa wyłącznie to, co zaczyna się od `/api/` (poza metrykami), oraz kokpit. Adresy `/health` i `/ready` bez prefiksu `/api/v1` trafiają do kokpitu i nie nadają się do monitoringu; `/metrics` i `/api/v1/metrics` Caddy zwraca jako 404 (`Caddyfile`). Limit zapytań per IP to 300 na minutę (`DAAS_RATE_LIMIT_IP_PER_MIN`), więc kontrola co minutę mieści się z zapasem.

Na serwerze:

```bash
cd /opt/daas-engine/deploy
docker compose ps                                     # stan i healthcheck
df -h / && docker system df                           # miejsce na dysku
docker compose logs --since 24h api | grep -cE 'auth_denied|magic link e-mail failed|abandoned'
TOKEN="$(sed -n 's/^DAAS_METRICS_TOKEN=//p' .env)"
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics | head
```

Nie ładuj `.env` przez `source`: `DAAS_EMAIL_FROM` zawiera spacje i nawiasy ostre, powłoka by je zinterpretowała. Dzienniki kontenerów mają rotację `json-file`, 10 MB × 3 pliki na usługę (`compose.yml`), więc historia sięga kilku godzin do kilku dni zależnie od ruchu; w incydencie wyeksportuj je od razu (sekcja 9). Certyfikat odnawia Caddy, ale własna kontrola daty zostaje:

```bash
echo | openssl s_client -connect <domena>:443 -servername <domena> 2>/dev/null | openssl x509 -noout -enddate
```

Miejsce na dysku: alarm w panelu dostawcy lub kontrola tygodniowa; próg działania to 80% zajętości `/`. Największe pozycje to `backups/`, obrazy (`docker image ls`) i wolumeny (`docker system df -v`).

Dowód: wszystkie kontrole z tabeli są zielone, a wymuszona awaria testowa (`docker compose stop api` na czas dłuższy niż próg alarmu, w oknie serwisowym) wywołuje alarm i po `docker compose start api` wraca do zieleni; `docker compose ps` pokazuje usługi `healthy`.

## 7. Rytm pracy

Budżet: dziennie kilka minut, tygodniowo ok. 20, miesięcznie 1-1,5 godziny.

**Codziennie (ok. 5 min)**

- [ ] brak alarmów z monitoringu; heartbeat kopii odnotowany dziś
- [ ] `docker compose ps`: `api` i `web` mają status `healthy`, `caddy` działa
- [ ] `docker compose logs --since 24h api | grep -cE 'auth_denied|magic link e-mail failed|abandoned'` bez niespodzianek (`429` i `auth_denied` w ilościach większych niż zwykle to powód do zajrzenia, `../docs/SECURITY.md`, lista kontrolna, pkt 7)

**Co tydzień (ok. 20 min)**

- [ ] `df -h /` poniżej 80%; `ls -lh backups/` pokazuje dzienne archiwa
- [ ] `sha256sum -c` najnowszego archiwum i `restic snapshots --tag daas` z dzisiejszą migawką (4.4)
- [ ] `test -f /var/run/reboot-required && echo "potrzebny restart"`; restart w oknie serwisowym, potem `./smoke.sh --wait 90`
- [ ] `sudo journalctl -u ssh --since "7 days ago" | grep -c 'Failed'` (skoki prób logowania) i `last -a | head`
- [ ] nowe wydania i wpisy o bezpieczeństwie: `../CHANGELOG.md`, zgłoszenia zależności w repozytorium
- [ ] test poczty (2.4), jeśli w tygodniu zmieniano pocztę lub DNS

**Co miesiąc (ok. 1-1,5 h)**

- [ ] test odtworzenia na osobnej maszynie (sekcja 5), z notatką o czasie
- [ ] `restic check` oraz losowy fragment danych: `restic check --read-data-subset=5%`
- [ ] test poczty (2.4)
- [ ] aktualizacja systemu i Dockera: `sudo apt-get update && sudo apt-get upgrade`, potem `docker compose ps` i `./smoke.sh --public https://<domena> --wait 90`. Aktualizacja `docker-ce` restartuje demona, więc kontenery wstają od nowa (`restart: unless-stopped`) i zdarza się krótka niedostępność
- [ ] przegląd dostępu: klucze SSH, klucze API w organizacji (`GET /api/v1/orgs/{org_id}/api-keys`), członkowie (`GET /api/v1/orgs/{org_id}/members`), wygaśnięcie domeny
- [ ] retencja organizacji i okres trzymania kopii nadal zgodne z umową (sekcja 0)
- [ ] aktualizacja aplikacji, jeśli jest nowy tag z poprawkami (sekcja 8)

Dowód: lista odhaczona, a z ostatniego miesiąca istnieje notatka z testu odtworzenia i wpis o wykonanej aktualizacji lub decyzji, by jej nie robić.

## 8. Updates and rollback

Follow [`README.md`](README.md), update and rollback variants A/B. Read the intervening changelog for migrations, take the cold and off-host backup from section 4.3, record the archive path, and retain the previous local images. Notify customers of the maintenance window. Verify the running version with `smoke.sh --expect-version <version>` and repeat the mail-delivery check only after the approved release.

An image-only rollback is allowed only when the previous image supports the current database schema. Any rollback that restores data must use **all of section 5**, in order: close ingress; prove the host/forwarded IPv4 and IPv6 egress block; `restore.sh --no-start`; start API with persistent maintenance mode; stop API and apply quarantine; complete the owner/data/queue checks; maintenance smoke; recreate API with maintenance off; remove the temporary block; full local smoke; reopen ingress and public smoke. Do not use an older image that lacks maintenance/quarantine support for this procedure.

Until D21b is delivered, reconciliation with the pre-restore archive remains unavailable: `--revoke-all` and owners' confirmation/correction of memberships and roles are required even when that archive exists. Deleted data and stale queued work also need review before release. Data written after the restored backup is absent from that backup; preserve the unique pre-restore copy for controlled recovery and inform affected customers. Evidence is the isolated recovery record from section 5, the restored running version and the local/public smoke results, not merely a successful archive extraction.

## 9. Incydent

Podejrzenie włamania, wycieku klucza, utraty danych albo niedostępności dłuższej niż okno serwisowe. Kolejność ma znaczenie: najpierw dowody, potem zmiany.

1. **Zacznij dziennik zdarzeń** (godzina UTC, co zauważono, co zrobiono). Nie wykonuj `docker compose up -d` po zmianie `.env`, `down` ani `rm` przed zabezpieczeniem logów: odtworzony kontener traci swój dziennik.
2. **Zabezpiecz logi i stan:**

   ```bash
   cd /opt/daas-engine/deploy
   d=~/incydent-$(date -u +%Y%m%dT%H%M%SZ) && mkdir -m 700 "$d"
   for s in api web caddy; do docker compose logs --no-color --timestamps "$s" > "$d/$s.log"; done
   docker compose ps > "$d/ps.txt"
   sudo journalctl -u ssh --since "14 days ago" > "$d/ssh.log"
   last -a | head -n 50 > "$d/last.txt"
   ./backup.sh && cp -a backups/"$(ls -1t backups | grep '\.tar\.gz$' | head -n 1)" "$d"/
   ```

   Kopia zimna (`./backup.sh`) zatrzymuje `api` na czas `tar`; jeśli usługa ma działać bez przerwy, użyj `./backup.sh --online` (bez gwarancji spójności DuckDB).

   Ślad operacji organizacji jest też w API: `GET /api/v1/orgs/{org_id}/audit?limit=200` (zakres `audit:read`, kolejne strony przez `next_cursor`). Jeśli host jest przejęty, zrób migawkę dysku w panelu dostawcy i pracuj na kopii, nie na żywym serwerze.
3. **Unieważnij klucze API**: `GET /api/v1/orgs/{org_id}/api-keys`, potem `DELETE /api/v1/orgs/{org_id}/api-keys/{key_id}` (zakres `keys:manage`; unieważnienie działa natychmiast). Jeśli to właśnie ten klucz jest podejrzany, usuń go jego własnym uprawnieniem, a nowy wystaw w kokpicie z sesji właściciela. Przejrzyj też członków i zaproszenia organizacji.
4. **Rotuj sekrety**, gdy mogły wyciec `.env` lub dostęp do hosta. Nowy `DAAS_SECRET_KEY`:

   ```bash
   openssl rand -hex 32                                  # wartość wklej do .env przez $EDITOR
   docker compose up -d                                  # tworzy kontenery od nowa z nową konfiguracją
   ```

   Skutki: wszyscy użytkownicy są wylogowani, a sekrety podpisu webhooków przestają obowiązywać (są z niego wyprowadzone), więc klient musi utworzyć webhooki na nowo (`../docs/SECURITY.md`, „Known limitations”). Klucze API tego nie odczuwają. Zaktualizuj `.env` w menedżerze haseł. Zmień też hasło SMTP, `DAAS_METRICS_TOKEN`, klucze dostępu do magazynu kopii i klucze SSH.
5. **Przywróć czyste środowisko**, jeśli integralność hosta jest wątpliwa: nowy serwer (sekcje 1-3), dane z kopii sprzed incydentu (sekcja 5, ale na docelowym hoście), nowe sekrety.
6. **Powiadom klienta bez zbędnej zwłoki** (umowa powierzenia, `../docs/DPA_v1_PL.md` §4 pkt 5; to szkic do przeglądu prawnego, więc konkretny termin w godzinach ustal w podpisanej umowie). Wiadomość: kiedy wykryto, co wiadomo o zakresie (jakie organizacje i kategorie danych), co już zrobiono, co klient ma zrobić (np. odtworzyć webhooki, zmienić hasła do swoich systemów), kontakt do dalszych ustaleń. Pisz o faktach, nie o domysłach, i uzupełniaj wiadomość, gdy ustalenia się zmienią.
7. **Zamknij incydent**: przyczyna, oś czasu, poprawki, zmiany w tym runbooku. Zgłoszenia podatności w samym oprogramowaniu: zgodnie z `../docs/SECURITY.md` („Reporting a vulnerability”).

Dowód: katalog `~/incydent-<znacznik>` zawiera logi `api`, `web`, `caddy`, `ps.txt` i archiwum kopii; stare klucze API dają `401` (`curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: <stary klucz>" https://<domena>/api/v1/me`); po rotacji `smoke.sh` przechodzi, a notatka klienta ma datę i godzinę wysyłki.

## 10. Zakończenie pilota i usunięcie danych

Usunięcie danych klienta ma kilka warstw: dane aplikacji, kopie lokalne, kopie poza serwerem, maszyna testowa. Nie pomijaj żadnej, jeśli umowa wymaga usunięcia.

1. **Uzgodnij z klientem** zakres: zwrot, usunięcie albo oba (umowa powierzenia §6: dane są „usuwane lub zwracane, według wyboru Administratora”) oraz termin.
2. **Eksport dla klienta** (przed usunięciem; raporty starsze niż retencja zwracają `410 report_expired`). Klucz API klienta z zakresami `runs:read` i `reports:read` (oraz `audit:read`, jeśli eksportujesz audyt). W API nie ma zbiorczego eksportu ani pobierania wgranych plików, tylko raporty:

   ```bash
   export DAAS=https://<domena> ORG=<org_id>; read -rs KEY; export KEY
   mkdir -p ~/eksport && cd ~/eksport
   curl -fsS -G -H "X-API-Key: $KEY" --data-urlencode "limit=100" "$DAAS/api/v1/orgs/$ORG/runs" -o runs.json
   jq -r '.items[].run_id' runs.json | while read -r id; do
     curl -fsS -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/runs/$id/report?fmt=html" -o "raport-$id.html" || echo "pominięto $id"
   done
   ```

   Kolejna strona listy: wartość `next_cursor` z `runs.json` (ma postać `<czas>|<run_id>` i może zawierać `+`) podaj przez `--data-urlencode "cursor=<next_cursor>"`, nie wklejaj jej surowo w adres (inaczej `422 invalid_cursor`); powtarzaj, aż `next_cursor` będzie `null`. Przekaż paczkę kanałem uzgodnionym z klientem, potem usuń ją z serwera. Pobierz też `GET .../audit`, jeśli klient go potrzebuje (kursor tak samo przez `--data-urlencode`).
3. **Sprawdź, co zrobiłaby retencja** (nie jest to podgląd usunięcia organizacji, tylko liczniki przeterminowanych uploadów i raportów):

   ```bash
   cd /opt/daas-engine/deploy
   docker compose exec api python -m app.cli purge --dry-run --org <org_id>
   ```

   Oczekiwany wynik: linia `dry-run org=<org_id> uploads=… reports=… files=… rows=…` i `organizations processed: 1`. Dopiero gdy liczby są zrozumiałe, ewentualne wykonanie właściwe to to samo polecenie bez `--dry-run`. Polecenie otwiera bazę DuckDB organizacji równolegle z działającym `api`; połączenia są krótkie, ale przy błędzie blokady pliku powtórz je po chwili.
4. **Usuń organizację.** `DELETE /api/v1/orgs/{org_id}` wymaga **sesji właściciela** (klucz API nie ma zakresu `org:delete`), nie ma przycisku w kokpicie, a `409 org_busy` oznacza trwające zadania (sprawdź `GET .../jobs?status=running`, poczekaj lub anuluj kolejkowane). Usuwa rekordy control plane i katalog danych organizacji. Sesję zdobywa właściciel (albo operator, jeśli właścicielem jest jego adres) przez link logowania:

   ```bash
   curl -sS -c jar.txt -X POST "$DAAS/api/v1/auth/magic-link" -H 'Content-Type: application/json' -d '{"email":"wlasciciel@example.com"}'
   # z e-maila: wartość po ?token= w linku …/login/verify?token=<token>
   curl -sS -b jar.txt -c jar.txt -X POST "$DAAS/api/v1/auth/magic-link/verify" -H 'Content-Type: application/json' -d '{"token":"<token>"}'
   CSRF="$(awk '$6=="daas_csrf"{print $7}' jar.txt)"
   curl -sS -b jar.txt -X DELETE -H "X-CSRF-Token: $CSRF" "$DAAS/api/v1/orgs/$ORG" -w '%{http_code}\n'   # 204
   shred -u jar.txt 2>/dev/null || rm -f jar.txt
   ```

   Jeśli właścicielem jest klient, uzgodnij z nim, kto wykonuje te wywołania i na czyim ekranie. Po organizacji **zostaje rekord użytkownika** (adres e-mail i imię, jeśli podano) w control.db: kod usuwa organizację z jej powiązaniami, ale nie usuwa kont użytkowników, a polecenia do tego w `python -m app.cli` nie ma. Wpisz tę informację do potwierdzenia dla klienta albo ustal z nim inne rozwiązanie przed zakończeniem pilota.
5. **Kopie.** Retencja organizacji i usunięcie organizacji nie dotykają kopii zapasowych (`../docs/SECURITY.md`; umowa powierzenia §6: kopie usuwane „zgodnie z rotacją”). Aby skrócić ten czas, zrób świeżą kopię już po usunięciu, a starsze archiwa skasuj ręcznie. Przy jednym kliencie pilota dotyczy to wszystkich archiwów, przy większej liczbie klientów najpierw zastanów się, co jeszcze w nich leży:

   ```bash
   ./backup.sh                                           # kopia po usunięciu organizacji
   ls -1 backups/ | grep '\.tar\.gz$'                    # wskaż starsze archiwa
   rm -i backups/daas-<data UTC>.tar.gz backups/daas-<data UTC>.tar.gz.sha256 backups/daas-<data UTC>.tar.gz.meta
   ( . ~/.config/daas-backup.env && restic snapshots --tag daas && restic forget <id migawki> --prune )
   ```

   Do tego dochodzą: ostatni test odtworzenia (maszyna zniszczona, sekcja 5), wpis „klucz API” i `org_id` w menedżerze haseł, pobrana paczka eksportu. Dzienniki kontenerów wygasają same przez rotację (10 MB × 3).
6. **Potwierdzenie dla klienta** zgodnie z umową powierzenia §6: data i godzina usunięcia, co usunięto (dane aplikacji, kopie lokalne i zdalne, kopie testowe), co wygasa dopiero z rotacją i kiedy. Dokument DPA to szkic do przeglądu prawnego: treść potwierdzenia uzgodnij według podpisanej wersji, nie według tego szkicu, i nie opisuj usługi jako spełniającej konkretne przepisy.
7. **Serwer.** Gdy pilot się kończy całkowicie: wyłącz monitoring i heartbeat, usuń rekordy DNS, wyczyść lub zniszcz dysk VPS w panelu dostawcy, zamknij konta usług i unieważnij klucze (SSH, magazyn kopii, SMTP).

Dowód: `DELETE` zwrócił `204`; stary klucz klienta daje `401` na `/api/v1/me`; `docker compose exec api ls /data/runtime/tenants` nie pokazuje `<org_id>`; `docker compose exec api python -m app.cli purge --dry-run --org <org_id>` kończy się błędem `organization <org_id> not found` (kod wyjścia 1); lista archiwów w `backups/` i `restic snapshots` zawiera tylko kopie zrobione po usunięciu; klient ma potwierdzenie.

## 11. Odnośniki

- [`README.md`](README.md): instalacja, poczta, aktualizacja, rollback, kopie, wspólne proxy, zmienne, operacje
- [`../docs/DEPLOY.md`](../docs/DEPLOY.md): pełna lista zmiennych `DAAS_*` i układ danych
- [`../docs/SECURITY.md`](../docs/SECURITY.md): model zagrożeń, retencja, znane ograniczenia, lista kontrolna operatora
- [`../docs/API.md`](../docs/API.md): trasy, zakresy kluczy, uwierzytelnianie sesją i CSRF
- [`../docs/DPA_v1_PL.md`](../docs/DPA_v1_PL.md): szkic umowy powierzenia (do przeglądu prawnego)
- [`../docs/pilot/ONBOARDING_CHECKLIST_PL.md`](../docs/pilot/ONBOARDING_CHECKLIST_PL.md) i [`../docs/pilot/SECURITY_SCOPE_PL.md`](../docs/pilot/SECURITY_SCOPE_PL.md): wdrożenie klienta i zakres bezpieczeństwa dla pilota
- [`../CHANGELOG.md`](../CHANGELOG.md): zmiany między wersjami i wpisy o migracjach
- Pliki w tym katalogu: [`compose.yml`](compose.yml), [`Caddyfile`](Caddyfile), [`gen-env.sh`](gen-env.sh), [`backup.sh`](backup.sh), [`restore.sh`](restore.sh), [`smoke.sh`](smoke.sh)

Dowód: każdy odnośnik względny z tej listy wskazuje istniejący plik (w katalogu `deploy/` polecenie `test -e <ścieżka>` zwraca 0 dla każdej z nich).
