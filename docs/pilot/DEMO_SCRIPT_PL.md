# Scenariusz Demonstracji dla Klienta (Demo Script)

**Czas trwania:** 5–7 minut  
**Cel:** Pokazanie drogi od surowego pliku CSV do przejrzystego raportu zarządczego.

---

### Krok 1: Wprowadzenie problemu (1 min)
- „Większość e-commerce ma dane w plikach CSV/Excel, ale wyciągnięcie marży per produkt i wykrycie transakcji ze stratą wymaga żmudnej pracy analityka lub Excela.”

### Krok 2: Wrzucenie pliku — Endpoint `/upload` (2 min)
- Pokazujemy surowy plik `data/fixtures/client_export_pl.csv` (polskie nagłówki: 'Numer zamówienia', 'Wartość brutto', 'Koszt zakupu', 'Rabat').
- Wysłanie pliku na endpoint `/upload`.
- Wyświetlenie natychmiastowej odpowiedzi: automatyczne rozpoznanie kolumn (confidence: 1.0), podgląd liczby wierszy (15) i gotowy payload.

### Krok 3: Wygenerowanie raportu — Endpoint `/run` (1 min)
- Uruchomienie pipeline'u na przesłanych danych.
- Czas wykonania: ułamek sekundy (przetwarzanie w pamięci DuckDB).

### Krok 4: Prezentacja raportu HTML (2 min)
- Otwarcie raportu w przeglądarce (`runtime/reports/..._latest.html`).
- Omówienie sekcji:
  1. **KPI:** Przychód łączny, Koszt, Czysty Zysk, Średnia Marża %.
  2. **Wykryte Anomalie:** Zamówienia z marżą ujemną (np. zbyt duży rabat łączony).
  3. **Wnioski praktyczne:** Rekomendacje dla zespołu sprzedaży.
  4. **Dynamika WoW:** Zmiana przychodu i marży w stosunku do poprzedniego tygodnia.

### Krok 5: Zamknięcie i propozycja pilota (1 min)
- „Możemy uruchomić taki raport dla Twoich danych już od następnego poniedziałku bez żadnych zmian w Twojej strukturze IT.”
