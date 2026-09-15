# Wymagania dotyczące Danych Wejściowych (Input Requirements)

DaaS Engine obsługuje elastyczne formaty plików, jednak aby raport był w 100% precyzyjny, zalecamy spełnienie poniższych wytycznych:

### 1. Formaty plików
- Obsługiwane rozszerzenia: `.csv`, `.xlsx`, `.xls`, `.tsv`, `.txt`.
- Maksymalny rozmiar pliku w pilocie: **25 MB**.
- Kodowanie: UTF-8 lub Windows-1250 (auto-detekcja).

### 2. Wymagane informacje w wierszach
Plik powinien zawierać co najmniej informacje o:
- **Identyfikatorze zamówienia** (np. `ID`, `Numer`, `Order ID`, `Nr zamowienia`).
- **Dacie transakcji** (dowolny format daty, np. `2024-03-01`, `01.03.2024`, `01/03/2024`).
- **Przychodzie / Wartości sprzedaży** (kwota brutto lub netto, np. `1299.00`, `1 299,00 zł`).

### 3. Zalecane informacje opcjonalne (dla pełnych metryk P&L)
- **Koszt własny towaru (COGS / Zakup):** pozwala wyliczyć marżę kwotową i procentową.
- **Rabat:** procentowy (np. `10%`) lub kwotowy.
- **Produkt / Kategoria:** umożliwia stworzenie zestawienia TOP produktów i TOP kategorii.
- **Status zamówienia:** pozwala odfiltrować zwroty i anulowania (`Zrealizowane`, `Completed`, `Zwrot`, `Cancelled`).
