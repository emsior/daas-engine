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
  - Ustawienie retencji uploadów (domyślnie 90 dni) w ustawieniach organizacji.
  - Dla integracji: klucz API z minimalnym zakresem (np. `uploads:write`, `runs:write`, `reports:read`), pokazany jeden raz i przekazany bezpiecznym kanałem.

- [ ] **Krok 4: Uruchomienie pierwszego raportu baseline**
  - Wykonanie pipeline'u dla danych historycznych.
  - Wygenerowanie raportu HTML i weryfikacja poprawności wyliczeń (przychód łączny, marża %).
  - Sprawdzenie skrótu SHA-256 raportu (blok „Sprawdź sam” w kokpicie).

- [ ] **Krok 5: Prezentacja wyników i odbiór feedbacku**
  - Przesłanie raportu do klienta i krótka sesja podsumowująca (15 min).
  - Ustalenie harmonogramu dostarczania kolejnych tygodniówek (ręcznie, harmonogram n8n albo webhook `run.finished`).
