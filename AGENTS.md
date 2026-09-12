# DaaS Engine - zasady pracy agenta

Repozytorium jest **publiczne**. To dyktuje wszystkie zasady ponizej.
Marka: **PPC / prochpc.pl**, kontakt: `prochpc@gmail.com`.

## Stack

Python 3.11, FastAPI, DuckDB, pandas, pytest, n8n.
Endpointy: `/upload` (CSV/XLSX klienta, mapper PL) -> `/run` ->
`/reports/{pipeline}/latest?fmt=html`.
Uruchomienie natywne: `URUCHOM_DAAS_PYTHON.ps1` (Docker na maszynie deweloperskiej
nie startuje - do naprawy dopiero pod self-host klienta).

## Twarde zasady - obowiazuja zawsze

1. **Slownictwo.** Projekt dotyczy analityki danych. Tematyka CS2 wystepuje wylacznie
   jako *esports statistics*. Slownictwo z domeny hazardowej jest zakazane
   w kodzie, komentarzach, commitach, README i dokumentacji (powod prawny:
   art. 110a k.k.s.). Pelna lista zakazanych fraz jest w agencie `opsec-guard`,
   trzymanym lokalnie poza repozytorium.
2. **Zero danych osobowych wlasciciela.** Zadnego nazwiska, zadnego prywatnego adresu
   e-mail w plikach, commitach ani metadanych. Jedyny adres kontaktowy w repo:
   `prochpc@gmail.com`.
3. **Zakaz `git push`** bez wyraznej zgody wlasciciela w danej sesji.
   Commit lokalny - tak. Push - nie. Push wykonuje sie osobnym, swiadomym krokiem.
4. Nie dotykac `.secrets/` ani niczego, co wyglada na token, haslo, klucz API.
   Takie pliki nie moga trafic do indeksu gita.
5. Pliki demo maja byc **syntetyczne**. Zero realnych danych klientow (imiona,
   nazwiska, e-maile, NIP, telefony).
6. Zadnych lokalnych sciezek dyskowych (`C:\Users\...`, `D:\...`) w plikach repo.
7. Kwoty w formacie polskim (`7 255,95`) - nie zamieniac na format US.

## Git - konfiguracja wymagana przed pierwszym pushem

Konto ma wlaczone "Block command line pushes that expose my email", wiec w repo musi byc:

```
git config user.email "267137654+emsior@users.noreply.github.com"
git config user.name  "PPC"
```

## Agenci i komendy

Zainstalowane globalnie (poza repo): `opsec-guard`, `test-runner`, `report-builder`
oraz komendy `/opsec` i `/tydzien`.
Kolejnosc w cyklu tygodniowym: test-runner -> opsec-guard -> report-builder.
**Zaden z nich nie pushuje.**

Przed kazdym commitem: `/opsec`.

## Tryb wykonania: EAGER - twarda zasada operacyjna

Auto-wykonanie jest w trybie eager, sandbox wylaczony, dostep do plikow ALLOW.
Dlatego kazda operacja nieodwracalna albo sieciowa wymaga jawnej zgody wlasciciela
w osobnej wiadomosci:

- kasowanie czegokolwiek (Remove-Item, del, rmdir, rm),
- git reset --hard, git clean, git checkout -- ., przepisywanie historii,
- git push,
- wysylka plikow poza maszyne (curl -T, Invoke-WebRequest -Method Post/Put,
  scp, rsync, rclone, kopiowanie na UNC),
- robocopy /MIR i /PURGE.

Blokada techniczna to hook PreToolUse deny (~/.gemini/config/hooks.json +
hooks/deny-push.ps1), nie ten zapis. Jesli hook jest wylaczony (enabled: false)
albo nie wiesz, czy dziala - nie wykonuj takich operacji wcale, tylko wypisz
komende do recznego uruchomienia.
