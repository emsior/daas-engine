# Alerty — specyfikacja i postępowanie

Alerty mówią o **objawach odczuwalnych przez klienta albo o bezpośrednim zagrożeniu danych**, nie o każdej metryce. Każdy wymaga działania; alert bez działania usuwamy. Cele, z których wynikają progi: [`SLO.md`](SLO.md) (cele wewnętrzne, nie SLA). Postępowanie ogólne w incydencie: [`RUNBOOK.md`](RUNBOOK.md) §9. Po incydencie: [`POSTMORTEM.md`](POSTMORTEM.md). Wydania i kody wyjścia deployu: [`CD.md`](CD.md).

Wersja 2 · 2026-11-04 · autor: rola SRE · dostosowana do reguł `deploy/observability/rules/alerts.yml` (Prometheus), `alertmanager.yml` i do `deploy/cd/deploy.sh`. Zmiana nazwy, progu lub `for` alertu = zmiana tego pliku, `SLO.md` i reguł w tym samym PR. Nazwa alertu = kotwica sekcji poniżej (`runbook_url` w regułach to `…/deploy/ALERTS.md#<nazwa małymi literami>`).

**Ważność.** `page` = wymaga reakcji w ciągu ok. 1 h, gdy operator jest osiągalny; `ticket` = do końca następnego dnia roboczego. Kanał to wyłącznie e-mail na adres kontaktowy (`prochpc@gmail.com`), z prefiksem `[PILNE]` lub `[ZADANIE]` w temacie. Nie ma dyżuru 24/7: żeby `[PILNE]` było słyszalne, operator ustawia w poczcie powiadomienie push dla tej etykiety (jednorazowa czynność tożsamościowa). Bez tego „page" oznacza tylko „e-mail wyższego priorytetu". Wyniki jobu `deploy` w GitHub Actions (kody 2, 10, 11, 12 oraz „wynik nieznany po terminie odpytywania", czyli stan kodu 3) przychodzą osobno, jako e-mail z GitHub o nieudanym przebiegu: włącz powiadomienia o Actions.

**Gdzie wykonywać polecenia.** Konto operatora **nie jest** w grupie `docker`, a `.env` ma prawa 0600 i właściciela `deploy` (`deploy/host/bootstrap.sh`). Wszystkie polecenia z `docker`, `.env` i `deploy/cd/` poniżej wykonuj w powłoce użytkownika `deploy`:

```bash
sudo -u deploy -i
cd /opt/daas-engine/deploy
```

Polecenia z `sudo` (np. `dmesg`, `journalctl`) i z `free` wykonuj w osobnej sesji operatora. Pojedyncze polecenie z konta operatora: `sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh status`.

**Oznaczenia „Dziś":** ✅ działa na istniejących metrykach (po włączniku i uruchomieniu nakładki obserwowalności); ⚠ działa z zastępnikiem lub przybliżeniem; 🚫 pełna wersja czeka na lukę instrumentacji (L-x). Reguły z etykietą `source: l2|l3|l4|l7` są nieaktywne, dopóki metryka nie istnieje; po wdrożeniu luki usuwa się regułę `source: interim` (inaczej alert zapłonie podwójnie).

## Tabela alertów (10 objawów + Watchdog)

| # | Nazwa | Intencja (objaw, szkic: sekcja „Szkice wyrażeń") | Próg | `for` | Ważność | Kanał | Źródło | Dziś | Runbook |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `ExternalProbeDown` | z zewnątrz nie działa `/api/v1/ready`, `/api/v1/health` (także gdy pole `maintenance` = true) lub `/login`; wariant ticket: certyfikat TLS wygasa za mniej niż 14 dni. **Nie jest regułą Prometheusa**: nie przechodzi przez Alertmanager, więc nic go nie tłumi ani nie wycisza | sondy nieudane nieprzerwanie ≥ 5 min (5 sond co 1 min albo 2 co 5 min); TLS: < 14 dni | ustawienie monitora | page (TLS: ticket) | e-mail z monitora | monitor zewnętrzny | ✅ po włączniku | [ExternalProbeDown](#externalprobedown) |
| 2 | `SloAvailabilityFastBurn` | szybkie spalanie budżetu SLO-1: błędy dostępności ≥ 14,4 × tempa budżetu (A1) | ≥ 7,2 % w 1 h **i** ≥ 7,2 % w 5 min | 0 (para okien zastępuje `for`) | page | e-mail | Prometheus | ✅ (przybliżenie `up` + 5xx z `/ready`) | [SloAvailabilityFastBurn](#sloavailabilityfastburn) |
| 3 | `SloAvailabilitySlowBurn` | wolne spalanie: ≥ 6 × tempa budżetu (A2) | ≥ 3 % w 6 h **i** ≥ 3 % w 30 min | 0 | ticket | e-mail | Prometheus | ✅ | [SloAvailabilitySlowBurn](#sloavailabilityslowburn) |
| 4 | `ApiHighErrorRatio` | udział 5xx w żądaniach użytkowników; z mianownika i licznika wyłączone: `/ready`, `/health` (z prefiksem `/api/v1` i bez), `/metrics` (z prefiksem i bez), `unmatched` (A3) | > 5 % w 10 min **i** ≥ 3 błędy | 5 min | ticket | e-mail | Prometheus | ✅ | [ApiHighErrorRatio](#apihigherrorratio) |
| 5 | `JobsFailedRising` | rośnie liczba zadań `failed`. Dziś (`source: interim`): przyrost gauge `daas_jobs{status="failed"}`, wlicza błędy danych klienta (A4). Docelowo (L-2, `source: l2`): tylko klasa „platforma" (A4b) | dziś ≥ 3 nowe w 1 h; docelowo ≥ 2 platformowe w 1 h | 0 | ticket | e-mail | Prometheus | ⚠ dziś, ✅ po L-2 | [JobsFailedRising](#jobsfailedrising) |
| 6 | `JobQueueStuck` | zadania nie schodzą z kolejki. Dziś, dwie reguły: `queued` > 0 przy `running` = 0 (`reason: queued_idle`) oraz `running` > 0 nieprzerwanie (`reason: running_long`) (A5). Docelowo (L-3, `reason: oldest_queued`): najstarsze `queued` starsze niż X = 10 min (A5b) | 15 min bez ruchu / 30 min jednego `running`; docelowo > 600 s | 15 min / 30 min / 5 min | ticket | e-mail | Prometheus | 🚫 pełna (L-3); ⚠ zastępnik | [JobQueueStuck](#jobqueuestuck) |
| 7 | `MailSendFailing` | link logowania nie jest przekazywany do SMTP (SLO-2). Dziś (`source: interim`): gauge `daas_mail_failures_30m` z crona hosta, liczba linii `magic link e-mail failed` z ostatnich 30 min (A6). Docelowo (L-4, `source: l4`): `daas_mail_send_total{result="error"}` (A6b). Trzecia reguła, `reason: stale_textfile` (ticket, `source: interim`), zgłasza ślepą plamę crona: `daas_textfile_collect_success == 0`, plik `daas_mail_failures.prom` starszy niż 900 s albo brak pliku (A6c) | ≥ 2 błędy w 30 min (page); `stale_textfile`: warunek utrzymany 15 min (ticket) | 0 (page) / 15 min (`stale_textfile`) | page (`stale_textfile`: ticket) | e-mail | textfile z dziennika (dziś), Prometheus (L-4) | 🚫 pełna (L-4); ⚠ zastępnik | [MailSendFailing](#mailsendfailing) |
| 8 | `BackupStale` | brak udanej kopii poza serwerem od ponad 26 h (SLO-4); dwie reguły: wiek znacznika (`reason: age`) i brak znacznika (`reason: absent`) (A7) | wiek > 26 h; brak znacznika przez 30 min | 0 / 30 min | ticket | e-mail | heartbeat (zewn.) + Prometheus (textfile, L-8) | ✅ heartbeat po włączniku; ⚠ Prometheus (wrapper hosta) | [BackupStale](#backupstale) |
| 9 | `DiskSpaceLow` | partycja `/` zajęta powyżej 80 % (`reason: used`) albo zapełni się w 24 h wg trendu z 6 h (`reason: forecast`) (A8) | > 80 % lub prognoza < 0 | 15 min / 30 min | ticket | e-mail | Prometheus (node_exporter) | ✅ | [DiskSpaceLow](#diskspacelow) |
| 10 | `ApiMemoryPressure` | `api` blisko limitu 1,5 GB. Dziś, dwie reguły: zabicie procesu przez OOM na hoście (`reason: oom_kill`) i mało pamięci hosta (`reason: host_low_memory`) (A9). Docelowo (L-7, `reason: rss`): RSS procesu `api` (A9b) | OOM-kill > 0 w 15 min; `MemAvailable` < 10 %; docelowo RSS > 85 % limitu (1,31 GiB) | 0 / 10 min / 10 min | ticket | e-mail | Prometheus | ⚠ dziś, 🚫 pełna (L-7) | [ApiMemoryPressure](#apimemorypressure) |
| — | `Watchdog` | meta: reguła płonie zawsze (A10) i jest wysyłana na heartbeat co 5 min (`severity: none`) | zawsze | 0 | brak (alarm zgłasza usługa heartbeat) | e-mail z usługi zewn. | Prometheus → Alertmanager → heartbeat | ✅ | [Watchdog](#watchdog) |

Uwagi do tabeli:

- **Etykiety reguł:** `severity` (`page`, `ticket`, `none`), `service` (`api` dla alertów 2, 3, 4, 10; `jobs` dla 5, 6; `mail` dla 7; `backup` dla 8; `host` dla 9; `meta` dla Watchdog), `source` (`interim`, `l2`, `l3`, `l4`, `l7`) i `reason` (wariant reguły o tej samej nazwie). Alertmanager grupuje po `alertname`, więc operator widzi jedną wiadomość na alert, niezależnie od wariantu.
- **Tłumienie (Alertmanager, `alertmanager.yml`):** (1) alert `page` tłumi `ticket` o **tej samej wartości `service`**: `SloAvailabilityFastBurn` (service `api`) tłumi więc `SloAvailabilitySlowBurn`, `ApiHighErrorRatio` i `ApiMemoryPressure`; (2) `SloAvailabilityFastBurn` tłumi dodatkowo `JobQueueStuck` i `JobsFailedRising`. `ExternalProbeDown` **nie tłumi niczego** (nie przechodzi przez Alertmanager). Skutek uboczny: gdy `api` pada z powodu OOM, ticket `ApiMemoryPressure` jest wyciszony przez page. Przy każdym `SloAvailabilityFastBurn` sprawdź więc OOM (sekcja tego alertu).
- **Powtórki:** `page` co 2 h, `ticket` co 24 h, `Watchdog` co 5 min (do heartbeatu). Usługa heartbeat ma okres ≥ 10 min z tolerancją (inaczej fałszywe alarmy przy restarcie Alertmanagera).
- **Retencja Prometheusa:** 35 dni i do 2 GB (`compose.observability.yml`), co pokrywa okno SLO 30 dni.
- Normalne wdrożenie (kopia zimna przed zmianą plus odtworzenie kontenerów, ⚠ 1–3,5 min przerwy) **nie** uruchamia `SloAvailabilityFastBurn` (godzinne okno zobaczy ≤ 5,8 % wobec progu 7,2 %; zapas niewielki, więc dłuższa kopia zimna może go przekroczyć i słusznie zostanie zgłoszona). Wdrożenie z wycofaniem (kod 10, 3–6 min) może je uruchomić, i słusznie: zużyło ≥ 2 % budżetu.
- Nocna kopia zimna (03:15 UTC) zatrzymuje `api` na ⚠ 0,5–2 min. Progi `for` i okna są dobrane tak, żeby jej nie zgłaszać; kopia dłuższa niż 5 min ma być zgłoszona jako anomalia.
- Opóźnienie odpowiedzi HTTP i ruch są na kokpicie Grafany (tunel SSH), bez alertu (brak SLO na opóźnienie). Brak alertu na `auth_denied`/401/429: skanowanie internetu jest tłem; skoki ogląda się raz w tygodniu.

## Szkice wyrażeń

Wyrażenia poniżej odpowiadają regułom w `deploy/observability/rules/`. Etykiety tras to szablony (`app/core/observability.py`, `route_template`): `/ready` i `/api/v1/ready` to dwie różne wartości `route`, bo healthcheck kontenera woła ścieżkę bez prefiksu, a metryki aplikacji są pod `/metrics` (bez prefiksu `/api/v1`).

```promql
# A1 — reguły nagrywające daas:slo1_error_ratio:{5m,30m,1h,6h} (i :30d dla kokpitu), źródło: recording.yml.
# Wartość = większy z dwóch błędów: 1 - avg(up{job="api"}) oraz udział 5xx wśród odpowiedzi z /ready lub /api/v1/ready.
# szybkie spalanie (page): obie strony naraz
daas:slo1_error_ratio:1h > (14.4 * 0.005)  and  daas:slo1_error_ratio:5m > (14.4 * 0.005)
# A2 — wolne spalanie (ticket)
daas:slo1_error_ratio:6h > (6 * 0.005)     and  daas:slo1_error_ratio:30m > (6 * 0.005)

# A3 — 5xx na trasach użytkowników (for: 5m)
  sum(rate(daas_http_requests_total{status=~"5..",route!~"(/api/v1)?/(ready|health)|(/api/v1)?/metrics|unmatched"}[10m]))
/ sum(rate(daas_http_requests_total{route!~"(/api/v1)?/(ready|health)|(/api/v1)?/metrics|unmatched"}[10m])) > 0.05
and sum(increase(daas_http_requests_total{status=~"5..",route!~"(/api/v1)?/(ready|health)|(/api/v1)?/metrics|unmatched"}[10m])) >= 3

# A4 — dziś (source: interim; gauge z bazy, wlicza błędy danych klienta)
sum(delta(daas_jobs{status="failed"}[1h])) >= 3
# A4b — po L-2 (source: l2; klasa błędu: platforma)
sum(increase(daas_jobs_finished_total{status="failed",class="platform"}[1h])) >= 2

# A5 — zastępnik dziś (source: interim)
max(daas_jobs{status="queued"}) > 0 and on() max(daas_jobs{status="running"}) == 0     # for: 15m, reason: queued_idle
max(daas_jobs{status="running"}) > 0                                                  # for: 30m, reason: running_long
# A5b — po L-3 (source: l3)
max(daas_job_queue_oldest_age_seconds) > 600                                          # for: 5m, reason: oldest_queued

# A6 — dziś (source: interim): gauge z okna przesuwnego 30 min, zapisywany przez cron hosta co 5 min (textfile)
max(daas_mail_failures_30m) >= 2
# A6b — po L-4 (source: l4)
sum(increase(daas_mail_send_total{kind="magic_link",result="error"}[30m])) >= 2
# A6c — ślepa plama crona (source: interim, reason: stale_textfile, ticket, for: 15m); usunąć razem z regułami interim po L-4
max(daas_textfile_collect_success{collector="mail_failures"}) == 0
  or max(time() - node_textfile_mtime_seconds{file="daas_mail_failures.prom"}) > 900
  or absent(node_textfile_mtime_seconds{file="daas_mail_failures.prom"})

# A7 — znacznik pisany po udanym restic backup/forget i sha256sum -c (textfile, L-8)
max(time() - daas_backup_last_success_timestamp_seconds{target="offsite"}) > 26 * 3600      # reason: age
absent(daas_backup_last_success_timestamp_seconds{target="offsite"})                         # for: 30m, reason: absent
# niezależnie: heartbeat o okresie 1 dnia i tolerancji 2 h (RUNBOOK §4.3)

# A8 — dysk
max(1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}
      / node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}) > 0.80          # for: 15m, reason: used
min(predict_linear(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}[6h], 24 * 3600)) < 0   # for: 30m, reason: forecast

# A9 — dziś (source: interim; kolektor vmstat z polem oom_kill jest w nakładce)
sum(increase(node_vmstat_oom_kill[15m])) > 0                                          # reason: oom_kill (licznik całego hosta)
min(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) < 0.10               # for: 10m, reason: host_low_memory
# A9b — po L-7 (source: l7)
max(process_resident_memory_bytes{job="api"}) > 0.85 * 1.5 * 1024 * 1024 * 1024       # for: 10m, reason: rss

# A10
vector(1)
```

Zastrzeżenia do szkiców:

- Liczniki HTTP są w pamięci procesu i zerują się przy każdym restarcie (wdrożenie, kopia); `increase`/`rate` to znoszą.
- Scrape `api` używa tokenu `DAAS_METRICS_TOKEN`. Błędny lub zrotowany token daje `up == 0` przy zdrowym API. **Metryką nie da się tego odróżnić od awarii** (Prometheus ustawia `up = 0` w obu przypadkach, kod HTTP widać tylko w UI w `/targets`, „Last error"). Dlatego `SloAvailabilityFastBurn` każe najpierw sprawdzić monitor zewnętrzny.
- Gauge `daas_mail_failures_30m` pochodzi z `docker compose logs --since 30m`, więc dokładność ogranicza rotacja dzienników (`json-file`, 10 MB × 3). Jeśli cron przestanie działać, plik textfile zostaje ze starą wartością; wtedy płonie ticket `MailSendFailing` z `reason: stale_textfile` (sekcja `MailSendFailing`). `page` o tej samej nazwie tłumi ten ticket (ta sama `service: mail`), więc przy aktywnym `page` kondycję crona sprawdź ręcznie.
- Odpowiedzi `503 maintenance` liczą się jako 5xx w A3. W trybie serwisowym (po kodzie 11 albo w wariancie B) `ApiHighErrorRatio` może więc płonąć: to zamierzone.

---

## ExternalProbeDown

**Znaczenie.** Klient z zewnątrz nie dostaje działającej usługi: nie odpowiada API (`ready` ≠ 200), kokpit (`/login`), albo pole `maintenance` w `/api/v1/health` jest `true` (sonda `health` ma wymagać `"maintenance":false`, RUNBOOK §6). Wariant TLS: certyfikat wygasa za mniej niż 14 dni (Caddy nie odnowił). To jedyny alert, który działa także wtedy, gdy umarł cały host.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy         # patrz „Gdzie wykonywać polecenia"
docker compose ps                           # stan i healthcheck: api, web, caddy
docker compose logs --since 15m caddy | tail -n 40
docker compose logs --since 15m api | tail -n 40
./smoke.sh --wait 30                        # lokalnie: czy problem jest przed Caddy czy za nim
./cd/deploy.sh status                       # ostatnie wdrożenie i jego wynik (kod 3 = trwa, 4 = brak stanu)
```

**Diagnoza.**

- Lokalny smoke zielony, publiczny czerwony: problem na krawędzi (Caddy, DNS, TLS, zapora). Po wdrożeniu to kod 11 z `edge=red` (sekcja „Kody wyjścia deployu"). `docker compose --profile edge ps caddy`, logi Caddy, `dig +short A <domena>`, RUNBOOK §2.1 i §6 (kontrola certyfikatu `openssl s_client`). Po pierwszym wdrożeniu na świeżym hoście Caddy potrzebuje chwili na wystawienie certyfikatu: odczekaj kilka minut i powtórz `./smoke.sh --public https://<domena>`.
- Lokalny smoke czerwony: `api` nie działa lub jest niegotowe. `ready` zwraca 503 przy awarii bazy control plane albo niezapisywalnym katalogu runtime: w logach `readiness check failed`; sprawdź `df -h /` (pełny dysk) i uprawnienia wolumenów.
- Awaria zaczęła się tuż po wdrożeniu (porównaj godzinę z `deploy.sh status`): podejrzewaj wersję.
- `maintenance: true` w `health`: tryb serwisowy został włączony (przez skrypt po kodzie 11 albo ręcznie, albo trwa odtworzenie z kopii). Patrz „Kody wyjścia deployu".
- Cisza z hosta (SSH nie odpowiada, Watchdog milczy): awaria hosta albo sieci; panel dostawcy, konsola awaryjna.

**Naprawa.** `docker compose restart api` (zachowuje dziennik kontenera; **nie** używaj `up -d` po zmianie `.env` przed zabezpieczeniem logów, RUNBOOK §9 pkt 1). Pełny dysk: sekcja `DiskSpaceLow`. Krawędź: `docker compose --profile edge up -d caddy` po sprawdzeniu `Caddyfile` (`caddy validate` jest w CI). TLS: odnowienie Caddy zależy od DNS i portów 80/443; sprawdź logi Caddy pod kątem błędów ACME.

**Klient i incydent.** Przestój do ok. 60 min bez utraty danych: bez powiadomienia klienta, wpis do postmortemu skróconego (tylko oś czasu i minuty budżetu). Dłużej niż okno serwisowe z RUNBOOK §9, brak odpowiedzi przy podejrzeniu włamania albo jakakolwiek utrata danych: to **incydent**, RUNBOOK §9 od kroku 1, powiadomienie klienta wg §9 pkt 6 i `docs/DPA_v1_PL.md` §4 pkt 5 (ocena prawna po stronie operatora). Progi czasu ⚠ do uzgodnienia z klientem w umowie.

**Wycofanie wersji.** Jeśli awaria zaczęła się w ciągu 30 min od wdrożenia, a schemat się nie zmienił: `sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh rollback` (wariant A, wraca do poprzedniego obrazu, robi kopię zimną, ponowny smoke). Skrypt sam odmówi (kod 2), gdy schemat bazy różni się od schematu poprzedniej wersji. Wydanie, które zmieniło schemat: rollback obrazu jest zabroniony, obowiązuje wariant B z RUNBOOK §5 i `README.md`. `rollback` wymaga zapisanych danych poprzedniej wersji (`previous.json`, `.env.prev`) i nie jest dostępny przez klucz SSH z workflow.

## SloAvailabilityFastBurn

**Znaczenie.** Budżet SLO-1 (99,5 % / 30 dni, 216 min) spala się 14,4 razy szybciej niż równomiernie: 7,2 % błędów w ostatniej godzinie (ok. 4,3 min przestoju, czyli 2 % budżetu) i nadal w ostatnich 5 minutach. W tym tempie budżet skończy się w 50 godzin. Pełna awaria uruchamia alert po ok. 4,5–5,5 min. Alert tłumi inne tickety o `service: api` oraz `JobQueueStuck` i `JobsFailedRising`, więc mogą być niewidoczne, dopóki płonie.

**Pierwsze 5 minut.** Najpierw: czy monitor zewnętrzny też widzi awarię? Jeśli tak, postępuj jak przy [ExternalProbeDown](#externalprobedown) (te same komendy). Jeśli **nie**: Prometheus jest ślepy, nie usługa.

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose ps
TOKEN="$(sed -n 's/^DAAS_METRICS_TOKEN=//p' .env)"
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics   # 200 = token dobry
docker compose logs --since 15m api | grep -E '"level": "(ERROR|CRITICAL)"' | tail -n 20
docker inspect -f '{{.State.OOMKilled}} {{.RestartCount}}' "$(docker compose ps -q api)"                # OOM tłumi ticket ApiMemoryPressure
```

**Diagnoza.** Kod `401` z `/metrics`: token w pliku scrape Prometheusa (`observability/secrets/metrics_token`) nie zgadza się z `.env` (po rotacji `DAAS_METRICS_TOKEN` zaktualizuj plik sekretu i zrestartuj Prometheus). Kod `200`, ale `up == 0`: sieć compose lub sam Prometheus. Realna awaria: jak w `ExternalProbeDown`. `OOMKilled=true`: patrz [ApiMemoryPressure](#apimemorypressure). Sprawdź, czy w ostatnich 30 min nie było wdrożenia lub kopii zimnej dłuższej niż zwykle.

**Naprawa.** Wg przyczyny (patrz wyżej). Do czasu wygaśnięcia alertu **nie wdrażaj** nowych wersji (polityka budżetu, [`SLO.md`](SLO.md) §5), chyba że wdrożenie jest naprawą.

**Klient i incydent.** Jak `ExternalProbeDown`. Zapisz minuty przestoju do rozliczenia budżetu w postmortemie.

**Wycofanie wersji.** Jeśli alert zaczął się po wdrożeniu: tak jak w `ExternalProbeDown` (rollback wariant A tylko przy niezmienionym schemacie).

## SloAvailabilitySlowBurn

**Znaczenie.** Budżet spala się 6 razy szybciej niż równomiernie: ≥ 3 % błędów w 6 h i nadal w ostatnich 30 min (ok. 10,8 min przestoju, 5 % budżetu). W tym tempie budżet skończy się w 5 dni. To nie jest pilna awaria, tylko powtarzający się problem (niestabilny restart, kopia zbyt długa, sporadyczne 503 z `ready`). Jest tłumiony przez `SloAvailabilityFastBurn`.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose ps
docker compose logs --since 6h api | grep -E 'readiness check failed|abandoned|unhandled exception' | tail -n 30
tail -n 30 /var/log/daas-backup.log
```

**Diagnoza.** Szukaj wzorca czasowego: ta sama godzina każdej nocy to kopia zimna (czas `tar` rośnie z danymi, RUNBOOK §4.2); restarty `api` bez wdrożenia to OOM ([ApiMemoryPressure](#apimemorypressure)); `readiness check failed` to baza lub dysk.

**Naprawa.** Usuń przyczynę wzorca (np. przesuń lub skróć kopię, zwiększ miejsce, popraw limit). Decyzja o rozmiarze zmiany wg stanu polityki budżetu ([`SLO.md`](SLO.md) §5).

**Klient i incydent.** Zwykle nie. Jeśli klient zgłosił przerwy: potraktuj jako problem jakości usługi i opisz w postmortemie.

**Wycofanie wersji.** Tylko gdy wzorzec zaczął się dokładnie od ostatniego wydania i schemat jest niezmieniony (`deploy.sh rollback`).

## ApiHighErrorRatio

**Znaczenie.** Ponad 5 % żądań użytkowników kończy się błędem 5xx (min. 3 błędy w 10 min, utrzymane 5 min), mimo że `ready` może odpowiadać 200. Pojedyncza ścieżka działa źle (np. tworzenie zadań, upload, raport). Do licznika i mianownika nie wchodzą `/ready`, `/health`, `/metrics` ani `unmatched`.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose logs --since 15m api | grep -E '"status": 5[0-9][0-9]' | tail -n 20     # które ścieżki
docker compose logs --since 15m api | grep -E 'unhandled exception|job crashed' | tail -n 10
TOKEN="$(sed -n 's/^DAAS_METRICS_TOKEN=//p' .env)"
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics | grep -E 'status="5'
```

**Diagnoza.** Etykieta `route` w metryce pokazuje, która trasa (szablon, bez identyfikatorów). Wspólny `request_id` z odpowiedzi 500 pozwala znaleźć wyjątek w dzienniku. Błędy `503` o kodzie `maintenance` oznaczają tryb serwisowy, nie awarię kodu (sprawdź `curl -fsS http://127.0.0.1:8000/health`, pole `maintenance`). Błędy tylko od jednej organizacji (zadania): zobacz `JobsFailedRising`.

**Naprawa.** Błąd po wdrożeniu: wycofanie. Błąd danych jednej organizacji: poprawka w kodzie to praca Backendu; do tego czasu poinformuj klienta o obejściu.

**Klient i incydent.** Jeśli funkcja nie działa dla klienta ponad kilka godzin: powiadom o stanie i obejściu (wiadomość o faktach, RUNBOOK §9 pkt 6 jako wzór treści). To incydent według §9 tylko przy podejrzeniu naruszenia danych.

**Wycofanie wersji.** Błędy zaczęły się w ciągu godziny od wdrożenia, schemat niezmieniony: `deploy.sh rollback`. Wydanie ze zmianą schematu: łatka naprzód (nowa wersja), a przy zagrożeniu danych wariant B.

## JobsFailedRising

**Znaczenie.** Przybywa zadań ze statusem `failed`. Dziś (`source: interim`) alert liczy wszystkie (w tym błędy danych klienta: `INVALID_DATA`, `no_records`), więc część to szum; po L-2 (`source: l2`) liczy tylko błędy platformy. Zadania `failed` z kodem `abandoned` powstają przy restarcie `api` (wdrożenie z `--force`, OOM, kopia zimna). Jest tłumiony przez `SloAvailabilityFastBurn`.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose logs --since 1h api | grep -E 'job crashed|marked .* interrupted' | tail -n 20
docker compose ps                    # czy api nie restartował się (czas działania)
```

**Diagnoza.** `marked N interrupted job(s) as failed (abandoned)`: restart `api` w ciągu godziny, szukaj jego przyczyny (wdrożenie, OOM, kopia). `job crashed`: wyjątek w runnerze, `request_id`/`job_id` w logu. Błędy jednej organizacji z kodami danych: klient wgrał zły plik, nie awaria. Status zadania i `error_code` widzi klient przez API, więc może to sprawdzić.

**Naprawa.** `abandoned`: klient uruchamia zadanie ponownie. `internal_error`/`RUN_FAILED` po wdrożeniu: wycofanie lub poprawka. Błędy danych klienta: wyjaśnij format (mapper nazw kolumn PL, `docs/API.md`).

**Klient i incydent.** Zadania `abandoned` po naszym restarcie: powiadom klienta, że raport trzeba uruchomić ponownie (to nasza przerwa). Brak utraty danych, więc nie jest to incydent z §9.

**Wycofanie wersji.** Gdy błędy platformy zaczęły się po wydaniu i dotyczą wielu zadań; schemat niezmieniony → `deploy.sh rollback`.

## JobQueueStuck

**Znaczenie.** Trzy warianty tej samej awarii „klient czeka na raport": (1) `queued_idle`: zadania czekają w `queued` od 15 min, a żadne nie jest wykonywane (martwy executor albo tryb serwisowy); (2) `running_long`: jakieś zadanie `running` trwa nieprzerwanie 30 min (limit SLO-3 to 5 min p95); (3) `oldest_queued` (po L-3, nieaktywny dziś): najstarsze `queued` czeka dłużej niż 10 min. Dziś nie widać zadania czekającego za długim, ale żywym przebiegiem (wariant 3). Jest tłumiony przez `SloAvailabilityFastBurn`.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
TOKEN="$(sed -n 's/^DAAS_METRICS_TOKEN=//p' .env)"
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics | grep '^daas_jobs'
curl -fsS http://127.0.0.1:8000/health          # pole "maintenance"
docker compose logs --since 30m api | grep -E 'maintenance=true|job crashed' | tail -n 20
docker stats --no-stream                        # czy api nie dusi CPU lub pamięci
```

**Diagnoza.** `maintenance: true` → executor nie startuje, kolejka stoi z założenia (kod 11 lub wariant B). `running` nie schodzi: duży plik XLSX albo wątek zablokowany (pipeline działa w wątku i nie da się go przerwać, `app/control/executor.py`). Brak oznak w logach i `queued` > 0 przy `running` = 0: executor martwy po restarcie.

**Naprawa.** `docker compose restart api`: przy restarcie `running` zmienia się w `failed/abandoned`, a `queued` wraca do kolejki (`executor.start()`). Zrób to dopiero po sprawdzeniu, że `running` to nie wielki, ale żywy przebieg (CPU `api` wysokie i rośnie postęp w kokpicie). Nie restartuj w trybie serwisowym, dopóki nie wiadomo, czemu został włączony.

**Klient i incydent.** Powiadom klienta, jeśli zadanie czeka dłużej niż ok. 1 h ⚠ lub zostało przerwane. To nie jest incydent z §9.

**Wycofanie wersji.** Jeśli kolejka stanęła po wdrożeniu, schemat niezmieniony: `deploy.sh rollback`.

## MailSendFailing

**Znaczenie.** Link logowania nie jest przekazywany do SMTP: klient dostaje `202`, ale e-mail nie wychodzi (`app/api/v1/auth.py`, `magic link e-mail failed`). Nikt, kto nie ma sesji, nie wejdzie do kokpitu. To awaria niewidoczna z zewnątrz, dlatego `page`. Dziś źródłem jest cron hosta (`/etc/cron.d/daas-observability`, co 5 min, skrypt `observability/textfile/mail-failures.sh`) zapisujący gauge do `/var/lib/node_exporter/textfile/daas_mail_failures.prom`; opóźnienie alertu to do ⚠ 5–10 min. **Ślepa plama:** gdy cron przestanie działać, plik zostaje ze starą wartością i `page` milczy; tę lukę zgłasza osobny ticket `MailSendFailing` z `reason: stale_textfile` (płonie, gdy `daas_textfile_collect_success{collector="mail_failures"} == 0`, plik jest starszy niż 15 min albo go nie ma). Gdy dostaniesz ten ticket, przez ten czas błędy wysyłki linku nie są wykrywane: traktuj to tak, jakby monitoring poczty był wyłączony.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose logs --since 1h api | grep 'magic link e-mail failed' | tail -n 3      # rodzaj wyjątku w polu exc
docker compose logs --since 1h api | grep -c 'magic link e-mail failed'
cat /var/lib/node_exporter/textfile/daas_mail_failures.prom                            # collect_success musi być 1
```

Potem test z RUNBOOK §2.4 (żądanie linku i `grep -c` po 5 minutach).

**Diagnoza.** Wyjątek uwierzytelnienia SMTP: hasło wygasło lub zostało zrotowane (RUNBOOK §9 pkt 4 rotuje także hasło SMTP). Timeout lub odmowa połączenia: przekaźnik niedostępny, blokada portu wychodzącego, limit dzienny konta pocztowego. Wcześniej wszystko działało, a nic nie zmieniono: stan przekaźnika po stronie dostawcy poczty. Sprawdź też `DAAS_EMAIL_BACKEND`: wartość `console` w produkcji nie wysyła nic (RUNBOOK §2.2). `daas_textfile_collect_success{collector="mail_failures"} 0` albo plik starszy niż 15 min (wariant `stale_textfile`): cron nie działa lub nie mógł odczytać dziennika. Sprawdź `cat /etc/cron.d/daas-observability`, uprawnienia katalogu textfile i ręcznie uruchom `observability/textfile/mail-failures.sh` jako `deploy`; do tego czasu policz błędy ręcznie (`docker compose logs --since 1h api | grep -c 'magic link e-mail failed'`).

**Naprawa.** Zaktualizuj poświadczenia w `.env` (edytor, bez `source`), potem `docker compose up -d api` (po zabezpieczeniu logów, RUNBOOK §9 pkt 1), i ponów test §2.4. Dokumentacja SPF/DKIM/DMARC: RUNBOOK §2.3. Zalogowani użytkownicy mają sesje, więc nie są dotknięci.

**Klient i incydent.** Powiadom klienta, jeśli logowanie nie działa dłużej niż ok. 2 h ⚠ w godzinach pracy: poda alternatywę (np. sesja już zalogowanego użytkownika). Nie jest to naruszenie danych, więc bez §9, chyba że przyczyną był wyciek poświadczeń SMTP: wtedy incydent.

**Wycofanie wersji.** Zwykle nie dotyczy (przyczyna poza kodem). Jeśli błąd zaczął się po wydaniu i dotyczy kodu wysyłki: `deploy.sh rollback`.

## BackupStale

**Znaczenie.** Ostatnia udana kopia poza serwerem ma więcej niż 26 h (SLO-4), albo znacznik w ogóle nie istnieje od 30 min, albo heartbeat nie przyszedł. Jeśli serwer padnie teraz, tracimy więcej niż dobę danych klienta, a kopia lokalna zginie razem z serwerem. Znacznik (`daas_backup_last_success_timestamp_seconds{target="offsite"}`) zapisuje dopiero hak kopii poza serwerem, po `restic backup`, `forget` i `sha256sum -c`. **Bez haka `deploy/cd/offsite-backup.hook` ten alert będzie płonął zawsze**: to zamierzone (brak kopii poza serwerem to brak ochrony).

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
tail -n 50 /var/log/daas-backup.log                  # wynik nocnej kopii (cron 03:15 UTC, /etc/cron.d/daas-backup)
ls -lh backups/ | tail -n 5
ls -l cd/offsite-backup.hook /var/lib/node_exporter/textfile/
( . ~/.config/daas-restic.env && restic snapshots --tag daas | tail -n 5 )
df -h /
```

**Diagnoza.** Archiwum lokalne jest, migawki restic nie ma: wysyłka poza serwer (sieć, poświadczenia magazynu, miejsce w magazynie). Brak haka albo hak bez prawa wykonania: kopia poza serwerem nigdy nie ruszy. Nie ma nawet archiwum: `backup.sh` (pełny dysk, `api` nie wstał po zatrzymaniu) lub cron. W logu „trwa wdrożenie albo inna operacja: pomijam kopię nocną": blokada `deploy.lock` trzymana zbyt długo (zawieszone wdrożenie albo wysyłka restic). Archiwum jest, ale suma nie przechodzi `sha256sum -c`: uszkodzenie, nie ufaj. Heartbeat milczy, a Prometheus pokazuje świeży znacznik (albo odwrotnie): rozjazd haka i usługi, sprawdź plik z adresem heartbeat (`~/.config/daas-heartbeat-url`).

**Naprawa.** Uruchom ręcznie `/opt/daas-engine/deploy/cd/nightly-backup.sh` jako `deploy` (kopia zimna, suma, hak) i potwierdź nową migawkę oraz sygnał heartbeat. Po zimnej kopii sprawdź `docker compose ps` (`api` musi wstać). Przy pełnym dysku najpierw [DiskSpaceLow](#diskspacelow). Szablon haka: `deploy/cd/offsite-backup.hook.example`.

**Klient i incydent.** Nie dotyczy klienta, o ile nie doszło do utraty danych. Jeśli przez 2 kolejne doby nie ma kopii poza serwerem, traktuj to jako ryzyko wysokie i rozwiąż tego samego dnia; tylko utrata serwera bez kopii byłaby incydentem z §9. Zmiana schematu (`allow_schema_change`) jest niemożliwa bez działającego haka: deploy kończy się kodem 2.

**Wycofanie wersji.** Nie dotyczy.

## DiskSpaceLow

**Znaczenie.** Partycja `/` jest zajęta w ponad 80 % (od 15 min) albo według trendu z 6 h zapełni się w ciągu doby (od 30 min). Pełny dysk zatrzyma `api`, kopie i wdrożenia; preflight deployu wymaga ≥ 3 GB wolnego miejsca (inaczej kod 2).

**Pierwsze 5 minut.**

```bash
df -h /
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker system df
du -sh backups cd/log
docker image ls
```

**Diagnoza.** Największe pozycje wg RUNBOOK §6: `backups/`, obrazy, wolumeny (`docker system df -v`). Nagły wzrost: kopie rosną z danymi albo dziennik poza rotacją. `cd/log/` rośnie powoli (skrypt trzyma 30 ostatnich dzienników i 10 katalogów incydentu).

**Naprawa.** Usuń najstarsze lokalne archiwa z `backups/` dopiero po potwierdzeniu świeżej kopii poza serwerem (`restic snapshots`). Obrazy: usuń tylko nieużywane (`docker image prune` dla wiszących); **nie używaj `prune -a`** i nie usuwaj obrazów poprzedniej wersji, bo są potrzebne do wycofania (ADR-004; po wdrożeniu zostają tagi N, N-1, N-2). Jeśli to nie wystarcza: powiększ dysk w panelu dostawcy.

**Klient i incydent.** Nie, dopóki `api` działa. Jeśli dysk zapełnił się i usługa stanęła: jak `ExternalProbeDown`.

**Wycofanie wersji.** Nie dotyczy.

## ApiMemoryPressure

**Znaczenie.** Dziś (bez cAdvisor i bez L-7) alert wyłapuje dwa objawy: jądro zabiło proces z powodu braku pamięci (`oom_kill`, licznik **całego hosta**, zwykle `api` przy dużym imporcie XLSX) albo hostowi zostało mniej niż 10 % pamięci (od 10 min). Po L-7 dojdzie ostrzeżenie wyprzedzające: RSS `api` > 85 % limitu 1,5 GB. Skutek zabicia: restart `api`, zadania w locie `abandoned`. Przy `SloAvailabilityFastBurn` ten ticket jest wyciszony, więc OOM sprawdzaj ręcznie.

**Pierwsze 5 minut.**

```bash
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker stats --no-stream
docker inspect -f '{{.State.OOMKilled}} {{.RestartCount}}' "$(docker compose ps -q api)"
```

W sesji operatora: `free -m` oraz `sudo dmesg -T | grep -iE 'out of memory|killed process' | tail -n 5`.

**Diagnoza.** `OOMKilled=true` lub wpis w `dmesg` z procesem `api`: limit przekroczony. Sprawdź czas zdarzenia i zadania z tej chwili (`abandoned`). Wpis w `dmesg` z innym procesem albo mało pamięci hosta przy małym `api`: inna usługa (Prometheus, Grafana w profilu `dash`), swap (RUNBOOK §1.4).

**Naprawa.** Nie zwiększaj limitu bez sprawdzenia budżetu RAM VPS 4 GB (api 1,5 GB + web 0,38 + caddy 0,13 + obserwowalność ok. 0,45; reszta to system). Wyłącz Grafanę, jeśli działa. Powtarzający się OOM przy jednym pliku: zgłoszenie do Backendu (limity XLSX), klientowi zaproponuj mniejszy eksport.

**Klient i incydent.** Powiadom klienta o przerwanych zadaniach (uruchomić ponownie). To nie jest incydent z §9.

**Wycofanie wersji.** Jeśli OOM zaczęły się po wydaniu i schemat niezmieniony: `deploy.sh rollback`.

## Watchdog

**Znaczenie.** Reguła płonie zawsze i jest wysyłana co 5 min na zewnętrzny heartbeat (webhook Alertmanagera). Ten alert **nie wymaga działania, dopóki płonie**. Gdy usługa heartbeat przestaje go dostawać, wysyła e-mail: to znaczy, że umarł host, Prometheus albo Alertmanager, czyli wszystkie pozostałe alerty Prometheusa też milczą (monitor zewnętrzny i heartbeat kopii działają niezależnie).

**Pierwsze 5 minut.**

```bash
ssh <operator>@<host>                       # czy host żyje
sudo -u deploy -i && cd /opt/daas-engine/deploy
docker compose ps                           # prometheus, alertmanager, node_exporter
docker compose logs --since 30m alertmanager | tail -n 20
```

**Diagnoza.** Host nie odpowiada: panel dostawcy, konsola awaryjna; dalej jak `ExternalProbeDown`. Host żyje, Alertmanager nie: brak wyrenderowanego pliku `observability/local/alertmanager.yml` (nakładka nie wstanie, to zamierzone), błąd konfiguracji (`amtool check-config` jest w CI), odrzucony webhook heartbeat. Prometheus martwy: zasoby, dysk.

**Naprawa.** Wystartuj brakującą usługę (`docker compose up -d prometheus alertmanager`, zgodnie z `COMPOSE_FILE` w `.env`). Do czasu naprawy pracujesz bez alertów: sprawdzaj monitor zewnętrzny.

**Klient i incydent.** Nie dotyczy klienta, o ile sama usługa działa.

**Wycofanie wersji.** Nie dotyczy.

---

## Kody wyjścia deployu

Kontrakt `deploy/cd/deploy.sh` (ADR-004/005), szczegóły uruchamiania i checklista włącznika: [`CD.md`](CD.md). Na stdout (i w logu GitHub Actions) wraca jedna linia `DAAS_DEPLOY result=… code=… version=… previous=… schema=… duration=… maintenance=<on|off|unverified> edge=<ok|red>`. Workflow przepuszcza do publicznego logu tylko linię o ścisłym wzorcu, z wersją tego wydania. Pełny dziennik jest tylko na hoście: `deploy/cd/log/<id>.log` (0700). Stan: `sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh status` (kod 3 = operacja trwa, 4 = brak stanu).

`edge=red` znaczy: lokalny smoke (API i kokpit na `127.0.0.1`) jest zielony, a czerwony jest któryś z testów `--public` (Caddy, DNS, TLS, nagłówki, ukryty `/api/v1/metrics`, metadane OAuth, `/mcp` bez klucza). Wersja jest wdrożona, nie było rollbacku ani trybu serwisowego. Który test zawiódł, widać w `deploy/cd/log/<id>.log`; nie zakładaj z góry, że to sam certyfikat.

| Kod | Znaczenie | Stan po | Tryb serwisowy | Co robisz |
|---|---|---|---|---|
| **0** | wdrożone, smoke zielony (`result=ok`), albo ta wersja już stoi (`noop`) | wersja N | bez zmian | nic; w ciągu godziny obserwuj `SloAvailabilityFastBurn` i `ApiHighErrorRatio`. Jeśli `.env` ma tryb serwisowy (po wcześniejszym incydencie), zostaje on włączony i podsumowanie pokazuje `maintenance=on`: sprawdź `health` i wyłącz po potwierdzeniu zdrowia (`maintenance off`) |
| **2** | preflight lub brama nie przeszły (brak miejsca, zła wersja, niezgodny digest lub SHA, downgrade, zmiana schematu bez zgody, drenaż (także ponowny odczyt tuż przed kopią), kopia albo przekroczony limit kopii (15 min) lub haka (20 min), brak plików sekretów nakładki lub wyrenderowanej konfiguracji Alertmanagera, wydanie z `allow_schema_change` bez haka kopii poza serwerem, inna operacja trwa; także proces roboczy zabity przed `apply`) | wersja N-1, **nic nie zmieniono** | bez zmian | przeczytaj przyczynę w `deploy/cd/log/<id>.log` (jako `deploy`), usuń ją, ponów. Bez presji czasu, bez powiadamiania klienta. Po przerwanej kopii skrypt sam ponownie uruchamia `api`; sprawdź `docker compose ps` |
| **3** | (brak linii `DAAS_DEPLOY`; `DAAS_STATUS state=running`) operacja trwa, wynik nieznany: minął limit czekania `deploy` (50 min) albo `status` widzi trwający proces | w toku | bez zmian | czekaj i sprawdzaj `deploy.sh status`; workflow odpytuje do ok. 62 min od startu kroku, potem zgłasza błąd „wynik nieznany". To **nie** jest incydent. Jeśli `status` po dłuższym czasie nadal pokazuje „trwa", a `docker compose ps` i logi stoją, traktuj jako zawieszenie i diagnozuj na hoście |
| **10** | smoke N czerwony (lokalnie), **rollback udany** | wersja N-1 | bez zmian | usługa działa na starej wersji. Przejrzyj przyczynę (podsumowanie, dziennik, logi), napraw w kodzie, nowe wydanie. Przestój ok. 3–6 min policz do budżetu SLO-1; klienta zwykle nie powiadamiasz, chyba że trwało to dłużej niż minuty i klient pracował |
| **11**, `edge=ok` | smoke N czerwony (lokalnie), **rollback zabroniony**: wydanie zmieniło schemat, pierwsze wdrożenie, **powtórka wdrożenia tej samej wersji** (w tym przebiegu nie zapisano `.env.prev`) albo schemat bazy różny od schematu poprzedniej wersji (żywy odczyt tuż przed rollbackiem) | wersja N, stan niepewny | **`on` automatycznie** (`maintenance=on`, albo `unverified`, gdy `.env` zmieniono, ale `health` nie potwierdził); stary `.env.prev` przeniesiony na `.env.prev.<id>` (nie do użycia w kolejnym przebiegu) | **incydent**: RUNBOOK §9 od kroku 2 (logi i `ps` są już zapisane w `deploy/cd/log/<id>-incident/`), decyzja: łatka naprzód albo wariant B (RUNBOOK §5, `README.md`). Sprawdź, czy dane klienta nie zostały uszkodzone. Powiadomienie klienta wg §9 pkt 6 i `docs/DPA_v1_PL.md` §4 pkt 5. Powrót z trybu serwisowego tylko ręcznie |
| **11**, **`edge=red`** | lokalny smoke zielony, czerwony tylko `--public`; w każdym wariancie schematu | wersja N **wdrożona**, bez rollbacku | **bez zmian** (`off`, albo `on`, jeśli był włączony wcześniej) | **nie incydent schematu**, ale usługa może być niedostępna z zewnątrz (`ExternalProbeDown` zwykle płonie). Sprawdź krawędź (`CD.md`, sekcja e, pkt 6): DNS, porty 80/443, `docker compose logs caddy`, `./smoke.sh --public https://<domena> --wait 120`. Gdy zielony, uruchom to samo wydanie ponownie (wynik `noop`). Na świeżym hoście Caddy potrzebuje chwili na certyfikat; skrypt ponawia wtedy `--public` sam |
| **12** | rollback podjęty i nieudany (także nieoczekiwane zakończenie procesu roboczego po rozpoczęciu zmian) | nieokreślony | `on` tylko gdy `api` odpowiada na `/health`, inaczej `unverified`; **dowody w `…-incident/` zapisane zawsze**; stary `.env.prev` przeniesiony na `.env.prev.<id>` | **incydent P1**: nie ponawiaj skryptu na ślepo. Sprawdź `docker compose ps`, zawartość `…-incident/` i stan trybu (`health`); przy `maintenance=unverified` i żywym `api` wykonaj `deploy.sh maintenance on` ręcznie. Potem wariant A lub B (`CD.md`, sekcja e) |

**`maintenance=unverified`** oznacza: `.env` ma `DAAS_MAINTENANCE_MODE=true`, ale `/api/v1/health` nie potwierdził trybu w 60 s (np. `api` nie wstaje), albo po kodzie 12 `api` w ogóle nie odpowiadało i tryb nie został włączony. Traktuj tryb jako niepewny: sprawdź `curl -fsS http://127.0.0.1:8000/api/v1/health` i `docker compose ps`, a przy żywym `api` z `maintenance:false` wykonaj `deploy.sh maintenance on` ręcznie.

**Powtórne uruchomienie po kodzie 11 lub 12.** Nadal nie uruchamiaj ponownie tego samego wydania na ślepo (`CD.md`, sekcja e). Różnica względem poprzedniej wersji skryptu: powtórka tej samej wersji **nigdy nie cofa** kodu ani `.env` do poprzedniej wersji (rollback wymaga świeżego `.env.prev` z tego samego przebiegu i zgodnego schematu bazy); porażka kończy się ponownie kodem 11 z dowodami i trybem serwisowym. Wyjątek: `edge=red` po zielonym `--public` kończy się `noop`. Drogą naprzód po prawdziwym kodzie 11 pozostaje nowy tag z poprawką albo wariant B.

### Tryb serwisowy: co robi, czego nie robi

Rozstrzygnięcie Q10 (zaakceptowane przez Orkiestratora, zaimplementowane w `deploy.sh`): przy kodzie 11 skrypt najpierw zapisuje `logs.txt` i `ps.txt` w `deploy/cd/log/<id>-incident/` (0700), włącza `DAAS_MAINTENANCE_MODE=true` atomowo w `.env`, odtwarza tylko `api` i potwierdza stan w `/api/v1/health`. Nie robi rollbacku, nie odtwarza danych i nigdy sam nie wyłącza trybu (`deploy.sh maintenance off`). Włącza go tylko wtedy, gdy czerwony jest **lokalny** smoke; przy samym `edge=red` tego nie robi. Kod 12 zapisuje te same dowody i włącza tryb tylko wtedy, gdy `api` odpowiada (inaczej `maintenance=unverified`).

Tryb serwisowy nie zwraca 503 na całym API ani strony serwisowej. Wstrzymuje executor, harmonogram przypomnień, retencję i powiadomienia, a uruchomienia zadań, zaproszenia, linki logowania i webhooki dostają 503 `maintenance` (`app/main.py`, `jobs.py`, `members.py`, `auth.py`, `webhooks.py`). `/ready` i odczyt raportów działają dalej, **uploady nie są blokowane**. Dlatego:

- SLO-1 nie „widzi" trybu serwisowego: czas w tym trybie księguj ręcznie do budżetu ([`SLO.md`](SLO.md) §3) i pilnuj sondy `"maintenance":false` w `ExternalProbeDown`.
- Wyłączenie trybu po zakończeniu incydentu jest czynnością ręczną; zapomniany tryb to cichy brak raportów dla klienta.
- `JobQueueStuck` (`queued_idle`) i `ApiHighErrorRatio` (503 jako 5xx) mogą płonąć w trybie serwisowym. To zamierzone.
- Kolejne wdrożenie przy włączonym trybie pomija drenaż zadań (decyduje żywe `health.maintenance`, a gdy `api` nie odpowiada, flaga w `.env`; executor nie działa, więc zadania `running` to pozostałość), a tryb zostaje włączony po wdrożeniu (`maintenance=on` w podsumowaniu). Rollback ręczny i obrazu zachowuje bieżącą flagę trybu serwisowego.

Dlaczego automatycznie (skrót uzasadnienia): po kodzie 11 działa czerwony kod na zmigrowanym schemacie, a wariant B odtwarza stan sprzed wdrożenia, więc wszystko zapisane po wdrożeniu przepada. Tryb ogranicza tę utratę do minut zamiast godzin do reakcji operatora (60 min dziennie, bez dyżuru) i wstrzymuje wysyłkę e-maili oraz webhooków przez podejrzany kod. Jest też pierwszym krokiem wariantu B (RUNBOOK §5). Wydanie z `allow_schema_change` uruchamiaj tylko wtedy, gdy będziesz osiągalny przez następne ok. 60 min, i nie przed weekendem.
