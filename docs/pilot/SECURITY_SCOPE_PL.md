# Zakres Bezpieczeństwa i Ochrony Danych (Security Scope)

Dokument opisuje gwarancje techniczne i operacyjne dotyczące bezpieczeństwa danych klienta przetwarzanych w ramach usługi DaaS Engine w wersji 2.0. Model zagrożeń i znane ograniczenia: [`../SECURITY.md`](../SECURITY.md). Umowa powierzenia: [`../DPA_v1_PL.md`](../DPA_v1_PL.md) (wersja 1, do przeglądu prawnego).

### 1. Izolacja Danych (dwa modele wdrożenia)

DaaS Engine działa w jednym z dwóch trybów (`DAAS_MODE`):

- **`selfhost` (jedna instancja na klienta).** Klient dostaje własną instancję aplikacji, własny proces i własny katalog
  runtime. Dane dwóch klientów nigdy nie trafiają do wspólnej bazy ani wspólnego procesu: izolacja jest granicą wdrożenia.
- **`cloud` (usługa hostowana, wiele organizacji).** Każda organizacja ma własny katalog `runtime/tenants/<org_id>/` z własnym
  plikiem bazy DuckDB, uploadami, raportami i logiem audytowym. Identyfikator organizacji pochodzi z poświadczeń
  (klucz API albo sesja), nie z danych żądania; zasób obcej organizacji odpowiada `404`. Metadane kont, kluczy i zadań są w osobnej
  bazie control plane i nie zawierają treści plików ani raportów. Proces jest wspólny dla organizacji, więc izolacja jest regułą
  w aplikacji (testowaną), a nie granicą procesu. Klient wymagający separacji na poziomie procesu wybiera `selfhost`.

W obu trybach:

- Pliki przesłane przez klienta trafiają wyłącznie do podkatalogu `uploads/` jego organizacji (lub wdrożenia).
- Nazwa pliku nadana przez klienta nie bierze udziału w budowie ścieżki zapisu — nazwę docelową generuje serwer.
- Ochrona przed Path Traversal: identyfikator organizacji jest walidowany (bez separatorów i kropek), a ścieżka docelowa
  rozwiązywana (`resolve()`) i sprawdzana pod kątem przynależności do katalogu metodą porównania komponentów ścieżki.
- Plik trafia do lokalizacji docelowej dopiero po przejściu wszystkich kontroli; zapis tymczasowy jest usuwany przy każdym niepowodzeniu.

**Poza zakresem tej wersji:** wspólna baza z kolumną `tenant_id`, role inne niż właściciel / administrator / członek, logowanie
SSO i kluczami dostępu (passkeys), umowa SLA.

### 2. Dane Osobowe (RODO / GDPR)
- Silnik analityczny **nie wymaga i nie przechowuje danych osobowych konsumentów** (imion, nazwisk, adresów zamieszkania, numerów telefonów, adresów e-mail czy numerów kart płatniczych).
- Klient jest instruowany, by eksportować wyłącznie dane transakcyjne (identyfikator, kwota, kategoria, data).
- W trybie `cloud` przetwarzane są dane kontaktowe użytkowników organizacji (adres e-mail, opcjonalnie imię i nazwisko). Podstawą jest umowa powierzenia
  (art. 28 RODO) akceptowana przy zakładaniu organizacji; wersja zaakceptowanej umowy i data są zapisane przy organizacji.
- Uploady są usuwane po okresie retencji ustawionym w organizacji (domyślnie 90 dni, liczony od dnia wgrania). Pliki raportów i dane analityczne przebiegów starszych niż ta retencja usuwa zadanie w tle (domyślnie co godzinę, `DAAS_RETENTION_INTERVAL_MIN`); rekord przebiegu z sumą SHA-256 raportu zostaje, a pobranie wygasłego raportu kończy się odpowiedzią `410 report_expired`. Upload czytany przez zadanie w kolejce lub w toku jest usuwany dopiero po jego zakończeniu. Usunięcie organizacji usuwa jej rekordy i katalog danych.

### 3. Tajemnica Przedsiębiorstwa i Kontrola Dostępu
- Raporty i dane analityczne nie są indeksowane ani udostępniane publicznie.
- Identyfikatory raportów (UUIDv4) i zadań (ULID) mają wysoką entropię, a mimo to dostęp do raportu zawsze wymaga poświadczeń organizacji.
- Warstwa audytowa (append-only) rejestruje każde uruchomienie narzędzia analitycznego.
- Log audytowy organizacji (`audit.jsonl`) rejestruje zdarzenia `upload`, `run` oraz `auth_denied` (odmowa dostępu). Wpisy zawierają
  znacznik czasu, rodzaj zdarzenia, decyzję, identyfikatory techniczne (`request_id`, `run_id`, `upload_id`), trasę i adres IP wywołującego;
  nie zawierają klucza API, ścieżek bezwzględnych ani treści danych klienta.
- W trybie `cloud` dodatkowo dziennik audytu organizacji w control plane (`GET /api/v1/orgs/{id}/audit`): założenie organizacji, zmiany ustawień,
  członkowie i zaproszenia, tworzenie i odwołanie kluczy, uploady, zadania, webhooki, logowania. Pole `meta` ma listę dozwolonych kluczy:
  nie zapisuje nazw plików, adresów e-mail w postaci jawnej ani wartości kluczy.

### 4. Uwierzytelnianie i sieć
- **`selfhost`:** wszystkie trasy poza `GET /health` i `GET /ready` wymagają nagłówka `X-API-Key`. Klucz ma co najmniej 16 znaków,
  jest porównywany w stałym czasie (skrót SHA-256) i nigdy nie trafia do odpowiedzi ani logów. Brak skonfigurowanego klucza nie oznacza
  dostępu otwartego: trasy chronione odpowiadają `401`, a start na adresie innym niż loopback jest odmawiany.
- **`cloud`:** brak haseł. Logowanie jednorazowym linkiem z e-maila (ważny 15 minut, 256-bitowy token, w bazie tylko jego skrót SHA-256),
  następnie sesja w cookie `HttpOnly; Secure; SameSite=Lax` z ochroną CSRF (double submit). Dla maszyn: klucze API organizacji z zakresami
  (np. tylko `uploads:write`, `runs:write`, `reports:read`), pokazywane jednorazowo, przechowywane jako skrót SHA-256, z terminem ważności i możliwością odwołania.
  Role: właściciel, administrator, członek. Nadanie uprawnień wyższych niż własne jest niemożliwe.
- Domyślny adres nasłuchu to `127.0.0.1`. Obraz kontenera ustawia `0.0.0.0` jawnie, a pliki compose publikują porty wyłącznie na `127.0.0.1`.
- Dokumentacja interaktywna (`/docs`, `/redoc`, `/openapi.json`) jest dostępna tylko przy `APP_ENV=dev`.
- `GET /health` i `GET /ready` nie ujawniają ścieżek ani konfiguracji; zwracają wyłącznie status, nazwę, wersję i tryb.
- Dostęp przez internet odbywa się przez TLS na reverse proxy (zestaw `deploy/` zawiera Caddy z automatycznym certyfikatem, HSTS i limitem rozmiaru żądania); aplikacja sama TLS nie obsługuje.

### 5. Limity i odporność na nadużycia
- Limity żądań w pamięci procesu (algorytm token bucket): uruchomienia i uploady per klucz, wszystkie trasy chronione
  per adres IP, nieudane próby klucza per adres IP, prośby o link logowania per adres e-mail i IP. Przekroczenie kończy się `429` z nagłówkiem `Retry-After`.
  Nieudane próby jednego adresu nie blokują poprawnego klucza.
- Kwota łączna plików w `uploads/` (domyślnie 1 GiB) egzekwowana w trakcie strumieniowania; przekroczenie kończy się `413`.
- W trybie `cloud` miesięczne limity planu (np. plan `free`: 20 uruchomień i 200 MiB uploadów) kończą się odpowiedzią `429` z kodem `plan_limit`;
  w organizacji działa jedno uruchomienie naraz, a kolejka jest ograniczona.
- Pojedynczy plik: do 10 MiB, do 100 000 wierszy (CSV), serwer generuje nazwę docelową. Pliki tymczasowe `*.tmp`
  są usuwane po niepowodzeniu oraz przy starcie.
- Limity działają w jednym procesie; start z liczbą workerów większą niż 1 jest blokowany.

### 6. Pliki wejściowe: CSV, XLSX i paczki kolektora
- Przesyłanie przyjmuje `.csv` oraz `.xlsx`. Plik XLSX jest sprawdzany jako kontener ZIP i konwertowany do CSV
  na tej samej ścieżce kontroli co plik CSV. Paczka z lokalnego kolektora artefaktów (`.json`, do 5 MiB) jest walidowana względem schematu.
- Odrzucane są: skoroszyty z makrami (`vbaProject`, arkusze makr), osadzone obiekty, deklaracje DTD,
  archiwa o nadmiernym rozmiarze po rozpakowaniu lub współczynniku kompresji, więcej niż 20 arkuszy,
  powyżej 200 000 wierszy lub 256 kolumn. Skoroszyt z kilkoma arkuszami wymaga wskazania arkusza w polu `sheet`.
- Formuły nie są wykonywane; odczytywane są zapisane wartości.

### 7. Raporty i odpowiedzi API
- Wartości z danych klienta (identyfikatory, nazwy produktów, kategorie, nazwa klienta, nagłówki kolumn) są
  zamieniane na tekst przed złożeniem raportu HTML; renderer nie przepuszcza surowego HTML, linków ani obrazów.
- Odpowiedzi raportów niosą nagłówki `Content-Security-Policy` (bez skryptów i zasobów zewnętrznych),
  `X-Content-Type-Options: nosniff` i `Referrer-Policy: no-referrer`. Kokpit pokazuje raport w izolowanej ramce (`sandbox`) bez skryptów.
- Odpowiedzi API nie zawierają ścieżek bezwzględnych ani surowych komunikatów wyjątków. Błędy mają postać `application/problem+json`
  (`code`, `detail`, `request_id`); ten sam `request_id` jest w nagłówku odpowiedzi i w logach JSON, co pozwala powiązać zgłoszenie z wpisem w logu.
- Skrót SHA-256 raportu jest zapisany w control plane. Funkcja „sprawdź sam” odpowiada, czy dany skrót należy do raportu organizacji; nigdy nie zwraca treści raportu.

### 8. Dostawcy zewnętrzni
- Streszczenie generowane przez model językowy jest **domyślnie wyłączone**. W trybie `selfhost` wymaga jawnego `DAAS_LLM_NARRATIVE=true`
  oraz klucza dostawcy, w trybie `cloud` zgody organizacji (ustawienie `llm_narrative`) oraz klucza dostawcy; sam klucz w środowisku niczego nie włącza.
- Do dostawcy trafiają policzone metryki oraz nazwy do trzech największych kategorii i map oraz do trzech zamówień stratnych z pliku klienta (tekst z pliku, np. nazwa kategorii, może więc trafić do zapytania). Identyfikatory zamówień, nazwy produktów, nazwa klienta,
  nazwa hosta i skróty paczki kolektora są usuwane z zapytania. Raport oznacza takie streszczenie
  jako wygenerowane przez model. Streszczenie nie jest weryfikowane z danymi (instrukcja „używaj tylko liczb z JSON” nie jest kontrolą), więc traktuj je jak szkic; tabele liczbowe raportu liczy silnik, nie model.
- Pozostałe źródła zewnętrzne (FACEIT, Liquipedia, Apify, Discord) działają tylko po skonfigurowaniu ich kluczy;
  bez nich pipeline używa danych przykładowych.
- Wiadomości e-mail (link logowania, zaproszenia) wysyła serwer SMTP wskazany przez operatora.

### 9. Łańcuch dostaw
- Zależności produkcyjne są przypięte do wersji i skrótów SHA-256 (`requirements.lock`); obraz Docker i CI
  instalują je z `--require-hashes`, a CI sprawdza je narzędziem `pip-audit`. Obraz `n8n` w `docker-compose.yml` jest przypięty do wersji `2.45.5`.
- Kontenery aplikacji i kokpitu działają jako użytkownik bez uprawnień administratora, z `no-new-privileges` i bez dodatkowych uprawnień systemowych.

### 10. Webhooki (tryb `cloud`)
- Organizacja może zarejestrować adres HTTPS powiadamiany po zakończeniu runu (`run.finished`). Treść zdarzenia zawiera wyłącznie identyfikatory
  i statusy, nigdy treść raportu ani danych klienta; każde wywołanie jest podpisane (`X-DaaS-Signature`, HMAC-SHA256).
- Adres musi być publiczny (ochrona przed SSRF), przekierowania nie są śledzone, dostawa jest ponawiana trzykrotnie.

### 11. Kopie zapasowe i retencja
- Operator wykonuje kopie `deploy/backup.sh` (archiwum z datą UTC i sumą SHA-256, retencja liczby kopii). Kopie zawierają dane klientów, mają prawa 0600
  i powinny być szyfrowane oraz przechowywane poza serwerem. Odtworzenie: `deploy/restore.sh` z weryfikacją sumy.
- Retencja organizacji **nie obejmuje kopii zapasowych**: dane usunięte z serwera pozostają w starszych archiwach do czasu ich rotacji. Dobierz liczbę przechowywanych kopii do obietnicy złożonej klientowi.
