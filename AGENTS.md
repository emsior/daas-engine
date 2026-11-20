# DaaS Engine — zasady dla agentów

Repozytorium jest **publiczne**. To dyktuje wszystkie zasady poniżej.
Kontekst projektu (stack, stan, pułapki środowiska) jest w `CLAUDE.md`.

## Twarde zasady — obowiązują zawsze

1. **Słownictwo.** Projekt dotyczy analityki danych. Tematyka CS2 występuje wyłącznie
   jako *esports statistics*. Słownictwo spoza domeny analityki danych, wskazane na liście
   zakazanych fraz projektu, jest zakazane w kodzie, komentarzach, commitach, README i dokumentacji.
   Pełna lista zakazanych fraz jest w agencie `opsec-guard`,
   trzymanym lokalnie poza repozytorium.
2. **Zero danych osobowych właściciela.** Żadnego nazwiska, żadnego prywatnego adresu
   e-mail w plikach, commitach ani metadanych. Marka: **PPC / prochpc.pl**.
   Jedyny adres kontaktowy w repo: `prochpc@gmail.com`.
3. **Zakaz `git push`** bez wyraźnej zgody właściciela w danej sesji.
   Commit lokalny — tak. Push — nie. Push wykonuje się osobnym, świadomym krokiem.
4. Nie dotykać `.secrets/` ani niczego, co wygląda na token, hasło, klucz API.
   Takie pliki nie mogą trafić do indeksu gita.
5. **Dane klienta.** Każdy plik z danymi realnego klienta, który trafi do repo, jest
   incydentem RODO. Pliki demo mają być **syntetyczne** (`data/fixtures/`): zero imion,
   nazwisk, e-maili, NIP-ów, telefonów.
6. Żadnych lokalnych ścieżek dyskowych (`C:\Users\...`, `D:\...`) w plikach repo.
7. Kwoty w formacie polskim (`7 255,95`) — nie zamieniać na format US.
8. **CODE FREEZE od v1.0.0 (28.09.2026) — uchylony poleceniem właściciela 09.10.2026.** Kod 2.0.0 jest na `main`
   (gałąź `claude/saas-2026-10` scalona 18.10.2026), bieżące prace idą przez gałęzie robocze i PR; zmiany w `app/`, `tests/`, `web/`, `deploy/` i `alembic/` są dozwolone w zakresie
   zadania. Pozostałe zasady z tej listy obowiązują bez zmian. Dokumentacja nigdy nie podlegała zamrożeniu.
9. **`web/` bez zewnętrznych CDN.** Kokpit nie ładuje fontów, skryptów, stylów ani obrazów z cudzych domen: fonty
   systemowe, zasoby z własnego originu, CSP z nonce bez `unsafe-inline`. Nowe zależności npm tylko jako
   devDependencies albo po uzgodnieniu, zawsze z zaktualizowanym `package-lock.json`.

## Git

Konto ma włączone „Block command line pushes that expose my email", więc w repo musi być:

```
git config user.email "267137654+emsior@users.noreply.github.com"
git config user.name  "PPC"
```

- Pliki do commita wybiera się jawnie: nigdy `git add .` ani `git add -A`.
- Publikacja wyłącznie przez gałąź roboczą i Pull Request. Push na `main`, force push
  i kasowanie gałęzi zdalnych wymagają jednorazowej zgody właściciela (globalny hook pre-push).
- Zakaz omijania hooków (`--no-verify` i podobne) oraz przepisywania opublikowanej historii.

## Jak pracować

1. Najpierw plan: 3 punkty i lista plików do zmiany. Czekaj na „ok".
2. Jedna rzecz na raz. Zmiany poza zakresem zadania to błąd, nie bonus.
3. Nie instaluj pakietów i nie dodawaj technologii bez pytania.
4. Po zmianie w kodzie uruchom `pytest -p no:cacheprovider` i pokaż wynik; po zmianie w `web/` dodatkowo
   `npm run typecheck && npm run lint && npm test` w katalogu `web/`. Wynik poniżej stanu bazowego z `CLAUDE.md` oznacza regresję.
5. Nie pisz README od zera — zaczyna się od tego, co dostaje klient, i to jest celowe.

## Agenci i komendy

Zainstalowane globalnie (poza repo): `opsec-guard`, `test-runner`, `report-builder`
oraz komendy `/opsec` i `/tydzien`.
Kolejność w cyklu tygodniowym: test-runner -> opsec-guard -> report-builder.
**Żaden z nich nie pushuje.**

Przed każdym commitem: `/opsec`.

## Operacje nieodwracalne i sieciowe — twarda zasada operacyjna

Agenci mogą działać z automatycznym wykonywaniem komend.
Dlatego każda operacja nieodwracalna albo sieciowa wymaga jawnej zgody właściciela
w osobnej wiadomości:

- kasowanie czegokolwiek (Remove-Item, del, rmdir, rm),
- git reset --hard, git clean, git checkout -- ., przepisywanie historii,
- git push,
- wysyłka plików poza maszynę (curl -T, Invoke-WebRequest -Method Post/Put,
  scp, rsync, rclone, kopiowanie na UNC),
- robocopy /MIR i /PURGE.

Blokada techniczna to hook blokujący takie komendy, skonfigurowany na maszynie właściciela
poza repozytorium, nie ten zapis. Jeśli blokada jest wyłączona albo nie wiesz, czy działa —
nie wykonuj takich operacji wcale, tylko wypisz komendę do ręcznego uruchomienia.

## Zasady PPC dla Codexa (15.10.2026, rejestr STACK v3)

Wykonawcę i recenzenta wyznacza właściciel i role się zmieniają; niezmienne jest jedno: autor zmiany jej nie recenzuje (zmiany Codexa recenzuje Claude, zmiany Claude'a recenzuje Codex). Pełne reguły: globalne zasady Codexa v3 (poza repozytorium) oraz `STACK.md` w warsztacie. Publiczny opis procesu: [`docs/PROCESS.md`](docs/PROCESS.md). Skrót obowiązujący w tym repo bez wyjątków:

1. **Zero nazwiska właściciela** w plikach, commitach, logach i promptach. Marka Proch PC / PPC, jedyny kontakt `prochpc@gmail.com`; stary adres z konta konektorów nie istnieje w treściach.
2. **Zakazane słownictwo** (lista zakazanych fraz projektu, jak w zasadzie 1 wyżej) w kodzie, commitach, docs i nazwach plików. Publicznie: „aplikacja analityczna z modelem EV” / *esports statistics*. Wzorce sprawdza skill `ppc-wyciek`; lista zostaje poza repozytorium.
3. **Git tylko po ścieżkach:** `git add <plik>`, nigdy `-A` ani `.`; nigdy `--no-verify`; nie zmieniaj `core.hooksPath`; nie przepisuj historii. Tożsamość: `user.name = PPC`, `user.email = 267137654+emsior@users.noreply.github.com`. Commit tylko gdy spec na to pozwala, na gałęzi roboczej, komunikat bez nazwiska i zakazanych fraz.
4. **Zero pusha, publikacji i zmian `main`** — bez wyjątków; push robi właściciel osobnym krokiem.
5. **Zero `.env`, `.secrets/`, kluczy, tokenów i danych klienta** — nie czytaj, nie cytuj, nie commituj. Dane testowe wyłącznie syntetyczne.
6. **Sandbox:** `workspace-write`, sieć wyłączona (konfiguracja Codexa poza repozytorium). Instalacje (`pip`/`npm`/`composer`), Docker, sieć i kasowanie tylko gdy spec jawnie na to pozwala. Poza katalogiem repo nie piszesz.
7. **Testy przed oddaniem:** komendy z `CLAUDE.md` tego repo; wynik liczbą z datą; wynik poniżej bazowego = regresja. Nie możesz uruchomić → 🚫 + powód, nigdy „zielone” bez dowodu.
8. **Wzorzec zadania:** ŹRÓDŁO (co czytasz) · OPERACJA (co wolno zmienić) · ZAKAZ (czego nie robisz) · DOWÓD (co pokazujesz: test, diff, plik). Brak pola → pytasz w bloku, nie zgadujesz. Zmiany poza zakresem to błąd, nie bonus; znalezione obok = obserwacja, nie poprawka.
9. **Wynik = blok dla Claude**, dopisany na końcu `DO_CLAUDE.md` w kanale `.handoff` warsztatu (append-only, UTF-8, nic nie kasuj):
   ```
   === DO CLAUDE === <data> — <ID-BLOKU>
   TASK: … / SCOPE: … / ROLE: wykonawca|reviewer / ROLE_VERSION: 3 / ASSIGNEE: codex. Autor zmian: …
   RESULT: DONE|ACCEPTED|CHANGES|BLOCKED — …
   FINDINGS: … (plik:linie, priorytet P1/P2/P3)
   EVIDENCE: … (komendy → wyniki z datą i godziną)
   CONFIDENCE: …
   ACTION_REQUIRED: …
   STATE_CHANGE: … (pliki, commity; zero pushy)
   RELEASED_BY: codex. ACCEPTED_BY: —
   === KONIEC ===
   ```
10. Ćwiczenia właściciela (znaczniki `TODO` z jego imieniem) zostawiasz właścicielowi. Pliki z polskimi znakami w UTF-8. W repozytorium z remote — zero lokalnych ścieżek dysku.
