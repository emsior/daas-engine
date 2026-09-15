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
uwierzytelnianie użytkownika końcowego. Dostęp do instancji zabezpiecza się
na poziomie sieci wdrożenia.

### 2. Dane Osobowe (RODO / GDPR)
- Silnik analityczny **nie wymaga i nie przechowuje danych osobowych konsumentów** (imion, nazwisk, adresów zamieszkania, numerów telefonów, adresów e-mail czy numerów kart płatniczych).
- Klient jest instruowany, by eksportować wyłącznie dane transakcyjne (identyfikator, kwota, kategoria, data).

### 3. Tajemnica Przedsiębiorstwa i Kontrola Dostępu
- Raporty i dane analityczne nie są indeksowane ani udostępniane publicznie.
- Unikalne identyfikatory raportów generowane są w oparciu o losowe identyfikatory o wysokiej entropii (UUIDv4).
- Zastosowanie warstwy audytowej (append-only audit log) rejestrującej każde uruchomienie narzędzia analitycznego.
