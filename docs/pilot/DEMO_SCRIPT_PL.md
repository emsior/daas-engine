# Scenariusz Demonstracji dla Klienta (Demo Script)

**Czas trwania:** 5–7 minut  
**Cel:** Pokazanie drogi od surowego pliku CSV lub XLSX do przejrzystego raportu zarządczego.  
**Przygotowanie:** organizacja demonstracyjna w kokpicie (tryb `cloud`) albo lokalna instancja (`docker compose up`); plik `data/fixtures/client_export_pl.csv` (dane syntetyczne).

---

### Krok 1: Wprowadzenie problemu (1 min)
- „Większość e-commerce ma dane w plikach CSV/Excel, ale wyciągnięcie marży per produkt i wykrycie transakcji ze stratą wymaga żmudnej pracy analityka lub Excela.”

### Krok 2: Wrzucenie pliku (2 min)
- Pokazujemy surowy plik `data/fixtures/client_export_pl.csv` (polskie nagłówki: „Numer zamówienia”, „Wartość brutto”, „Koszt zakupu”, „Rabat”). Ten sam kreator przyjmie plik `.xlsx` (bez makr).
- Logowanie do kokpitu linkiem z e-maila (bez hasła), ekran **Wgraj plik**.
- Kreator od razu pokazuje rozpoznanie kolumn (zgodność 100 %), liczbę wierszy (15) i podgląd mapowania.
- Wariant dla integratora: ta sama operacja przez API kluczem organizacji: `POST /api/v1/orgs/{id}/uploads` z nagłówkiem `X-API-Key` (przykłady `curl`: `docs/API.md`).

### Krok 3: Wygenerowanie raportu (1 min)
- Start zadania przyciskiem; postęp na żywo („wiersz X z Y”), całość trwa ułamek sekundy (przetwarzanie w pamięci DuckDB).
- Wariant API: `POST /api/v1/orgs/{id}/jobs`, odpowiedź `202`, potem raport pod `.../runs/{run_id}/report`.

### Krok 4: Prezentacja raportu HTML (2 min)
- Raport otwiera się w kokpicie (ramka izolowana); można go pobrać jako HTML, Markdown lub JSON.
- Omówienie sekcji:
  1. **KPI:** Przychód łączny, Koszt, Czysty Zysk, Średnia Marża %.
  2. **Wykryte Anomalie:** Zamówienia z marżą ujemną (np. zbyt duży rabat łączony).
  3. **Wnioski praktyczne:** Rekomendacje dla zespołu sprzedaży.
  4. **Dynamika WoW:** Zmiana przychodu i marży w stosunku do poprzedniego tygodnia.
- Blok **Sprawdź sam**: skrót SHA-256 pobranego pliku liczony w przeglądarce; plik nie jest wysyłany na serwer.

### Krok 5: Zamknięcie i propozycja pilota (1 min)
- „Możemy uruchomić taki raport dla Twoich danych już od następnego poniedziałku bez żadnych zmian w Twojej strukturze IT.”
