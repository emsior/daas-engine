# Wymagania dotyczące Danych Wejściowych (Input Requirements)

DaaS Engine obsługuje elastyczne formaty plików, jednak aby raport był w 100% precyzyjny, zalecamy spełnienie poniższych wytycznych:

### 1. Formaty plików
- Obsługiwane rozszerzenia: `.csv` i `.xlsx`. Plik w innym formacie (`.xls`, `.tsv`, `.txt`) zapisz najpierw jako CSV albo XLSX.
- Kodowanie CSV: UTF-8 lub Windows-1250 (auto-detekcja); separator `;`, `,` lub tabulator.
- **CSV:** do 10 MiB i 100 000 wierszy danych.
- **XLSX:** do 10 MiB, do 200 000 wierszy i 256 kolumn. Skoroszyt bez makr (plik `.xlsm` i skoroszyty z makrami są odrzucane), z osadzonymi obiektami też nie. Gdy skoroszyt ma kilka arkuszy, wskaż arkusz z danymi (pole `sheet` w API). Formuły nie są liczone: odczytywane są zapisane wartości.
- Pilot przewiduje pliki do 25 MB. Plik większy niż 10 MiB operator przetwarza lokalnie na instancji pilota albo prosi o podział na części.
- Paczka z lokalnego kolektora artefaktów (`.json`, pipeline `uksc_evidence`): do 5 MiB, zgodna ze schematem kolektora.

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

### 4. Czego nie wysyłać
Dane transakcyjne wystarczą. Nie dołączaj imion, nazwisk, adresów, telefonów, adresów e-mail ani numerów kart konsumentów (zob. `SECURITY_SCOPE_PL.md`, sekcja 2).
