# DaaS Engine — Raporty Zarządcze bez Zmiany Twojego Systemu

### Co robimy?
Dostarczamy gotowe, cotygodniowe raporty rentowności, marżowości i anomalii sprzedażowych dla e-commerce i MŚP bezpośrednio z Twoich bieżących eksportów (CSV / Excel).

### Jak to działa?
1. **Dostarczasz plik:** Raz w tygodniu wgrywasz zwykły eksport ze swojego sklepu lub systemu ERP w kokpicie (logowanie linkiem z e-maila) albo przesyłasz go przez prosty link/webhook lub API.
2. **Automatyczne przetwarzanie:** Silnik DaaS Engine mapuje kolumny, weryfikuje poprawność liczb, wylicza kluczowe metryki P&L (przychód, koszty, marża %, AOV) i wykrywa transakcje stratne.
3. **Gotowy raport:** Otrzymujesz przejrzysty raport HTML z wnioskami biznesowymi i podsumowaniem zmian tydzień-do-tygodnia (WoW), do pobrania także jako Markdown i JSON.

### Dlaczego to ma sens?
- **Zero wdrożeń:** Nie musisz integrować skomplikowanych narzędzi BI ani płacić za licencje użytkowników.
- **Działa z Twoimi danymi:** Polski format liczb (`1 299,00 zł`), elastyczne nagłówki, CSV i XLSX, obsługa zwrotów i rabatów.
- **Prywatność:** Twoja organizacja ma własny katalog danych i własną bazę; pliki nie trafiają do wspólnej bazy z danymi innych firm. Chcesz mieć wszystko u siebie? Ten sam silnik działa na Twoim serwerze (Docker lub `pip`).
- **Sprawdzisz sam:** Każdy raport ma skrót SHA-256; w kokpicie policzysz go lokalnie w przeglądarce, bez wysyłania pliku.
