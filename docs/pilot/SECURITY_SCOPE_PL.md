# Zakres Bezpieczeństwa i Ochrony Danych (Security Scope)

Dokument opisuje gwarancje techniczne i operacyjne dotyczące bezpieczeństwa danych klienta przetwarzanych w ramach usługi DaaS Engine.

### 1. Izolacja Danych (Multi-Tenancy)
- Każdy klient posiada wydzieloną, dedykowaną przestrzeń w katalogu `runtime/tenants/{tenant_id}/`.
- Pliki przesłane przez klienta są ściśle ograniczone do podkatalogu `uploads/` danego tenanta.
- Wdrożono rygorystyczną ochronę przed atakami Path Traversal — silnik odrzuca próby wyjścia poza dozwolony korzeń roboczy (`workspace`).

### 2. Dane Osobowe (RODO / GDPR)
- Silnik analityczny **nie wymaga i nie przechowuje danych osobowych konsumentów** (imion, nazwisk, adresów zamieszkania, numerów telefonów, adresów e-mail czy numerów kart płatniczych).
- Klient jest instruowany, by eksportować wyłącznie dane transakcyjne (identyfikator, kwota, kategoria, data).

### 3. Tajemnica Przedsiębiorstwa i Kontrola Dostępu
- Raporty i dane analityczne nie są indeksowane ani udostępniane publicznie.
- Unikalne identyfikatory raportów generowane są w oparciu o losowe identyfikatory o wysokiej entropii (UUIDv4).
- Zastosowanie warstwy audytowej (append-only audit log) rejestrującej każde uruchomienie narzędzia analitycznego.
