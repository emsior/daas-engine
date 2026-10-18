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

## 5. Próba odtworzenia i odtworzenie w izolacji (co miesiąc)

Comiesięczną próbę wykonuj na **osobnym, zatwierdzonym hoście**, w tym samym regionie i na tych samych zasadach przetwarzania danych co produkcja. Zgodne obrazy aplikacji, Compose, `nft`, `curl`, archiwum z plikiem `.sha256` oraz prywatną konfigurację wdrożenia przygotuj **przed izolacją**. Wszystkie poniższe polecenia wykonuj z katalogu `deploy/`; podczas izolacji nie pobieraj obrazów, nie buduj ich i nie instaluj pakietów. Próba na osobnym hoście oraz odtworzenie na nowym hoście po utracie serwera wymagają `restore.sh --no-pre-backup` i ścieżki §5.4: nie ma tam bieżących danych odtwarzanego wdrożenia. Przy odtwarzaniu na tym samym hoście zachowaj kopię pre-restore oraz jej sumę wypisane przez `restore.sh` w tym przebiegu; tylko taki stan jest źródłem uzgodnienia z §5.3.

### 5.1 Zamknięcie wejścia i potwierdzenie blokady wyjścia

Najpierw zamknij wszystkie publiczne drogi wejścia. Przy dołączonym proxy wykonaj `docker compose --profile edge stop caddy`; przy wspólnym proxy przełącz trasy DaaS w tryb serwisowy i sprawdź, że dostęp publiczny jest zamknięty. Poniższą zmianę zapory całego hosta wykonuj z konsoli dostawcy: blokuje ona także odpowiedzi SSH i ruch wychodzący innych usług. Zatrzymanie Caddy nie blokuje wiadomości wychodzących.

Użyj kontrolowanych przez operatora punktów testowych HTTP z osiągalnymi **dosłownymi adresami IPv4 i IPv6**, bez nazw DNS. Ustaw `PROBE_V4`, `PROBE_V6` oraz `PROBE_PORT` na te punkty. Przed dodaniem reguł każda mająca zastosowanie próba poniżej musi się udać; zapisz wyniki i czas. Próba kontenerowa korzysta z takiej samej konfiguracji sieci jak API, ale nie uruchamia aplikacji:

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

Jeśli rodzina adresów jest wyłączona, zanim oznaczysz jej próby jako niemające zastosowania, zapisz interfejsy, ustawienia wyłączenia na każdym interfejsie i trasy hosta **oraz każdej właściwej kontenerowej przestrzeni nazw sieci**. Sam timeout albo brak trasy nie dowodzi izolacji. Jeżeli rodzina jest włączona, a kontrola dodatnia nie działa, przerwij. Te polecenia obejmują standardowe wdrożenie z mostem sieciowym Linuksa; każdy dodatkowy sterownik lub przestrzeń nazw wymaga tak samo potwierdzonej izolacji przed odtworzeniem.

Dodaj osobną tabelę `inet`, bez ograniczenia do jednego interfejsu i bez wyjątku dla już zestawionych połączeń. Jej punkt zaczepienia `output` blokuje ruch hosta, a `forward` ruch Dockera, dla obu rodzin adresów. Pętla zwrotna hosta pozostaje dostępna. Jeśli tabela o tej nazwie już istnieje, sprawdź ją zamiast zastępować; nigdy nie opróżniaj całego zestawu reguł hosta.

```bash
if sudo nft list table inet daas_restore_guard >/dev/null 2>&1; then
  echo 'Blokada odtworzenia juz istnieje: sprawdz ja przed kontynuacja.' >&2
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

Powtórz **bez zmian każde mające zastosowanie polecenie kontroli dodatniej**: teraz każde musi zakończyć się błędem. Porównaj liczniki reguł przed próbą i po niej: próby hosta muszą zwiększać właściwy licznik `output`, a próby kontenerowe właściwy licznik `forward`. Zachowaj oba odczyty. Nieudane żądanie bez wzrostu odpowiedniego licznika nie potwierdza blokady. Przerwij przy niespodziewanym sukcesie lub brakującym dowodzie. Utrzymuj reguły przez całe odtworzenie; restart hosta albo zmiana sieci wymagają ponownego założenia i sprawdzenia blokady. Powyższe reguły nie są trwałą konfiguracją startową.

### 5.2 Odtworzenie bez uruchamiania usług i kwarantanna

Zapisz `DAAS_MAINTENANCE_MODE=true` w prywatnej konfiguracji wdrożenia i wyeksportuj tę zmienną w powłoce operatora. Przypisanie tylko przy jednym poleceniu nie ochroni późniejszego odtworzenia kontenera przez Compose w innej powłoce. Sprawdź, że potrzebne obrazy są już lokalnie. Ustaw `RESTORE_ARCHIVE` na wybrane archiwum, `EXPECTED_VERSION` na zgodną wersję obrazu, a `PUBLIC_ORIGIN` na publiczny adres HTTPS wdrożenia. Poniższe wywołanie `restore.sh` dotyczy **tego samego hosta z bieżącymi danymi wdrożenia**. Na nowym hoście po utracie serwera i podczas comiesięcznej próby zastąp je `./restore.sh "$RESTORE_ARCHIVE" --yes --no-start --no-pre-backup`, a po kwarantannie przejdź do §5.4. Następnie:

```bash
export DAAS_MAINTENANCE_MODE=true
./restore.sh "$RESTORE_ARCHIVE" --yes --no-start
DAAS_MAINTENANCE_MODE=true docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 90 api
docker compose exec -T api python -c \
  'import json,urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health"))["maintenance"] is True'
docker compose stop api
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine --dry-run
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine
```

Błąd dowolnego polecenia zatrzymuje bieżący etap; wejście i wyjście pozostają zamknięte. `restore.sh` odmawia kontynuacji po nieudanej kopii pre-restore, nieudanym zatrzymaniu, przy działającym API/web lub błędzie spójności SQLite albo kluczy obcych. Na tym samym hoście zapisz wypisaną w tym przebiegu unikalną ścieżkę kopii pre-restore. Na nowym hoście i podczas comiesięcznej próby nawet czytelna, zmigrowana baza utworzona przez wcześniejsze uruchomienie stosu nie jest bieżącym stanem odtwarzanego wdrożenia: stosuj `--no-pre-backup` i §5.4. Tak samo postępuj, gdy bieżących danych nie da się skopiować. Kopia może zatrzymać i uruchomić poprzednie API, dlatego zapora musi działać wcześniej. `--no-start` pozostawia odtworzone API i web zatrzymane.

Uruchamiaj polecenia CLI przy zatrzymanym API, żeby logowanie lub wydawanie uprawnień nie ścigało się z transakcją. Podgląd `--dry-run` nie zmienia wierszy ani audytu. Kwarantanna usuwa wszystkie sesje, oznacza nieużyte magic linki jako wykorzystane i wygasza kody OAuth `pending`/`approved`; zwraca same liczby, a zmiany zapisuje jako zdarzenia `restore.*`. Nie zmienia sekretów wdrożenia ani klucza API trybu selfhost ze zmiennej środowiskowej. Teraz wybierz jedną z dwóch ścieżek poniżej.

### 5.3 Ten sam host i kopia pre-restore z tego przebiegu: uzgodnienie

Tę ścieżkę stosuj wyłącznie przy przywracaniu na **tym samym hoście**, z kopią pre-restore wypisaną przez `restore.sh` **w tym przebiegu**. `restore-reconcile --pre` porównuje odtworzoną bazę z `control/control.db` z tej kopii. Najpierw sprawdza plik `.sha256`, odczytuje potrzebne kolumny bazy tylko do odczytu w katalogu tymczasowym i sprząta po sobie. Dodatkowe kolumny nowszego schematu są pomijane; brak potrzebnej kolumny oznacza błąd. Jeśli odtworzona baza zawiera organizacje, ale kopia pre nie zawiera żadnego wspólnego identyfikatora organizacji (także gdy jest zmigrowana i pusta), kontrola pochodzenia odrzuca ją zarówno w podglądzie, jak i z `--apply`: błąd bez wartości, bez zmian i bez audytu, z odesłaniem do §5.4. Częściowe pokrycie identyfikatorów pozostawia dotychczasowe uzgodnienie. Wspólny identyfikator nie zastępuje sprawdzenia hosta i pochodzenia archiwum przez operatora. Domyślne wywołanie tylko raportuje, a `--apply` stosuje zmiany w jednej transakcji. Pliki runtime usuwa dopiero po zatwierdzeniu transakcji.

Ustaw `PRE_RESTORE` na dokładną ścieżkę kopii wypisaną przez `restore.sh` w tym przebiegu. Kontener API działa jako UID `10001`, a archiwa mają prawa `0600` operatora, więc sam montaż katalogu może nie dać dostępu. Przygotuj prywatną kopię archiwum i sumy dostępną dla tego UID, zachowując nazwy plików; nie udostępniaj ich wszystkim użytkownikom. Poniższy katalog jest na hoście, a `/backups` wyłącznie w kontenerze:

```bash
PRE_BACKUP_NAME="$(basename "$PRE_RESTORE")"
PRE_BACKUP_DIR="$(sudo mktemp -d /var/tmp/daas-pre-restore.XXXXXX)"
sudo install -o 10001 -m 0400 "$PRE_RESTORE" "$PRE_BACKUP_DIR/$PRE_BACKUP_NAME"
sudo install -o 10001 -m 0400 "$PRE_RESTORE.sha256" "$PRE_BACKUP_DIR/$PRE_BACKUP_NAME.sha256"
sudo chown 10001 "$PRE_BACKUP_DIR"
sudo chmod 0500 "$PRE_BACKUP_DIR"
docker compose run --rm --pull never --no-deps \
  --volume "$PRE_BACKUP_DIR:/backups:ro" api python -m app.cli restore-reconcile \
  --pre "/backups/$PRE_BACKUP_NAME"
```

**Właściciel przegląda raport przed zastosowaniem.** Wynik zawiera liczby kategorii i identyfikatory ULID, bez e-maili, URL-i i sekretów. Uzgodnienie usuwa obiekty nieobecne w kopii pre-restore, obniża role, cofa unieważnione poświadczenia, usuwa nieaktualne zaproszenia, ogranicza webhooki i harmonogramy oraz usuwa przywrócone uploady, rekordy runów i raporty, które wcześniej usunięto. Zatrzymuje nieaktualne zadania `queued`; przywraca ograniczenia powiadomień i automatycznych uruchomień. Nie importuje brakujących obiektów; jedynym dopuszczalnym podwyższeniem roli jest jawne rozwiązanie konfliktu właścicieli opisane poniżej. Różnice planu, retencji i nazw pozostają do oceny w raporcie.

Brak bezpiecznego właściciela organizacji oznacza konflikt: podgląd wypisuje identyfikator organizacji oraz identyfikatory właścicieli z pre, którzy mają członkostwo w odtworzonej bazie, bez e-maili. Nierozwiązany konflikt blokuje całe `--apply` **bez zapisania żadnej części uzgodnienia**. Po potwierdzeniu właściwej osoby z właścicielem organizacji klienta operator może jawnie dodać `--resolve-owner <org_id>:<user_id>` do `--apply`; opcję można powtórzyć dla kolejnych konfliktów. Wskazana organizacja musi mieć konflikt, osoba musi być właścicielem w pre, a jej konto i członkostwo w odtworzonej bazie muszą pozostać po uzgodnieniu. Błędny identyfikator lub niespełniony warunek powoduje błąd bez zmian. Rola `owner` jest nadawana w tej samej transakcji co pozostałe zmiany i zapisywana jako `restore.reconcile.owner_resolved` z identyfikatorami ULID.

Jeśli właściwego właściciela nie ma jako członka w odtworzonej bazie, **utrzymuj izolację i przekaż decyzję właścicielowi platformy**. Polecenia odzyskiwania nie mają narzędzia do rozwiązania tego przypadku; `restore-members` nie dodaje członków ani nie podwyższa ról. Nie obchodź konfliktu ścieżką bez kopii. Jeśli samo archiwum jest nieczytelne, uszkodzone, nie ma wymaganej struktury lub kontrola pochodzenia odrzuca jego historię, przerwij ten etap i przejdź świadomie do §5.4; nie pomijaj kontroli sumy. Po zatwierdzeniu poprawnego raportu bez konfliktów przez właściciela:

```bash
docker compose run --rm --pull never --no-deps \
  --volume "$PRE_BACKUP_DIR:/backups:ro" api python -m app.cli restore-reconcile \
  --pre "/backups/$PRE_BACKUP_NAME" --apply
```

Przy potwierdzonym konflikcie ustaw `ORG_ID` i `USER_ID` na zweryfikowane identyfikatory i zamiast powyższego wywołania zastosuj:

```bash
docker compose run --rm --pull never --no-deps \
  --volume "$PRE_BACKUP_DIR:/backups:ro" api python -m app.cli restore-reconcile \
  --pre "/backups/$PRE_BACKUP_NAME" --apply --resolve-owner "$ORG_ID:$USER_ID"
```

Każda zastosowana akcja ma zdarzenie `restore.reconcile.*`. Niezerowy kod wyjścia po błędzie usuwania plików może oznaczać, że transakcja bazy już się zatwierdziła: utrzymaj izolację, sprawdź pozostałe pliki i dokończ zatwierdzone usuwanie przed otwarciem. Samo ponowienie polecenia nie gwarantuje sprzątnięcia plików, których metadane zostały już usunięte. Zachowaj raport oraz kopię pre-restore zgodnie z zasadami przechowywania kopii; prywatny katalog pomocniczy zawiera te same dane i też podlega tym zasadom.

### 5.4 Nowy host, próba miesięczna lub brak właściwej kopii: reset i korekta członkostw

Na nowym hoście po utracie serwera oraz podczas comiesięcznej próby na osobnym hoście zawsze wybierz tę ścieżkę, używając w §5.2 `restore.sh --no-pre-backup`: bieżących danych tego wdrożenia tam nie ma, nawet gdy stos zdążył utworzyć pustą, zmigrowaną bazę. Dotyczy to także braku, nieczytelnej lub uszkodzonej kopii pre-restore, nieudanej weryfikacji i odrzucenia kopii z innej historii. Nie zgaduj stanu sprzed odtworzenia. Przy nadal zatrzymanym API zastosuj pełny reset:

```bash
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine --dry-run --revoke-all
docker compose run --rm --pull never --no-deps api python -m app.cli restore-quarantine --revoke-all
docker compose run --rm --pull never --no-deps api python -m app.cli restore-members
```

`--revoke-all` dodatkowo unieważnia zapisane klucze API organizacji, granty i tokeny OAuth oraz klientów dynamicznych, usuwa nieprzyjęte zaproszenia, wyłącza webhooki i przypomnienia oraz czyści adresy webhooków powiadomień o runach. Anuluje wszystkie zadania `queued` bez wykonania (`error_code=restore_quarantine`, z czasem zakończenia), wyłącza `notify_on_finish` i `auto_run_on_upload`. Podgląd liczy także te kategorie bez zmian; zdarzenia `restore.jobs_cancelled`, `restore.run_notifications_disabled` i `restore.auto_runs_disabled` zawierają same liczby. Tryb bez `--revoke-all` pozostaje ograniczony do sesji i poświadczeń jednorazowych.

Wynik `restore-members` bez akcji jest tylko do odczytu: identyfikator organizacji i użytkownika, rola, data członkostwa i data utworzenia konta. **Przejrzyj go z właścicielem organizacji klienta**, korzystając z niezależnej ewidencji. Jeśli identyfikacja wymaga e-maili, dodaj `--show-emails` wyłącznie na terminalu operatora; nie przekierowuj tego wyniku do dziennika ani raportu operacji. E-maile nie trafiają do audytu ani logu aplikacji.

Ustaw `ORG_ID` i `USER_ID` na zweryfikowane identyfikatory ULID. Każda korekta działa od razu, w jednej transakcji. Wybierz odpowiednią akcję dla konkretnej osoby:

```bash
# Usuniecie czlonkostwa, takze jednego z kilku wlascicieli:
docker compose run --rm --pull never --no-deps api python -m app.cli restore-members \
  --org "$ORG_ID" --user "$USER_ID" --remove
```

```bash
# Alternatywnie: obnizenie roli do member (albo admin):
docker compose run --rm --pull never --no-deps api python -m app.cli restore-members \
  --org "$ORG_ID" --user "$USER_ID" --demote-to member
```

Usunięcie lub obniżenie ostatniego właściciela oraz podwyższenie roli kończą się błędem bez zmian. Korekty zapisują `restore.member_removed` lub `restore.member_demoted` z identyfikatorami, bez e-maili. Polecenie nie dodaje członków i nie nadaje wyższych ról: zatwierdzone uprawnienia właściciel nadaje ponownie w aplikacji po otwarciu. Powtórz listowanie i potwierdź wynik z właścicielem.

Brak kopii pre-restore oznacza brak automatycznego dowodu, które konta, organizacje, uploady i raporty wcześniej usunięto. Właściciel odtwarza żądania usunięcia danych z niezależnej ewidencji; przed otwarciem trzeba zweryfikować dane i wykonać wymagane korekty zatwierdzoną procedurą. `restore-members` koryguje członkostwa, nie usuwa kont ani danych klienta. Jeżeli tych kontroli i korekt nie da się ukończyć, pozostań w trybie serwisowym z zamkniętym wejściem i wyjściem.

### 5.5 Lokalny test dymny i warunki otwarcia

Uruchom tylko API w trybie serwisowym i testuj przez jego własną pętlę zwrotną; zapora hosta celowo blokuje ruch host–kontener i kontener–kontener. Nie zgłaszaj próbnego zadania i nie wysyłaj poczty:

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
    raise AssertionError("Zaakceptowano dostep bez uwierzytelnienia")
print("Test API w trybie serwisowym OK")
PY
```

W **próbie odtworzenia** zakończ tutaj: utrzymuj izolację do zatwierdzonego usunięcia hosta testowego i jego danych. Zapisz czas odtworzenia, wersję obrazu, sumę kontrolną, ścieżkę pre-restore lub przyczynę jej braku, kontrole i liczniki zapory, wyniki i audyt kwarantanny oraz uzgodnienia albo korekt członkostw, a także wynik testu dymnego. To nie jest otwarcie produkcji.

Przy **odtworzeniu produkcji**, dopiero po zakończeniu kontroli właścicieli, danych i kolejki oraz udanym teście w trybie serwisowym, zapisz `DAAS_MAINTENANCE_MODE=false` w konfiguracji wdrożenia. Odtwórz kontener API, aby proces odczytał nową wartość, i sprawdź ją przed zdjęciem izolacji. Następnie usuń tylko tymczasową tabelę zapory, uruchom web, wykonaj pełny lokalny test dymny i na końcu otwórz wejście:

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

Przy wspólnym proxy zastąp polecenie Caddy ponownym otwarciem wcześniej zamkniętych tras. Jeśli lokalny test dymny się nie uda, nie otwieraj wejścia. Zapisz zatwierdzenie i czas otwarcia; użytkownicy logują się ponownie, a nowe klucze i połączenia wydawaj dopiero po potwierdzeniu uprawnień. Obiekty i uprawnienia utworzone po odtwarzanej kopii nie są importowane przez uzgodnienie; wymagają osobnego, zatwierdzonego odtworzenia.

Dowód: zapis izolacji, wyniki kwarantanny oraz wybranej ścieżki korekt, potwierdzenie właściciela i udany test dymny; przy otwarciu produkcji także potwierdzony stan `maintenance: false` i udane testy lokalne oraz publiczne.

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

## 8. Aktualizacje i przywracanie poprzedniej wersji

Postępuj według [`README.md`](README.md): aktualizacja oraz warianty A/B przywrócenia poprzedniej wersji. Przeczytaj wpisy o migracjach między wersjami, wykonaj zimną kopię i kopię poza serwerem z §4.3, zapisz ścieżkę archiwum i zachowaj poprzednie obrazy lokalnie. Poinformuj klientów o oknie serwisowym. Sprawdź działającą wersję przez `smoke.sh --expect-version <wersja>`, a dostarczanie poczty ponownie sprawdź dopiero po zatwierdzonym otwarciu.

Powrót wyłącznie do poprzedniego obrazu jest dozwolony tylko wtedy, gdy obsługuje on bieżący schemat bazy. Przywrócenie obejmujące dane wymaga **całej §5**, w kolejności: zamknięcie wejścia; potwierdzona blokada wyjścia hosta i ruchu przekazywanego dla IPv4 i IPv6; `restore.sh --no-start` (nowy host i próba miesięczna także `--no-pre-backup`); start API z trwale ustawionym trybem serwisowym; zatrzymanie API i kwarantanna; wyłącznie na tym samym hoście z właściwą kopią pre-restore z tego przebiegu podgląd `restore-reconcile --pre`, przegląd właściciela i `--apply` z ewentualnym potwierdzonym `--resolve-owner`, a w pozostałych przypadkach §5.4: `restore-quarantine --revoke-all` i przegląd oraz korekty przez `restore-members`; test dymny w trybie serwisowym; odtworzenie kontenera API z wyłączonym trybem serwisowym; zdjęcie tymczasowej blokady; pełny lokalny test dymny; otwarcie wejścia i test publiczny. Nie używaj do tej procedury starszego obrazu bez obsługi trybu serwisowego, kwarantanny i poleceń korekty.

Uzgodnienie nie importuje obiektów; jedyny wyjątek od zakazu podwyższania ról to jawne `--resolve-owner` według §5.3. Bez właściwej kopii pre-restore właściciel odtwarza także ewidencję żądań usunięcia danych i potwierdza korekty przed otwarciem. Zachowaj unikalną kopię pre-restore do kontrolowanego odzyskiwania zmian i powiadom klientów o utracie danych. Nierozwiązany konflikt właścicieli, nieukończone usuwanie plików albo niepotwierdzony stan danych oznaczają dalszą izolację.

Dowód: zapis odtworzenia w izolacji z §5, wersja działającej aplikacji oraz wyniki lokalnego i publicznego testu dymnego; samo udane rozpakowanie archiwum nie wystarcza.

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
