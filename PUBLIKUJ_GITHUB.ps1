# PUBLIKUJ_GITHUB.ps1 - publikacja D:\daas-engine jako publiczne repo GitHub (konto: emsior)
# Uruchom: prawy klik -> "Uruchom w programie PowerShell"
# Wymaga: git (winget install Git.Git). GitHub CLI opcjonalnie (winget install GitHub.cli) - wtedy repo tworzy sie samo.

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Repo = "daas-engine"
$User = "emsior"

function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host "BLAD: $m" -ForegroundColor Red; Read-Host "Enter, aby zamknac"; exit 1 }

Step "1/4 git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Brak git. Zainstaluj:  winget install Git.Git  i odpal ponownie." }
git config user.name  | Out-Null; if (-not (git config user.name))  { git config --global user.name  $User }
git config user.email | Out-Null; if (-not (git config user.email)) { git config --global user.email "mcq089@gmail.com" }
git config core.autocrlf false
if (-not (Test-Path ".git")) { Fail "Brak .git - repo powinno byc juz zainicjowane (commit 0d1836a)." }
git status --short
git log --oneline -3

Step "2/4 Sprawdzam, czy w repo nie ma slowa 'betting' (zasada z 05_OFERTA)"
$hits = git grep -n -i -E "betting|bukmach" -- . ':!PUBLIKUJ_GITHUB.ps1' 2>$null
if ($hits) { Write-Host $hits -ForegroundColor Yellow; Fail "Znaleziono zakazane slowa - popraw przed publikacja." } else { Write-Host "OK - 0 trafien" }

Step "3/4 Zdalne repo"
$hasRemote = git remote get-url origin 2>$null
if (-not $hasRemote) {
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        gh auth status 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "Logowanie do GitHub (otworzy przegladarke)..."; gh auth login --web --git-protocol https }
        gh repo create "$User/$Repo" --public --source . --remote origin --description "Source-agnostic Data-as-a-Service engine: FastAPI + DuckDB + n8n. Upload CSV/XLSX -> automated weekly HTML report."
        if ($LASTEXITCODE -ne 0) { Fail "gh repo create nie przeszlo." }
    } else {
        Write-Host "Nie ma GitHub CLI. Zrob recznie (2 min):" -ForegroundColor Yellow
        Write-Host "  1. Otworz https://github.com/new  -> Repository name: $Repo  -> Public  -> NIE zaznaczaj README/.gitignore  -> Create"
        Write-Host "  2. Wroc tu i nacisnij Enter."
        Start-Process "https://github.com/new"
        Read-Host "Enter, gdy repo utworzone"
        git remote add origin "https://github.com/$User/$Repo.git"
    }
}
git remote -v

Step "4/4 Push"
git branch -M main
git push -u origin main
if ($LASTEXITCODE -ne 0) { Fail "Push nie przeszedl. Najczesciej: brak logowania - Windows otworzy okno 'Git Credential Manager', zaloguj sie przez przegladarke i odpal skrypt ponownie." }

Write-Host ""
Write-Host "OPUBLIKOWANE: https://github.com/$User/$Repo" -ForegroundColor Green
Write-Host "CI: https://github.com/$User/$Repo/actions  (job 'test' + 'docker' - docker buduje sie na runnerze GitHub, nie u Ciebie)"
Start-Process "https://github.com/$User/$Repo/actions"
Read-Host "Enter, aby zamknac"
