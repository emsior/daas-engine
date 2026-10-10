# DaaS Engine — zasady dla agentów

Repozytorium jest **publiczne**. To dyktuje wszystkie zasady poniżej.
Kontekst projektu (stack, stan, pułapki środowiska) jest w `CLAUDE.md`.

## Twarde zasady — obowiązują zawsze

1. **Słownictwo.** Projekt dotyczy analityki danych. Tematyka CS2 występuje wyłącznie
   jako *esports statistics*. Słownictwo z domeny hazardowej jest zakazane
   w kodzie, komentarzach, commitach, README i dokumentacji (ze względów prawnych).
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
8. **CODE FREEZE od v1.0.0 (28.09.2026) — uchylony poleceniem właściciela 09.10.2026.** Prace nad wersją 2.0.0
   idą na gałęzi `claude/saas-2026-10`; zmiany w `app/`, `tests/`, `web/`, `deploy/` i `alembic/` są dozwolone w zakresie
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

## Tryb wykonania: EAGER — twarda zasada operacyjna

Auto-wykonanie jest w trybie eager, sandbox wyłączony, dostęp do plików ALLOW.
Dlatego każda operacja nieodwracalna albo sieciowa wymaga jawnej zgody właściciela
w osobnej wiadomości:

- kasowanie czegokolwiek (Remove-Item, del, rmdir, rm),
- git reset --hard, git clean, git checkout -- ., przepisywanie historii,
- git push,
- wysyłka plików poza maszynę (curl -T, Invoke-WebRequest -Method Post/Put,
  scp, rsync, rclone, kopiowanie na UNC),
- robocopy /MIR i /PURGE.

Blokada techniczna to hook PreToolUse deny (~/.gemini/config/hooks.json +
hooks/deny-push.ps1), nie ten zapis. Jeśli hook jest wyłączony (enabled: false)
albo nie wiesz, czy działa — nie wykonuj takich operacji wcale, tylko wypisz
komendę do ręcznego uruchomienia.
