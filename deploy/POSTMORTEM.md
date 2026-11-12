# Postmortem — szablon (bez szukania winnego)

Wypełnienie: ok. 30 minut, w ciągu 3 dni roboczych od zamknięcia incydentu. Skopiuj ten plik do **prywatnych notatek operatora**. **Wypełnionego postmortemu nie wkładaj do publicznego repozytorium**: zawiera szczegóły awarii i może zawierać dane klienta. Do repo trafiają tylko wynikające z niego zmiany w `RUNBOOK.md`, [`ALERTS.md`](ALERTS.md) i [`SLO.md`](SLO.md).

Zasady: opisujemy **role i systemy, nie osoby** („operator", „skrypt wdrożenia", „monitor"). Pytamy, czemu system pozwolił na błąd albo go nie wykrył, nie kto go popełnił. Ocena prawna naruszenia ochrony danych nie należy do tego dokumentu. Żadnych adresów e-mail, nazw klientów, tokenów ani adresów IP w treści; klientów oznaczaj identyfikatorem organizacji lub liczbą.

---

**ID:** `PM-RRRR-MM-DD-n` · **Status:** szkic / zatwierdzony · **Ważność:** P1 (utrata danych, podejrzenie włamania, kod 12) / P2 (niedostępność > 60 min, kod 11) / P3 (reszta) · **Alert / wyzwalacz:** nazwa z `ALERTS.md` albo kod wyjścia deployu · **Wersja aplikacji:** X.Y.Z

## 1. Podsumowanie (3 zdania)

Co się stało, jaki był skutek dla klienta, jak przywrócono usługę.

## 2. Wpływ

| Wymiar | Wartość |
|---|---|
| Początek i koniec wpływu (UTC) | |
| Czas niedostępności lub degradacji (min) | |
| Dotknięte organizacje (liczba lub identyfikatory) | |
| Zadania: `failed` / `abandoned` / opóźnione | |
| Dane: utrata (od-do, jakie), uszkodzenie, ujawnienie, brak | |
| **Budżet błędu zużyty** (SLO-1: min z 216 i % budżetu; SLO-2/-3/-4: zdarzenia) | |
| Stan polityki budżetu po incydencie ([`SLO.md`](SLO.md) §5) | zielony / żółty / czerwony |

Pamiętaj o księgowaniu ręcznym: czas w trybie serwisowym i odtwarzania z kopii liczy się do SLO-1, mimo że `/ready` mogło odpowiadać 200.

## 3. Oś czasu (UTC)

| Czas (UTC) | Zdarzenie | Dowód (log, alert, komenda) |
|---|---|---|
| | wydarzenie wyzwalające (np. wdrożenie, kopia, zmiana konfiguracji) | |
| | początek wpływu na klienta | |
| | wykrycie (alert, klient, operator) | |
| | pierwsza reakcja | |
| | diagnoza: ustalona przyczyna | |
| | mitygacja (co zatrzymało szkody) | |
| | przywrócenie usługi (potwierdzone smoke / sondą) | |
| | zamknięcie incydentu | |

Czasy pochodne: wykrycie = od początku wpływu do wykrycia · reakcja = od wykrycia do pierwszej czynności · mitygacja · pełne przywrócenie.

## 4. Wykrycie

- Kto albo co zauważyło pierwsze: alert (który) / monitor zewnętrzny / klient / operator przy rutynowej kontroli.
- Czy alert zadziałał: tak / z opóźnieniem (ile) / nie. Jeśli nie lub z opóźnieniem: czego zabrakło (próg, okno, brak instrumentacji L-x, kanał, tłumienie)?
- Czy e-mail został zauważony w czasie, jaki zakłada ważność (`page` ok. 1 h, `ticket` następny dzień roboczy)?

## 5. Przyczyny (5 × dlaczego)

Przyczyna bezpośrednia: …

1. Dlaczego wystąpił skutek? …
2. Dlaczego (1)? …
3. Dlaczego (2)? …
4. Dlaczego (3)? …
5. Dlaczego (4)? … (zwykle tu leży luka w procesie, teście, alercie lub dokumentacji)

Czynniki sprzyjające (obciążenie, pora, nietypowe dane, zmiana tego samego dnia): …
Co system powinien był zablokować albo wykryć wcześniej: …

## 6. Co zadziałało, co nie

| Zadziałało | Nie zadziałało / zabrakło |
|---|---|
| (np. kopia przed wdrożeniem, automatyczny rollback, runbook, alert) | (np. brak alertu, niejasny krok runbooka, nieczytelny log) |

## 7. Działania naprawcze

Maks. 5, każde z właścicielem (rola) i terminem. Typ: **zapobiega** (usuwa przyczynę) / **wykrywa** (alert, test) / **łagodzi** (skraca czas lub skutek).

| # | Działanie | Typ | Właściciel (rola) | Termin | Status |
|---|---|---|---|---|---|
| 1 | | | | | |

Role: operator, Inżynier DevOps, Backend, SRE, Obserwowalność. Działania w kodzie aplikacji zgłasza się Backendowi jako osobne zadanie.

## 8. Powiadomienie klienta

| Pole | Wpis |
|---|---|
| Decyzja | tak / nie / nie dotyczy |
| Kiedy i jak wysłano (UTC) | |
| Kto zdecydował (rola) | operator |
| Uzasadnienie (fakty, 1–2 zdania: czy dane zostały utracone, uszkodzone lub ujawnione; ile trwał przestój; czy dotyczy kilku organizacji) | |

Podstawa postępowania: `RUNBOOK.md` §9 pkt 6 oraz `docs/DPA_v1_PL.md` §4 pkt 5 (szkic do przeglądu prawnego). Ten szablon nie zawiera treści prawnych; kwalifikację zdarzenia jako naruszenia ochrony danych i terminy ustala operator na podstawie podpisanej umowy. Wiadomość do klienta zawiera fakty, nie domysły, i jest uzupełniana, gdy ustalenia się zmienią.

## 9. Lekcje dla runbooka i alertów

- [ ] Runbook: sekcja ……… zaktualizowana / dodana (`RUNBOOK.md` lub `ALERTS.md`)
- [ ] Alert: dodany / zmieniony / usunięty (nazwa) …; ma sekcję postępowania w `ALERTS.md`
- [ ] Luka instrumentacji zgłoszona (L-x) …
- [ ] SLO: cel lub okno do rewizji? …
- [ ] Toil: czy ręczna praca w tym incydencie da się zautomatyzować? …
- [ ] Test w stagingu (`staging.sh`) odtwarza ten scenariusz? tak / nie → zadanie

## 10. Kontrola przed zapisem

- [ ] Brak nazwisk, adresów e-mail, nazw klientów, tokenów i adresów IP.
- [ ] Opisano systemy i role, nie osoby.
- [ ] Zapisano w prywatnych notatkach, nie w repozytorium.
