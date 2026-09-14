# Lista Kontrolna Wdrożenia Pilota (Onboarding Checklist)

Krok po kroku dla operatora (PPC) i klienta przed rozpoczęciem pilota:

- [ ] **Krok 1: Wstępna rozmowa i ustalenie celów**
  - Uzgodnienie, na jakie pytania biznesowe raport ma odpowiedzieć (marża, anomalie rabatowe, spadki WoW).
  - Wskazanie osoby kontaktowej po stronie klienta.

- [ ] **Krok 2: Próbka danych (Sanity Check)**
  - Otrzymanie próbnego pliku CSV/XLSX (np. 1–2 dni transakcji).
  - Sprawdzenie zgodności z `INPUT_REQUIREMENTS_PL.md`.
  - Przetestowanie mapowania kolumn przez endpoint `/upload`.

- [ ] **Krok 3: Utworzenie przestrzeni tenanta**
  - Przygotowanie katalogu `runtime/tenants/{klient_id}/`.
  - Przypisanie bezpiecznego identyfikatora pilota.

- [ ] **Krok 4: Uruchomienie pierwszego raportu baseline**
  - Wykonanie pipeline'u dla danych historycznych.
  - Wygenerowanie raportu HTML/PDF i weryfikacja poprawności wyliczeń (przychód łączny, marża %).

- [ ] **Krok 5: Prezentacja wyników i odbiór feedbacku**
  - Przesłanie raportu do klienta i krótka sesja podsumowująca (15 min).
  - Ustalenie harmonogramu dostarczania kolejnych tygodniówek.
