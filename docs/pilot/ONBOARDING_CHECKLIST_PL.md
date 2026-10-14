# Lista Kontrolna Wdrożenia Pilota (Onboarding Checklist)

Krok po kroku dla operatora (PPC) i klienta przed rozpoczęciem pilota:

- [ ] **Krok 1: Wstępna rozmowa i ustalenie celów**
  - Uzgodnienie, na jakie pytania biznesowe raport ma odpowiedzieć (marża, anomalie rabatowe, spadki WoW).
  - Wskazanie osoby kontaktowej po stronie klienta.

- [ ] **Krok 2: Próbka danych (Sanity Check)**
  - Otrzymanie próbnego pliku CSV/XLSX (np. 1–2 dni transakcji).
  - Sprawdzenie zgodności z `INPUT_REQUIREMENTS_PL.md`.
  - Przetestowanie mapowania kolumn w kreatorze uploadu (albo `POST /api/v1/orgs/{id}/uploads`).

- [ ] **Krok 3: Organizacja klienta i umowa**
  - Założenie organizacji: właściciel loguje się w kokpicie linkiem z e-maila i akceptuje umowę powierzenia (DPA v1), albo operator tworzy ją poleceniem `python -m app.cli create-org --name ... --owner-email ...`.
  - Organizacja ma własny katalog danych (`runtime/tenants/{org_id}/`) i własną bazę; identyfikator nadaje system.
  - Ustawienie retencji (domyślnie 90 dni) w ustawieniach organizacji: dotyczy uploadów (od dnia wgrania), plików raportów i danych przebiegów. Kopie zapasowe operatora nie podlegają tej retencji: ustal z klientem, jak długo je przechowujesz.
  - Dla integracji: klucz API z minimalnym zakresem (np. `uploads:write`, `runs:write`, `reports:read`), pokazany jeden raz i przekazany bezpiecznym kanałem.

- [ ] **Krok 4: Uruchomienie pierwszego raportu baseline**
  - Wykonanie pipeline'u dla danych historycznych.
  - Wygenerowanie raportu HTML i weryfikacja poprawności wyliczeń (przychód łączny, marża %).
  - Sprawdzenie skrótu SHA-256 raportu (blok „Sprawdź sam” w kokpicie).

- [ ] **Krok 5: Prezentacja wyników i odbiór feedbacku**
  - Przesłanie raportu do klienta i krótka sesja podsumowująca (15 min).
  - Ustalenie harmonogramu dostarczania kolejnych tygodniówek (ręcznie, harmonogram n8n albo webhook `run.finished`).

## Wariant compliance: raport statusu artefaktów (pipeline `uksc_evidence`)

Dla wertykału compliance (nie e-commerce) ścieżka jest inna — zamiast eksportu sprzedaży klient dostarcza paczkę JSON z lokalnego kolektora artefaktów stacji Windows. Raport jest **zapisem stanu technicznego artefaktów** (kontrola → wymóg UKSC/NIS2 → status → skrót artefaktu); nie jest oceną prawną ani potwierdzeniem zgodności.

- [ ] **C1: Zebranie paczki na stacji**
  - Uruchomienie `tools/uksc-collector.ps1` na stacji Windows (tryb tylko do odczytu, jako administrator; bez uprawnień administratora część kontroli wychodzi `NA_NO_ADMIN`). Kolektor tworzy `uksc_<host>_<stamp>.json` (+ plik `.sha256`) — nic nie opuszcza stacji, dopóki klient sam nie wgra paczki.
- [ ] **C2: Wgranie paczki trasą v1**
  - Paczka `.json` (do 5 MiB) idzie trasą v1: `POST /api/v1/orgs/{org}/uploads` (w selfhost `org` = `default`, klucz `DAAS_API_KEY`). Legacy `POST /upload` przyjmuje tylko CSV/XLSX i odrzuci `.json`. Upload wykrywa `kind=package` i podpowiada `uksc_evidence`.
- [ ] **C3: Uruchomienie `uksc_evidence`**
  - `POST /api/v1/orgs/{org}/jobs {"pipeline":"uksc_evidence","upload_id":"..."}` (ten pipeline nie przyjmuje okresu). Po `succeeded` pobranie raportu HTML i `GET /api/v1/orgs/{org}/evidence` (kontrola, wymóg, status, skrót artefaktu, `package_sha256`, `integrity`).
- [ ] **C4: Weryfikacja i zmiany między runami**
  - `package_sha256` odtwarzalny niezależnie (opis w raporcie i w `docs/API.md`); `POST .../verify` sprawdza skrót PLIKU RAPORTU, nie paczki.
  - Kolejny run tej samej stacji pokazuje `diff_vs_previous` (poprawy/regresje). Pełny worked-example: [`../API.md`](../API.md#status-of-collector-artifacts-uksc_evidence-end-to-end).
