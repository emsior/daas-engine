# Obserwowalność DaaS Engine (Prometheus, Alertmanager, node_exporter, Grafana)

Stos metryk i alertów dla jednego VPS (2 vCPU / 4 GB), neutralny wobec dostawcy. Cele i progi: [`../SLO.md`](../SLO.md), alerty i postępowanie: [`../ALERTS.md`](../ALERTS.md). Nakładka compose: [`../compose.observability.yml`](../compose.observability.yml). Bez Loki, bez tracingu i bez agentów zewnętrznych: jeden proces, logi JSON z `request_id` w `docker compose logs`.

## 1. Co jest w stosie

| Usługa | Rola | Port (tylko `127.0.0.1`) | `mem_limit` |
|---|---|---|---|
| `prometheus` | scrape `api:8000/metrics` (Bearer z pliku), `node_exporter`, siebie, Alertmanager; reguły; retencja 35 dni lub 2 GB | 9090 | 320 MB |
| `alertmanager` | e-mail (`[PILNE]` dla `page`, `[ZADANIE]` dla `ticket`), heartbeat dla `Watchdog`, tłumienie | 9093 | 64 MB |
| `node_exporter` | CPU, pamięć, load, dysk `/`, `oom_kill`, pliki textfile z crona | 9100 | 64 MB |
| `grafana` (profil `dash`, na żądanie) | dashboard `DaaS Engine: przegląd` | 3001 | 256 MB |

Stałe: 448 MB; z Grafaną 704 MB. Razem z aplikacją (api 1536 + web 384 + caddy 128 MB): 2496 MB stale, 2752 MB z Grafaną, z 4096 MB hosta. Limity to sufit, rzeczywiste zużycie jest niższe.

Pliki: `prometheus.yml`, `rules/recording.yml`, `rules/alerts.yml` (9 alertów + `Watchdog`, odwzorowanie tabeli z `ALERTS.md`), `alertmanager.yml` (szablon z placeholderami), `render-alertmanager.sh`, `tests/*.test.yml` (promtool), `textfile/*.sh` (przejściowe L-4 i L-8), `grafana/` (provisioning i dashboard), `secrets/*.example`.

`ExternalProbeDown` (alert 1) nie jest regułą Prometheusa: to sonda monitora zewnętrznego (RUNBOOK §6).

## 2. Włączenie (na hoście, w `deploy/`)

1. **Sekrety** (pliki muszą istnieć przed `up`, inaczej Docker założy w ich miejscu katalogi). Szablony: `secrets/*.example`; prawdziwe pliki są ignorowane przez git.

   | Plik | Zawartość | Właściciel (UID) | Prawa |
   |---|---|---|---|
   | `secrets/metrics_token` | wartość `DAAS_METRICS_TOKEN` z `.env` | 65534 (`nobody`, Prometheus) | 0400 |
   | `secrets/smtp_password` | hasło konta SMTP dla alertów | 65534 (Alertmanager) | 0400 |
   | `secrets/heartbeat_url` | pełny adres pingu usługi heartbeat | 65534 (Alertmanager) | 0400 |
   | `secrets/grafana_admin_password` | hasło administratora Grafany | 472 (Grafana) | 0400 |

   ```bash
   cd /opt/daas-engine/deploy/observability/secrets
   umask 077
   sed -n 's/^DAAS_METRICS_TOKEN=//p' ../../.env | tr -d '\r\n' > metrics_token
   read -rsp 'Haslo SMTP: ' v; printf '%s' "$v" > smtp_password; unset v; echo
   read -rsp 'Adres heartbeat: ' v; printf '%s' "$v" > heartbeat_url; unset v; echo
   openssl rand -base64 24 | tr -d '\n' > grafana_admin_password
   sudo chown 65534:65534 metrics_token smtp_password heartbeat_url
   sudo chown 472:472 grafana_admin_password
   sudo chmod 0400 metrics_token smtp_password heartbeat_url grafana_admin_password
   ```

   **Pliki sekretów muszą istnieć przed `up`; `deploy.sh` sprawdza to w preflight (kod 2, w dzienniku tylko nazwy brakujących plików).** Bez tego Docker założyłby w ich miejscu puste katalogi.

   Po rotacji `DAAS_METRICS_TOKEN` zaktualizuj `metrics_token` i `docker compose restart prometheus`. Hasło Grafany odczytasz raz: `sudo cat grafana_admin_password`.

2. **Alertmanager** (szablon ma placeholdery `<<SMTP_HOST:587>>`, `<<SMTP_USER>>`, `<<ALERT_FROM>>`, `<<ALERT_TO>>`; wartości trzymasz tylko na hoście):

   ```bash
   cd /opt/daas-engine/deploy
   ALERT_SMTP_HOSTPORT=<host>:587 ALERT_SMTP_USER=<login> ALERT_FROM=<nadawca> ALERT_TO=<odbiorca> \
     ./observability/render-alertmanager.sh
   ```

   Skrypt zapisuje `observability/local/alertmanager.yml` (ignorowany przez git). **Fail-closed:** nakładka montuje wyłącznie ten wyrenderowany plik (`type: bind`, `create_host_path: false`, `read_only: true`), więc bez niego `docker compose up` kończy się błędem, zamiast uruchomić Alertmanager z placeholderami, który nie wysłałby żadnego e-maila. Uwaga: `docker compose config -q` **nie** wykrywa brakującego pliku (sprawdzone), błąd pojawia się dopiero przy `up`. Zmiana wartości: ponowny render i `docker compose up -d alertmanager`.

3. **Katalog textfile** dla node_exportera: `sudo install -d -m 0755 -o <uzytkownik crona> /var/lib/node_exporter/textfile`.

4. **`deploy/.env`**:

   ```
   COMPOSE_FILE=compose.yml:compose.observability.yml
   ```

   Z nakładką LLM: `COMPOSE_FILE=compose.yml:compose.llm.yml:compose.observability.yml`.

5. `docker compose up -d` (Prometheus, Alertmanager, node_exporter). Grafana na żądanie: `docker compose --profile dash up -d grafana`, po użyciu `docker compose stop grafana`.

6. **Cron operatora** (użytkownik z dostępem do dockera), przejściowe liczniki:

   ```
   */5 * * * * /opt/daas-engine/deploy/observability/textfile/mail-failures.sh
   ```

   Znacznik kopii (`backup-marker.sh offsite`) wołany jest z wrappera nocnego i z haka kopii poza serwerem, po `sha256sum -c` (sekcja 5).

Przeładowanie reguł: `docker compose kill -s SIGHUP prometheus` (`--web.enable-lifecycle` jest wyłączone). Alertmanager: `docker compose restart alertmanager`.

## 3. Dostęp do Grafany i Prometheusa

Brak publikacji przez Caddy. Z komputera operatora (tunel SSH):

```bash
ssh -L 3001:127.0.0.1:3001 <uzytkownik>@<host>      # Grafana: http://127.0.0.1:3001/ (login admin)
ssh -L 9090:127.0.0.1:9090 <uzytkownik>@<host>      # awaryjnie wystarcza UI Prometheusa (/targets, /alerts)
```

Dashboard: wiersze SLO, Golden signals API, Zadania, Host, Kopie i poczta. Panel „Saturacja" pokazuje brak danych do czasu luki L-7.

## 4. Sprawdzanie (bez lokalnych binariów, przez kontenery)

Wszystkie poniższe kontrole w jednym poleceniu: `bash deploy/observability/check.sh` (obrazy bierze z `compose.observability.yml`; używa go job `observability` w `.github/workflows/cd-staging.yml`). Asercja stosu z działającym `api` (target `up == 1`, `daas_build_info`, `Watchdog`): `bash deploy/observability/stack-check.sh` (wymaga Dockera; buduje obraz `api`, kasuje `deploy/.env`, więc **tylko na CI lub w katalogu roboczym bez `.env`**). Ręcznie, krok po kroku:

Tag obrazu jak w `compose.observability.yml` (⚠ do weryfikacji). Z korzenia repo:

```bash
P=prom/prometheus:v3.5.0; A=prom/alertmanager:v0.28.1; O="$PWD/deploy/observability"
# konfiguracja (pliki sekretow podstawiamy szablonami; ścieżki jak w kontenerze)
docker run --rm -v "$O/prometheus.yml:/etc/prometheus/prometheus.yml:ro" -v "$O/rules:/etc/prometheus/rules:ro" \
  -v "$O/secrets/metrics_token.example:/etc/prometheus/secrets/metrics_token:ro" --entrypoint promtool $P check config /etc/prometheus/prometheus.yml
# reguly
docker run --rm -v "$O/rules:/rules:ro" --entrypoint promtool $P check rules /rules/recording.yml /rules/alerts.yml
# testy regul: scenariusze (a)-(j), jeden test na alert + warianty
docker run --rm -v "$O:/work:ro" -w /work/tests --entrypoint promtool $P test rules slo_burn.test.yml symptoms.test.yml data_safety.test.yml host.test.yml meta.test.yml
# Alertmanager (szablon z placeholderami jest poprawny skladniowo)
docker run --rm -v "$O/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  -v "$O/secrets/smtp_password.example:/etc/alertmanager/secrets/smtp_password:ro" \
  -v "$O/secrets/heartbeat_url.example:/etc/alertmanager/secrets/heartbeat_url:ro" --entrypoint amtool $A check-config /etc/alertmanager/alertmanager.yml
```

Nakładka compose: `cd deploy && DAAS_SECRET_KEY=$(openssl rand -hex 32) docker compose -f compose.yml -f compose.observability.yml --profile dash config -q`.

Po uruchomieniu: `http://127.0.0.1:9090/targets` (cztery cele `UP`), `http://127.0.0.1:9090/alerts` (`Watchdog` firing). Cel `api` w stanie `DOWN` z błędem `401` = niezgodny token (panel „Stan scrape"); sprawdzenie bez Prometheusa: `ALERTS.md`, sekcja `SloAvailabilityFastBurn`.

## 5. Luki instrumentacji (praca Kodera w `app/`, poza tym zakresem) i rozwiązania przejściowe

Zasada etykiet: tylko szablon trasy, status, pipeline, rodzaj i wynik (kilka–kilkadziesiąt wartości). **Bez `org_id`, e-maili, nazw plików i identyfikatorów zadań.** `org_id` miałby kardynalność = liczba organizacji (ograniczona `DAAS_MAX_ACTIVE_TENANTS`), więc technicznie byłby dopuszczalny, ale wiąże telemetrię z klientem wbrew polityce `render_prometheus` i dokumentowi `ALERTS.md`; diagnoza per organizacja idzie przez audyt i tabelę `jobs`. Rewizja dopiero przy kilku klientach i realnej potrzebie, wtedy tylko w L-1/L-2 i nigdy w tekście alertu.

| ID | Metryka (typ) | Etykiety | Gdzie dodać (`plik:linia`) | Priorytet | Decyzja |
|---|---|---|---|---|---|
| L-1 | `daas_job_duration_seconds` (histogram, kubełki do 900 s: 5, 15, 30, 60, 120, 300, 600, 900) | `pipeline` (3), `status` (`succeeded`, `failed`, `cancelled`) | `app/control/executor.py:253-278` (`_execute`, przy `finished_at`) oraz `:324-328` (`_finish`) | P2 (SLO-3 p95) | Koder później; do tego czasu SQL z tabeli `jobs` (SLO.md §SLO-3) |
| L-2 | `daas_jobs_finished_total` (counter) | `pipeline`, `status`, `class` (`platform`, `client` wg `error_code`) | `executor.py:158-160` (`abandoned`), `:197` (`internal_error`), `:219`, `:226` (`SOURCE_UNAVAILABLE`), `:253-268` (wynik runu) | P2 | Koder później; interim: `delta(daas_jobs{status="failed"}[1h])` (reguła `source: interim`) |
| L-3 | `daas_job_queue_oldest_age_seconds` (gauge) | brak | `app/api/v1/metrics.py:46-53` (zapytanie `min(created_at)` dla `queued`) i `app/core/observability.py:288-295` (`render_prometheus`) | P2 | Koder później; interim: zastępnik `queued > 0` przy `running = 0` |
| L-4 | `daas_mail_send_total` (counter) | `kind` (`magic_link`, `invitation`, `reminder`, `run_notice`), `result` (`ok`, `error`); **bez adresów** | `app/api/v1/auth.py:126-131` (P1: SLO-2), potem `members.py:108`, `schedule.py:144`, powiadomienia po runie | **P1** | **Teraz interim** (poniżej); docelowo Koder |
| L-5 | `daas_webhook_notify_total` (counter) | `kind` (`slack`, `teams`, `json`), `result` | `app/control/notifications.py:130`, `webhooks.py:359-365` | P3 | Koder później |
| L-6 | `daas_scheduler_last_tick_timestamp_seconds`, `daas_retention_last_run_timestamp_seconds` (gauge) | brak | `app/control/schedule.py:269-274`, `app/control/retention.py:221-228` | P3 (panel, bez alertu: limit 10 objawów) | Koder później |
| L-7 | `process_resident_memory_bytes` (gauge; odczyt `/proc/self/statm`, stdlib; na Windows pominąć) | brak | `app/core/observability.py:260-296` (`render_prometheus`) | P2 | Koder później; interim: `oom_kill` i `MemAvailable` z node_exportera |
| L-8 | `daas_backup_last_success_timestamp_seconds` (gauge) | `target` (`offsite`, `local`) | **bez zmian w `app/`**: `textfile/backup-marker.sh` | **P1** | **Teraz** (rozwiązanie docelowe) |
| L-9 | `daas_maintenance_mode` (gauge 0/1) | brak | `metrics.py:46-56` + `observability.py:292-295` (`state.settings.daas_maintenance_mode`) | P3 | Koder później; interim: sonda monitora na `"maintenance":false` w `/api/v1/health` |

### Interim L-4: `textfile/mail-failures.sh`

Cron co 5 minut zlicza w dzienniku `api` linie `magic link e-mail failed` (`app/api/v1/auth.py:129-131`) z ostatnich 30 min i 24 h i zapisuje `daas_mail_failures_30m`, `daas_mail_failures_24h` (gauge z oknem przesuwnym) oraz `daas_textfile_collect_success{collector="mail_failures"}` do `daas_mail_failures.prom` (zapis atomowy: plik tymczasowy i `mv`). Skrypt **nie wypisuje treści dziennika**: czyta go do prywatnego katalogu tymczasowego (0700), liczy `grep -c` i kasuje. Przy niedostępnym `docker compose logs` zapisuje `collect_success 0` (a nie „0 błędów") i kończy się kodem 1. Opóźnienie do 5 minut. Alert: `MailSendFailing` (`source: interim`, `>= 2` w 30 min). Ograniczenia: pokrywa tylko link logowania; `DAAS_EMAIL_BACKEND=console` w produkcji maskuje wszystko (zawsze „sukces"); po wdrożeniu L-4 usuń cron i regułę `interim` (inaczej alert zapłonie podwójnie). Ślepa plama crona: osobny wariant `MailSendFailing` z `reason: stale_textfile` (ticket, `for: 15m`) płonie, gdy `daas_textfile_collect_success{collector="mail_failures"} == 0`, plik `daas_mail_failures.prom` jest starszy niż 900 s (`node_textfile_mtime_seconds{file="daas_mail_failures.prom"}`) albo go nie ma (cron nigdy nie zainstalowany).

### Interim L-8: `textfile/backup-marker.sh`

`backup-marker.sh offsite` zapisuje `daas_backup_last_success_timestamp_seconds{target="offsite"}`. Wołać **wyłącznie** po całym łańcuchu sukcesu, jako ostatni krok przed heartbeatem: `backup.sh` → `restic backup` → `restic forget --prune` → `sha256sum -c` → `backup-marker.sh offsite` → heartbeat (RUNBOOK §4.3). Brak znacznika przez 30 minut lub wiek > 26 h = `BackupStale`. Źródłem prawdy pozostaje heartbeat zewnętrzny (działa także, gdy host nie żyje); znacznik daje to samo na kokpicie i w regułach.

## 6. Retencja, rozmiar i koszty telemetrii

Prometheus: `--storage.tsdb.retention.time=35d` (okno SLO to 30 dni) i `--storage.tsdb.retention.size=2GB` (dysk 40 GB). Szacunek ⚠: poniżej 1 500 serii (api ok. 1 000 przy ok. 55 trasach, host ok. 100), czyli ok. 230 MB w 35 dni; `sample_limit: 10000` dla `api` zatrzymuje wybuch etykiet (scrape odrzucony, `up == 0`). Logi: `json-file` 10 MB × 3 na usługę. Telemetria nie zawiera danych klienta (etykiety i adnotacje alertów są statyczne).
