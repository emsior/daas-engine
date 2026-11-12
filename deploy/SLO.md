# SLO — cele niezawodności DaaS Engine Cloud

> **Cele wewnętrzne, nie SLA; nie trafiają do oferty, DPA ani README klienta.**
> Służą operatorowi do decyzji (wdrażać czy naprawiać) i do progów alertów ([`ALERTS.md`](ALERTS.md)). Nie są obietnicą wobec nikogo, nie wiążą się z karami ani rekompensatami. Jeden serwer bez HA (`README.md`, „Znane ograniczenia i uwagi") nie pozwala uczciwie obiecać więcej niż „mierzymy i poprawiamy".

Wersja 1 · 2026-11-04 · autor: rola SRE · status: do akceptacji operatora.
Oznaczenia: ✅ da się zmierzyć na istniejącym kodzie (po uruchomieniu produkcji i monitora), ⚠ przybliżenie lub zastępnik, ❌ niepewne, 🚫 wymaga luki instrumentacji L-x (`reports/00_orkiestrator_strategia.md` §4; implementacja w `app/` poza tym zakresem).

## 1. Zasady wspólne

- **Okno:** kroczące 30 dni, czas UTC. Budżet błędu = (1 − cel) × okno.
- **Produkcja jeszcze nie działa** (włącznik: podpisana umowa pilota). Do tego czasu SLO jest specyfikacją i niczego nie mierzy. Pierwsze 30 dni po włączniku to **okres kalibracji**: zbieramy dane i poprawiamy cele, a polityka zamrożenia z sekcji 5 nie jest egzekwowana ⚠.
- **Mały ruch.** Pilot to jeden klient, więc procenty z małej próby są szumem. Dla SLO-2 i SLO-3 budżet podajemy w zdarzeniach i z minimalną próbą.
- **Jedno źródło prawdy na SLO.** Gdy Prometheus i monitor zewnętrzny się rozjeżdżają, wygrywa to, co widzi użytkownik (monitor zewnętrzny, dziennik, baza zadań).
- **Retencja Prometheusa pokrywa okno.** Okno 30 dni wymaga retencji ≥ 35 dni; plan ADR-008 mówił 15 dni, co dawałoby zapytania `[30d]` z połową okna. Nakładka obserwowalności ustawia 35 dni i limit 2 GB (`compose.observability.yml`). Koszt jest pomijalny (kilkaset serii co 30 s).
- **Czego nie ma w SLO:** opóźnienie odpowiedzi HTTP (histogram jest na kokpicie, bez celu), dostarczalność poczty do skrzynki (SLO-2 liczy przekazanie do SMTP, patrz uwagi), poprawność liczb w raporcie.

## 2. Zestawienie

| SLO | SLI (dobre zdarzenie) | Źródło pomiaru | Cel / 30 dni | Budżet błędu | Mierzalne dziś |
|---|---|---|---|---|---|
| **SLO-1** Dostępność | sonda `GET /api/v1/ready` zwraca 200 i treść z `ready` | monitor zewnętrzny (RUNBOOK §6); przybliżenie do alertów: `up{job="api"}` i `daas_http_requests_total` | 99,5 % | **216 min** (≈ 43 nieudane sondy co 5 min) | ✅ monitor po włączniku; Prometheus ⚠ jako przybliżenie |
| **SLO-2** Logowanie | żądanie linku logowania, które przekazano do SMTP bez wyjątku | docelowo `daas_mail_send_total` (L-4); do tego czasu licznik linii `magic link e-mail failed` z hosta ⚠ | ≥ 99 % | 1 % prób (30 prób = 0 zdarzeń; 300 = 3) | 🚫 L-4; ⚠ zastępnik z dziennika |
| **SLO-3** Raport | zadanie zakończone `succeeded`, bez błędów danych klienta w mianowniku; p95 czasu od utworzenia do końca ≤ 5 min ⚠ | docelowo `daas_jobs_finished_total` (L-2) i `daas_job_duration_seconds` (L-1); ręcznie: tabela `jobs` w `control.db` | ≥ 95 % i p95 ≤ 5 min | 5 % zadań (20 zadań = 1 zdarzenie) | 🚫 L-1/L-2; ⚠ ręcznie z bazy |
| **SLO-4** Kopia | wiek ostatniej udanej kopii poza serwerem < 26 h | heartbeat po kopii (RUNBOOK §4.3); docelowo `daas_backup_last_success_timestamp_seconds` (L-8) | < 26 h w każdej chwili | 1 przekroczenie na 30 dni (jedna opuszczona noc) | ✅ heartbeat po włączniku; Prometheus ⚠ L-8 (wrapper hosta) |

## 3. Szczegóły

### SLO-1 — dostępność

- **Co mierzymy:** czy API jest gotowe (baza control plane odpowiada, katalog runtime zapisywalny; `app/api/routes.py`, `ready`). 503 = awaria.
- **Źródło:** monitor zewnętrzny, sonda `/api/v1/ready`. RUNBOOK §6 zakłada 5 minut; jeśli plan monitora pozwala, ustaw 1 minutę (limit IP to 300/min). Rozdzielczość pomiaru to interwał sondy: jedna nieudana sonda księguje 5 minut (lub 1).
- **Księgowanie przestoju:** od pierwszej nieudanej sondy do pierwszej udanej. Dodatkowo **ręcznie** księgujemy każdą nieplanowaną chwilę, gdy klient nie mógł pracować, a `ready` odpowiadało 200: tryb serwisowy po kodzie 11, odtworzenie z kopii (wariant B). `DAAS_MAINTENANCE_MODE` blokuje uruchomienia, zaproszenia, linki logowania i webhooki (503 `maintenance`), ale `ready` i odczyt raportów działają dalej (`app/main.py`, `jobs.py`, `members.py`, `auth.py`). Bez tej reguły SLO byłoby zielone przy nieczynnym produkcie.
- **Przybliżenie w Prometheusie (do alertów spalania, nie do raportu):** udział 30-sekundowych odczytów z `up{job="api"} == 0` oraz udział odpowiedzi 503 na `/ready` (docker healthcheck wywołuje `/ready` co 30 s, więc jest stały strumień próbek niezależny od ruchu klientów). Nie widzi awarii Caddy, DNS, TLS ani hosta; to wyłapuje monitor zewnętrzny.
- **Budżet:** 43 200 min × 0,5 % = **216 min**.

### SLO-2 — logowanie (przekazanie linku do SMTP)

- **Dlaczego:** awaria wysyłki linku daje klientowi `202` i tylko wpis w dzienniku (`app/api/v1/auth.py`, `magic link e-mail failed`), więc jest niewidoczna w metrykach HTTP i w `/ready`. Bez tego SLO jedyny sygnał to skarga klienta.
- **Próba:** żądanie `POST /api/v1/auth/magic-link`, które przeszło walidację i limity i dotarło do wysyłki. Wyłączone: `429` (limit) i `503 maintenance`. Dobre zdarzenie: `mailer.send` bez wyjątku. Poza zakresem: faktyczne dojście do skrzynki (SPF/DKIM/DMARC i test dostarczenia: RUNBOOK §2.4, raz w miesiącu ręcznie).
- **Dziś (⚠):** błędy = liczba linii `magic link e-mail failed` (gauge `daas_mail_failures_30m` z crona na hoście zapisywany do textfile, decyzja Obserwowalności); próby = `increase(daas_http_requests_total{route="/api/v1/auth/magic-link",status="202"}[30d])`. Status 202 pada zarówno przy sukcesie, jak i przy błędzie wysyłki, więc to mianownik prób, nie sukcesów.
- **Docelowo (L-4):** `sum(increase(daas_mail_send_total{kind="magic_link",result="error"}[30d])) / sum(increase(daas_mail_send_total{kind="magic_link"}[30d]))`, etykiety bez adresów.
- **Budżet i próg próby:** 1 % prób. Poniżej 100 prób na 30 dni budżet jest mniejszy niż jedno zdarzenie, więc **każda** awaria wysyłki naruszenia SLO i trafia do przeglądu (bez rachunku procentowego).
- **Uwaga:** przy `DAAS_EMAIL_BACKEND=console` wysyłka „udaje się" zawsze (zapis do pliku). SLO-2 ma sens wyłącznie w produkcji z `smtp`.

### SLO-3 — raport (powodzenie i czas)

- **Zdarzenia kwalifikujące:** zadania zakończone w oknie ze statusem `succeeded` lub `failed`. Wyłączone: `cancelled` (decyzja klienta).
- **Błąd klienta czy platformy.** Statusy w metrykach (`daas_jobs`) tego nie rozróżniają, ale baza tak: `jobs.error_code` (`app/control/models.py`, `app/core/redact.py`, `app/control/executor.py`).

  | `error_code` | Klasa | W SLO |
  |---|---|---|
  | `INVALID_DATA`, `POLICY_DENIED`, `no_records` | dane lub konfiguracja klienta | **wyłączone** z mianownika |
  | `RUN_FAILED`, `internal_error`, `abandoned` | platforma | błąd |
  | `SOURCE_UNAVAILABLE` | niejasne (plik zniknął po retencji albo z dysku) | błąd ⚠ (ocena konserwatywna) |

  Założenie ⚠: `RUN_FAILED` to kod domyślny dla każdego nieznanego wyjątku i może przykrywać dane klienta (np. brakująca kolumna). Liczymy go jako błąd platformy, a przegląd miesięczny (sekcja 7) może go przeklasyfikować po sprawdzeniu dziennika. `abandoned` z naszych restartów (deploy z `--force`, zimna kopia) liczy się jako nasz błąd.
- **p95:** czas `finished_at − created_at` zadań `succeeded` (obejmuje kolejkę, tak widzi to klient). ❌ Wartość 5 minut nie ma pomiaru w żadnym dokumencie repo; to hipoteza do skorygowania po kalibracji.
- **Histogram HTTP nie wystarcza:** ostatni kubełek to 10 s (`DURATION_BUCKETS`), a raport trwa dłużej. Potrzebne L-1 z kubełkami do co najmniej 900 s (np. 5, 15, 30, 60, 120, 180, 300, 450, 600, 900, 1800).
- **Dziś:** w Prometheusie 🚫 (gauge `daas_jobs` pokazuje stan bieżący z bazy, nie przepływ, i nie ma klasy błędu). Ręcznie ✅: zapytanie do tabeli `jobs` w `control.db` przez kontener `maint` (`sqlite3` ze stdlib, tylko odczyt): liczba zadań wg `status` i `error_code` oraz 95. percentyl różnicy `finished_at − created_at` za 30 dni. Polecenie przygotuje Obserwowalność razem z wrapperem hosta.
- **Budżet i próg próby:** 5 % zadań. Przy < 20 zadań na 30 dni budżet to < 1 zdarzenie: każdy błąd platformy naruszenia SLO i idzie do przeglądu. Pilot z jednym klientem i cotygodniowym raportem to ⚠ 4–20 zadań na miesiąc, więc przez długi czas SLO-3 będzie działać w tym trybie.

### SLO-4 — świeżość kopii poza serwerem

- **SLI:** `time() − (znacznik ostatniej UDANEJ kopii poza serwerem)`. Znacznik pisze wrapper nocny dopiero po zakończeniu `restic backup` i `restic forget` oraz po `sha256sum -c` lokalnego archiwum (RUNBOOK §4.3, §4.4). Kopia lokalna bez wysyłki poza serwer nie liczy się.
- **Cel:** < 26 h w każdej chwili (kopia o 03:15 UTC + 2 h tolerancji heartbeatu z RUNBOOK §4.3 + zapas). Dobowy rytm: jedna opuszczona noc przekracza 26 h na około 22 godziny.
- **Budżet:** jedno przekroczenie na 30 dni. Drugie w oknie przenosi stan polityki na żółty (sekcja 5), bo oznacza brak ochrony przed utratą serwera.
- **Źródło:** heartbeat (zewnętrzny, działa także gdy host nie żyje) jest źródłem prawdy. Prometheus (L-8, textfile z hosta, bez zmian w `app/`) daje to samo na kokpicie i w regułach.

## 4. Budżet SLO-1 a wdrożenia

Szacunki ⚠ (do podmiany na pomiar po 30 dniach):

| Pozycja | Częstość / 30 dni | Przerwa jednostkowa ⚠ | Razem ⚠ |
|---|---|---|---|
| Wdrożenie `recreate` (ADR-004): kopia zimna przed zmianą (`api` zatrzymane na czas `tar`) + odtworzenie kontenerów | do 8 | 1–3,5 min ❌ (kopia 0,5–2 min zależy od rozmiaru danych, plus 20–90 s) | 8–28 min |
| Wdrożenie z automatycznym wycofaniem (kod 10) | 0–2 | 3–6 min | 0–12 min |
| Nocna kopia zimna: `backup.sh` zatrzymuje `api` na czas `tar` (RUNBOOK §4.2) | 30 | 0,5–2 min ❌ (zależy od rozmiaru danych) | 15–60 min |
| Restart hosta po aktualizacji jądra | 1 | 2–5 min | 2–5 min |
| Aktualizacja `docker-ce` (restart demona) | 1 | 1–3 min | 1–3 min |
| **Plan razem** | | | **26–108 min = 12–50 % budżetu** |

Wnioski:

- **Przerwa na wdrożenie nie ogranicza tempa zmian.** Przy 3,5 min cały budżet mieściłby ok. 60 wdrożeń. Ogranicza je ryzyko nieudanych wdrożeń: każde z wycofaniem kosztuje 3–6 min, czyli 1,5–3 % budżetu.
- **Największą pozycją jest nocna kopia zimna** (❌ nieznany czas `tar` do pierwszych pomiarów). Jeśli przekroczy 2 min, rozważ `--online` tylko jako decyzję świadomą (ryzyko spójności DuckDB, RUNBOOK §4.2) albo przesunięcie kopii do okna bez ruchu. Wymaga to zmiany w `backup.sh`/cronie, nie w SLO. Czas przestoju z kopii księgujemy w SLO, bo klient mógłby go zauważyć.
- Zostaje ≥ 124 min na nieplanowane awarie. Pojedyncza awaria z 60-minutowym przestojem to 28 % budżetu.

## 5. Polityka budżetu błędu

Stan liczymy dla SLO-1 jako U = zużyte minuty / 216 (kroczące 30 dni). Dla SLO-2 i SLO-3 stan wynika ze zdarzeń z sekcji 3. Najgorszy z czterech SLO decyduje o stanie.

| Stan | Warunek | Co wolno |
|---|---|---|
| **Zielony** | U < 50 %; w SLO-2/-3/-4 budżet zdarzeń nieprzekroczony | normalne tempo; wydanie zmieniające schemat (`allow_schema_change`) po zatwierdzeniu operatora |
| **Żółty** | 50 % ≤ U < 100 % albo spalił się budżet zdarzeń w SLO-2/-3/-4 | wdrażamy po jednej zmianie na raz, tylko po zielonym stagingu; **zmiany schematu tylko jako poprawka niezawodności**; przegląd przyczyn w ciągu 7 dni |
| **Czerwony** | U ≥ 100 % | **zamrożenie: tylko poprawki błędów, które zużywają budżet, i zmiany niezawodnościowe** (alerty, kopie, limity, runbook). Nowe funkcje czekają. Wyjście: okno kroczące wraca poniżej 100 % albo operator zapisuje świadomy wyjątek w postmortemie |

Spalanie w toku: gdy płonie `SloAvailabilityFastBurn`, **nie wdrażamy** (poza wdrożeniem, które naprawia przyczynę). `SloAvailabilitySlowBurn` wymaga przeglądu przed następnym wydaniem. W kalibracji (pierwsze 30 dni) te reguły są zaleceniami, nie blokadą ⚠.

Budżet nie jest do wydania: brak zużycia nie uzasadnia ryzykownych wydań, a zużycie nie jest winą nikogo (postmortem bez winy: [`POSTMORTEM.md`](POSTMORTEM.md)).

## 6. Co jest mierzalne dziś, a co czeka na luki

„Dziś" znaczy: na kodzie w repo, po uruchomieniu produkcji i monitora zewnętrznego.

| Składnik | Stan | Dowód / luka |
|---|---|---|
| `up{job="api"}`, liczniki i histogram HTTP, `daas_jobs`, `daas_build_info` | ✅ | `app/core/observability.py`, `app/api/v1/metrics.py` |
| Metryki hosta (dysk, pamięć, OOM-kill) | ✅ po nakładce obserwowalności | node_exporter (O1) |
| Sonda zewnętrzna, heartbeat | ✅ po włączniku | usługi poza repo (E4) |
| Sukces wysyłki poczty | 🚫 | L-4; ⚠ licznik z dziennika |
| Przepływ zadań i klasa błędu | 🚫 | L-2 (`class`: dane klienta / platforma) |
| Czas zadania (p95) | 🚫 | L-1 |
| Wiek najstarszego `queued` | 🚫 | L-3; ⚠ zastępnik w `ALERTS.md` |
| Pamięć procesu `api` | 🚫 | L-7; ⚠ zastępnik: OOM-kill hosta |
| Znacznik kopii poza serwerem w Prometheusie | ⚠ | L-8, wrapper hosta |
| Stan trybu serwisowego jako metryka | 🚫 | brak; ⚠ pole `maintenance` w `/api/v1/health` (monitor zewnętrzny) |

## 7. Przegląd miesięczny (ok. 10 min)

1. SLO-1: zużycie budżetu z monitora (minuty) i rozbieżność z przybliżeniem z Prometheusa. Zapisz stan polityki (zielony / żółty / czerwony).
2. SLO-2/-3: liczby zdarzeń; lista `error_code` z klasą „platforma" i ewentualna przeklasyfikowanie `RUN_FAILED`.
3. SLO-3: p95 z bazy i czy 5 minut ma sens.
4. SLO-4: ile nocy bez kopii poza serwerem.
5. Czy któryś alert zadzwonił bez potrzeby działania (usuń lub popraw) i czy przestój nie został wykryty alertem (dodaj).
6. Zapis w notatkach operatora: stan polityki i decyzje. Nie do repo.
