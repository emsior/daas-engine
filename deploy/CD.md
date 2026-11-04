# Wydania i wdrożenia (CI/CD) DaaS Engine Cloud

Ten dokument opisuje drogę od taga do produkcji: pipeline wydania w GitHub Actions, wdrożenie na hoście przez `deploy/cd/deploy.sh`,
automatyczne wycofanie obrazu i to, co robić, gdy coś pójdzie źle. Instalacja ręczna, kopie i odtworzenie danych zostają w
[`README.md`](README.md) i [`RUNBOOK.md`](RUNBOOK.md); tu są tylko linki do nich.

Produkcja jest **włącznikiem**: dopóki zmienna `DAAS_CD_ENABLED` nie ma wartości `true`, każde wydanie kończy się na stagingu
i publikacji obrazów, a job `deploy` jest pomijany. Włącznik ustawiasz w dniu, w którym pierwszy klient zaczyna korzystać z trybu
`cloud` (checklista w sekcji b).

## a) Jak wygląda wydanie

```
tag vX.Y.Z ──► verify ──► test ──► images ──► staging ──► deploy (tylko gdy włączony) ──► release-notes
```

| Job | Co robi | Uprawnienia |
|---|---|---|
| `verify` | format tagu; wersja z tagiem w `pyproject.toml`, `app/core/config.py`, `web/package.json`; commit tagu jest przodkiem `origin/main`; head Alembica tagu i poprzedniego wydania; gitleaks na drzewie; `tools/opsec_scan.sh --tree` | `contents: read` |
| `test` | ruff + pełny pytest na Pythonie 3.12 (8–10 min); `pip-audit` tylko informacyjnie | `contents: read` |
| `images` | budowa `api` (korzeń repo) i `web` (`web/`, pusty `NEXT_PUBLIC_API_ORIGIN`), etykiety OCI `source`/`revision`/`version`, tagi `X.Y.Z` i `sha-<12>`, **bez `latest`**, publikacja w GHCR; wynik = digesty | `packages: write` |
| `staging` | `cd-staging.yml` na obrazach wydania po digestach: pełny stos, `deploy.sh` N-1 → N, smoke lokalny i przez Caddy, wstrzyknięta awaria, kopia, smoke selfhost | `packages: read` |
| `deploy` | `environment: production` (zatwierdza właściciel), SSH z wymuszoną komendą, `deploy <wersja> <digest api> <digest web> <sha> <0\|1>` | brak uprawnień do repozytorium |
| `release-notes` | GitHub Release: digesty, linia `schema: <poprzedni head> -> <nowy head>`, instrukcja selfhost (`docker pull ghcr.io/emsior/daas-engine-api:X.Y.Z@sha256:…`) | `contents: write` |

**Kto taguje i jak.** Wydanie to mały krok: podbij wersję w trzech plikach (`pyproject.toml`, `app/core/config.py`, `web/package.json`) w PR,
scal do `main`, potem utwórz tag `vX.Y.Z` na commicie z `main` i wypchnij sam tag. Push tagu wymaga zgody właściciela (`AGENTS.md`).
Tag nie może wskazywać commitu spoza historii `main`, a tag wersji w rejestrze jest niezmienny: obraz `X.Y.Z` nie zostanie nadpisany
(ponowne uruchomienie tego samego wydania używa istniejących obrazów, o ile pochodzą z tego samego commitu).

**Wydanie, które zmienia schemat bazy.** Gdy wydanie dodaje migrację Alembica, host odmówi wdrożenia (kod 2), dopóki nie uruchomisz
`Release` ręcznie (`Run workflow`, pole `tag` = istniejący tag) z zaznaczonym `allow_schema_change`. Takie wydanie wymaga kopii poza
serwerem (hak `deploy/cd/offsite-backup.hook`, sekcja „Kopia poza serwerem”) i **nie ma automatycznego wycofania**: po awarii zostaje
wariant B z README. To świadoma decyzja właściciela przy każdym takim wydaniu.

**Co robi host po otrzymaniu polecenia** (`deploy/cd/deploy.sh`, praca odłączona od sesji SSH, stan w `deploy/cd/state/`):

1. blokada (`flock`; dwa wdrożenia naraz są niemożliwe);
2. preflight: `.env` z prawami 0600, ≥ 3 GB wolnego miejsca, bieżąca wersja z `/api/v1/health`, `git fetch --tags`, **commit tagu musi być równy SHA z workflow**, `docker pull` obu obrazów po digeście i porównanie `RepoDigests`;
3. brama schematu: rewizja Alembica w `control.db` (odczyt z kopii pliku w kontenerze `maint`, bez sieci) kontra head kodu wydania;
4. drenaż: czekanie (do 10 min) aż `daas_jobs{status="running"}` spadnie do 0;
5. kopia zimna `backup.sh` + hak kopii poza serwerem;
6. checkout taga, pobranie obrazów pomocniczych (np. Caddy), atomowa podmiana `DAAS_VERSION`/`DAAS_API_IMAGE`/`DAAS_WEB_IMAGE` w `.env` (poprzedni `.env` jako `.env.prev`, 0600);
7. `docker compose up -d --no-build --pull never` (honoruje `COMPOSE_FILE` z `.env`);
8. `smoke.sh --wait 90 --expect-version X.Y.Z` lokalnie i `--public https://<domena>` (domena z `DAAS_DOMAIN`);
9. sukces: zostają obrazy N, N-1, N-2 (starsze usuwane po tagu, nigdy `prune -a`); porażka: rollback albo incydent wg tabeli niżej.

Na stdout (i w publicznym logu GitHub) wraca **wyłącznie** jedna linia:

```
DAAS_DEPLOY result=<ok|noop|rollback|blocked|failed> code=<n> version=<X.Y.Z> previous=<X.Y.Z|none> schema=<same|changed|unknown> duration=<s> maintenance=<on|off|unverified>
```

Pełny dziennik jest tylko na hoście: `deploy/cd/log/<id>.log` (prawa 0700, bez wysyłki do GitHub). Stan: `sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh status`
(kod 3 = operacja trwa, 4 = brak stanu). Zerwanie SSH niczego nie przerywa, a workflow dopytuje `status`; ponowne `deploy` z tymi samymi argumentami dołącza do trwającej
operacji, a gdy ta wersja już działa i smoke jest zielony, kończy się `result=noop`.

Profil `edge` (Caddy) jest domyślnie włączony. Host ze wspólnym proxy wpisuje do `.env` linię `COMPOSE_PROFILES=` (może być pusta); nakładki (`compose.llm.yml`,
obserwowalność) włącza `COMPOSE_FILE` w `.env`, tak jak w README.

## b) Włącznik produkcji: checklista właściciela (≈ 60 min)

Wyłącznie czynności, które wymagają Twojej tożsamości. Reszta (obrazy, skrypty, testy) jest już gotowa w repozytorium. Czasy są szacunkami ⚠.

| # | Krok | ⚠ min |
|---|---|---|
| 1 | **Konto u dostawcy VPS** (płatność, weryfikacja konta). Utwórz serwer: Ubuntu 24.04 lub Debian 12, 2 vCPU / 4 GB, region w UE. Dostawcę i koszt wybierasz sam; repozytorium jest neutralne. Konto możesz założyć wcześniej, bo weryfikacja bywa wieloetapowa | 15 |
| 2 | **Dwie pary kluczy** w PowerShellu (Windows 10/11 ma wbudowany OpenSSH): `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\daas_operator" -C operator` (z hasłem) oraz `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\daas_deploy" -C daas-deploy -N '""'` (bez hasła, to klucz CI) | 3 |
| 3 | **cloud-init:** w panelu wklej `deploy/host/cloud-init.yaml`, zastępując `<<OPERATOR_SSH_PUBLIC_KEY>>` zawartością `daas_operator.pub` i `<<DEPLOY_SSH_PUBLIC_KEY>>` zawartością `daas_deploy.pub` (tylko klucze publiczne, plik nie zawiera sekretów). Po starcie `cloud-init status --wait` pokaże `done` | 5 |
| 4 | **DNS:** rekord A (i AAAA) domeny produkcyjnej na adres serwera | 5 |
| 5 | **Logowanie operatora** w drugim oknie: `ssh -i "$env:USERPROFILE\.ssh\daas_operator" operator@<adres>`. Sprawdź `sudo ufw status` i `sudo sshd -T \| grep -E "passwordauthentication\|permitrootlogin"`. Zablokuj domyślne konto obrazu chmurowego po potwierdzeniu, że operator wchodzi | 5 |
| 6 | **`.env` z sekretami na hoście:** `sudo -u deploy bash -c 'cd /opt/daas-engine/deploy && ./gen-env.sh --domain <domena>'`, potem tryb `smtp` i dane przekaźnika w `sudo -u deploy nano /opt/daas-engine/deploy/.env` (README, „Poczta”). Zachowaj kopię `.env` w menedżerze sekretów. Sekrety aplikacji **nie trafiają do GitHub** | 10 |
| 7 | **Odcisk hosta:** `ssh-keyscan -t ed25519 <adres>` (wynik to wartość sekretu `DEPLOY_KNOWN_HOSTS`; porównaj odcisk z `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` na serwerze) | 3 |
| 8 | **GitHub → Settings → Environments → New environment `production`:** *Required reviewers* = Ty; *Deployment branches and tags* = *Selected* → dodaj regułę tagów `v*`. Sekrety środowiska: `DEPLOY_SSH_KEY` (cała zawartość pliku `daas_deploy`) i `DEPLOY_KNOWN_HOSTS`. Zmienne środowiska: `DEPLOY_HOST` (adres serwera), `DEPLOY_USER` (`deploy`), `DAAS_PUBLIC_URL` (`https://<domena>`). Potem usuń plik `daas_deploy` z dysku | 10 |
| 9 | **Zmienna repozytorium `DAAS_CD_ENABLED=true`** (Settings → Secrets and variables → Actions → Variables → *repository*, nie środowiska: warunek joba jest oceniany przed wyborem środowiska) | 1 |
| 10 | **Pierwsze wydanie:** tag `vX.Y.Z` (sekcja a). Po pierwszej publikacji pakiety `daas-engine-api` i `daas-engine-web` w GHCR ustaw jako **publiczne** (profil → *Packages* → pakiet → *Package settings* → *Change visibility*); host pobiera je bez logowania. Zatwierdź deployment w Actions. Pierwsze wdrożenie na pustych wolumenach nie wymaga `allow_schema_change` | 5 |
| 11 | **Po wdrożeniu:** pierwsza organizacja (README, krok 4, przez `sudo -u deploy docker compose exec …`) i monitor zewnętrzny z RUNBOOK §6 | — |

Sprawdzenie końcowe: job `deploy` kończy się zielono, a krok „Kontrola z zewnątrz” potwierdza wersję przez domenę (DNS, TLS, proxy).

## c) Rollback ręczny

```bash
sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh rollback          # wariant A: poprzedni obraz + kod poprzedniego commitu
sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh maintenance on    # tryb serwisowy (wstrzymanie zadań); off = powrót
```

`rollback` przechodzi przez tę samą bramę schematu co deploy: jeśli schemat bazy różni się od schematu poprzedniej wersji, kończy się kodem 2 i nic nie zmienia
(wtedy obowiązuje wariant B z [README](README.md#przywracanie-poprzedniej-wersji)). Przed rollbackiem robi kopię zimną. Działa tylko raz po wdrożeniu
(po udanym rollbacku `deploy/cd/state/previous.json` i `.env.prev` są usuwane). Rollback nie jest dostępny przez klucz SSH z workflow.

## d) Kody wyjścia

SRE: [`ALERTS.md`](ALERTS.md) opisuje reakcję operatora na każdy kod.

| Kod | `result` | Znaczenie | Stan po | Tryb serwisowy | Reakcja |
|---|---|---|---|---|---|
| 0 | `ok` / `noop` | wdrożone i smoke zielony (albo ta wersja już działa) | wersja N | bez zmian | brak |
| 2 | `blocked` / `failed` | preflight lub brama nie przeszły (zmiana schematu bez zgody, downgrade, drenaż, brak kopii, niezgodny digest lub SHA) | wersja N-1, **nic nie zmieniono** | bez zmian | popraw przyczynę i uruchom ponownie |
| 10 | `rollback` | smoke N nie przeszedł, rollback do N-1 udany | wersja N-1 | bez zmian | analiza bez presji czasu |
| 11 | `failed` | smoke N nie przeszedł, rollback zabroniony (zmiana schematu albo pierwsze wdrożenie) | wersja N, stan niepewny | **automatycznie `on`**, gdy czerwony jest lokalny smoke (`maintenance=on`); **bez zmian** (`off`), gdy czerwony jest tylko `--public` (Caddy, DNS, TLS) | **incydent**, sekcja e |
| 12 | `failed` | rollback podjęty i nieudany | nieokreślony | bez zmian | **incydent P1**, sekcja e |

Kod 3 i 4 dotyczą wyłącznie `status` (operacja trwa, brak stanu). `maintenance=unverified` oznacza, że `.env` zmieniono, ale `/api/v1/health` nie potwierdził trybu (np. `api` nie wstaje).

## e) Co robić przy kodach 11 i 12

1. **Nie wykonuj ponownego deployu na ślepo.** Stan: `sudo -u deploy …/deploy.sh status`; dziennik: `deploy/cd/log/<id>.log`.
2. Przy kodzie 11 z czerwonym lokalnym smoke skrypt zapisał dowody do `deploy/cd/log/<id>-incident/` (`logs.txt` z `docker compose logs --no-color --timestamps api web` i `ps.txt`; katalog 0700, mogą zawierać adresy klientów, nigdy do GitHub) i włączył tryb serwisowy.
   Tryb serwisowy **nie** zwraca 503 na całym API: wstrzymuje executor zadań, harmonogram, retencję i powiadomienia, a uruchomienia zadań, zaproszenia, linki logowania i webhooki dostają 503 `maintenance`; `/ready` i odczyt raportów działają dalej, więc `/ready` nie zrobi się czerwony.
3. Zdecyduj: poprawka wprzód (nowy tag z naprawą) albo odtworzenie danych z kopii. Odtworzenie to **wariant B** w [README](README.md#przywracanie-poprzedniej-wersji) i [RUNBOOK §5](RUNBOOK.md#5-próba-odtworzenia-i-odtworzenie-w-izolacji-co-miesiąc); incydent prowadzisz według [RUNBOOK §9](RUNBOOK.md#9-incydent) (powiadomienie klienta wg DPA).
4. Powrót z trybu serwisowego tylko ręcznie, po potwierdzeniu, że aplikacja jest zdrowa: `sudo -u deploy /opt/daas-engine/deploy/cd/deploy.sh maintenance off`. Skrypt nigdy nie wyłącza go sam.
5. Kod 12 (rollback nieudany): zacznij od `docker compose ps` i `logs` w katalogu `deploy`, sprawdź, czy poprzedni obraz jest lokalnie (`docker image ls`), i przywróć stan ręcznie według README, wariant A; gdy dane są uszkodzone, wariant B.

## Kopia poza serwerem

`deploy.sh` wywołuje (jeśli istnieje i jest wykonywalny) `deploy/cd/offsite-backup.hook` z jednym argumentem: ścieżką świeżo zrobionego archiwum. Plik jest lokalny dla hosta (ignorowany przez git) i zawiera
Twoją konfigurację z RUNBOOK §4.3 (np. wysyłka `restic`, dane dostępowe w pliku 0600 poza repozytorium). Przy `allow_schema_change=1` brak haka albo jego błąd przerywa wdrożenie kodem 2.

## Staging: co dowodzi, a czego nie

`cd-staging.yml` (przy zmianach w `deploy/`, `Dockerfile`, `alembic/` i w workflowach oraz w każdym wydaniu) uruchamia na runnerze ten sam `deploy.sh`, którego używa produkcja, przeciwko pełnemu stosowi z danymi syntetycznymi.
Dowodzi: kolejności kroków, kodów 0/2/10/11, bramy schematu (symulowanej), kopii i sumy, smoke przez Caddy z lokalnym CA. **Nie dowodzi:** zachowania prawdziwego hosta (DNS, zapora dostawcy, Let's Encrypt, SMTP),
migracji na danych klienta ani rollbacku po rzeczywistej migracji (dlatego zmiana schematu jest świadomą decyzją i ma kopię poza serwerem).

## Ograniczenia, o których warto wiedzieć

- `deploy.sh` na hoście jest w wersji poprzedniego wydania w chwili wdrożenia (checkout następuje w trakcie); zmiany samego procesu wchodzą w życie od następnego wydania.
- Jedna instancja `api` i jeden VPS: wdrożenie to krótka przerwa (⚠ 20–90 s), nie blue-green. To decyzja architektoniczna, nie brak.
- Kopia zimna zatrzymuje `api` na kilka sekund przy każdym wdrożeniu i rollbacku.
- Obrazy pomocnicze (Caddy) są pobierane po tagu z `compose.yml`, nie po digeście; aktualizuje je Dependabot.
