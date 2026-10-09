# Zakres Bezpieczeństwa i Ochrony Danych (Security Scope)

Dokument opisuje gwarancje techniczne i operacyjne dotyczące bezpieczeństwa danych klienta przetwarzanych w ramach usługi DaaS Engine.

### 1. Izolacja Danych (model: jedna instancja na klienta)

Model wdrożenia to **single-tenant per deployment**: jeden klient otrzymuje własną
instancję aplikacji, własny proces i własny katalog runtime. Dane dwóch klientów
nigdy nie trafiają do wspólnej bazy ani wspólnego procesu — izolacja jest granicą
wdrożenia, a nie regułą wewnątrz współdzielonej aplikacji.

- Każde wdrożenie ma jeden katalog roboczy (`runtime root`), wskazany przez konfigurację instancji.
- Pliki przesłane przez klienta trafiają wyłącznie do podkatalogu `uploads/` tego wdrożenia.
- Nazwa pliku nadana przez klienta nie bierze udziału w budowie ścieżki zapisu —
  nazwę docelową (`<uuid>.csv`) generuje serwer.
- Ochrona przed Path Traversal: ścieżka docelowa jest rozwiązywana (`resolve()`)
  i sprawdzana pod kątem przynależności do katalogu roboczego metodą porównania
  komponentów ścieżki, nie porównania tekstowego.
- Plik trafia do lokalizacji docelowej dopiero po przejściu wszystkich kontroli;
  zapis tymczasowy jest usuwany przy każdym niepowodzeniu.

**Poza zakresem tej wersji:** współdzielona wielonajemczość w jednej instancji,
`tenant_id` we wspólnej bazie, role i uprawnienia użytkowników (RBAC) oraz
uwierzytelnianie użytkownika końcowego. Dostęp do API chroni jeden klucz
instancji (sekcja 4); dostęp do samej instancji zabezpiecza się dodatkowo
na poziomie sieci wdrożenia.

### 2. Dane Osobowe (RODO / GDPR)
- Silnik analityczny **nie wymaga i nie przechowuje danych osobowych konsumentów** (imion, nazwisk, adresów zamieszkania, numerów telefonów, adresów e-mail czy numerów kart płatniczych).
- Klient jest instruowany, by eksportować wyłącznie dane transakcyjne (identyfikator, kwota, kategoria, data).

### 3. Tajemnica Przedsiębiorstwa i Kontrola Dostępu
- Raporty i dane analityczne nie są indeksowane ani udostępniane publicznie.
- Unikalne identyfikatory raportów generowane są w oparciu o losowe identyfikatory o wysokiej entropii (UUIDv4).
- Zastosowanie warstwy audytowej (append-only audit log) rejestrującej każde uruchomienie narzędzia analitycznego.
- Log audytowy (`audit.jsonl`, jeden plik na instancję) rejestruje zdarzenia `upload`, `run` oraz `auth_denied`
  (odmowa dostępu). Wpisy zawierają znacznik czasu, rodzaj zdarzenia, decyzję, identyfikatory techniczne
  (`request_id`, `run_id`, `upload_id`), trasę i adres IP wywołującego; nie zawierają klucza API, ścieżek
  bezwzględnych ani treści danych klienta.

### 4. Uwierzytelnianie i sieć
- Wszystkie trasy poza `GET /health` i `GET /ready` wymagają nagłówka `X-API-Key`. Klucz ma co najmniej 16 znaków,
  jest porównywany w stałym czasie (skrót SHA-256) i nigdy nie trafia do odpowiedzi ani logów.
- Brak skonfigurowanego klucza nie oznacza dostępu otwartego: trasy chronione odpowiadają `401`, a start na adresie
  innym niż loopback jest odmawiany.
- Domyślny adres nasłuchu to `127.0.0.1`. Obraz kontenera ustawia `0.0.0.0` jawnie, a `docker-compose.yml`
  publikuje porty wyłącznie na `127.0.0.1`.
- Dokumentacja interaktywna (`/docs`, `/redoc`, `/openapi.json`) jest dostępna tylko przy `APP_ENV=dev`.
- `GET /health` i `GET /ready` nie ujawniają ścieżek ani konfiguracji; zwracają wyłącznie status, nazwę i wersję.
- Dostęp przez internet wymaga terminacji TLS po stronie wdrożenia (reverse proxy lub tunel); aplikacja nie obsługuje TLS.

### 5. Limity i odporność na nadużycia
- Limity żądań w pamięci procesu (algorytm token bucket): `POST /run` i `POST /upload` per klucz, wszystkie trasy chronione
  per adres IP, nieudane próby klucza per adres IP. Przekroczenie kończy się `429` z nagłówkiem `Retry-After`.
  Nieudane próby jednego adresu nie blokują poprawnego klucza.
- Kwota łączna plików w `uploads/` (domyślnie 1 GiB) egzekwowana w trakcie strumieniowania; przekroczenie kończy się `413`.
- Pojedynczy plik: do 10 MiB, do 100 000 wierszy (CSV), serwer generuje nazwę docelową. Pliki tymczasowe `*.tmp`
  są usuwane po niepowodzeniu oraz przy starcie.
- Limity działają w jednym procesie; start z liczbą workerów większą niż 1 jest blokowany.

### 6. Pliki wejściowe: CSV i XLSX
- `POST /upload` przyjmuje `.csv` oraz `.xlsx`. Plik XLSX jest sprawdzany jako kontener ZIP i konwertowany do CSV
  na tej samej ścieżce kontroli co plik CSV.
- Odrzucane są: skoroszyty z makrami (`vbaProject`, arkusze makr), osadzone obiekty, deklaracje DTD,
  archiwa o nadmiernym rozmiarze po rozpakowaniu lub współczynniku kompresji, więcej niż 20 arkuszy,
  powyżej 200 000 wierszy lub 256 kolumn. Skoroszyt z kilkoma arkuszami wymaga wskazania arkusza w polu `sheet`.
- Formuły nie są wykonywane; odczytywane są zapisane wartości.

### 7. Raporty i odpowiedzi API
- Wartości z danych klienta (identyfikatory, nazwy produktów, kategorie, nazwa klienta, nagłówki kolumn) są
  zamieniane na tekst przed złożeniem raportu HTML; renderer nie przepuszcza surowego HTML, linków ani obrazów.
- Odpowiedzi raportów niosą nagłówki `Content-Security-Policy` (bez skryptów i zasobów zewnętrznych),
  `X-Content-Type-Options: nosniff` i `Referrer-Policy: no-referrer`.
- Odpowiedzi API nie zawierają ścieżek bezwzględnych ani surowych komunikatów wyjątków. `POST /run`
  przyjmuje `upload_id` zamiast ścieżki pliku. Błędy mają postać `{detail, code, request_id}`; ten sam
  `request_id` jest w nagłówku odpowiedzi i w logach JSON, co pozwala powiązać zgłoszenie z wpisem w logu.

### 8. Dostawcy zewnętrzni
- Streszczenie generowane przez model językowy jest **domyślnie wyłączone**. Wymaga jawnego `DAAS_LLM_NARRATIVE=true`
  oraz klucza dostawcy; sam klucz w środowisku niczego nie włącza.
- Do dostawcy trafiają wyłącznie policzone metryki. Identyfikatory zamówień, nazwy produktów, nazwa klienta,
  nazwa hosta i skróty paczki dowodowej są usuwane z zapytania. Raport oznacza takie streszczenie
  jako wygenerowane przez model.
- Pozostałe źródła zewnętrzne (FACEIT, Liquipedia, Apify, Discord) działają tylko po skonfigurowaniu ich kluczy;
  bez nich pipeline używa danych przykładowych.

### 9. Łańcuch dostaw
- Zależności produkcyjne są przypięte do wersji i skrótów SHA-256 (`requirements.lock`); obraz Docker i CI
  instalują je z `--require-hashes`. Obraz `n8n` w `docker-compose.yml` jest przypięty do wersji `2.43.1`.
- Kontener działa jako użytkownik bez uprawnień administratora.
