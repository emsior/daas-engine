# URUCHOM_DAAS_PYTHON.ps1 - start DaaS Engine v0.2 NATYWNIE (bez Dockera) na Windows
# Wymaga: Python 3.11+ (winget install Python.Python.3.12) - n8n opcjonalnie przez Node (npx n8n)
# Uruchom: prawy klik -> "Uruchom w programie PowerShell", albo:
#   powershell -ExecutionPolicy Bypass -File "D:\daas-engine\URUCHOM_DAAS_PYTHON.ps1"

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host "BLAD: $m" -ForegroundColor Red; Read-Host "Enter, aby zamknac"; exit 1 }

Step "1/5 Python"
$py = $null
foreach ($cand in @("py -3.12", "py -3.11", "py -3", "python")) {
    try { $v = & cmd /c "$cand --version 2>&1"; if ($v -match "Python 3\.(1[1-9]|[2-9]\d)") { $py = $cand; break } } catch {}
}
if (-not $py) { Fail "Brak Pythona 3.11+. Zainstaluj:  winget install Python.Python.3.12  (zaznacz 'Add to PATH'), potem odpal ponownie." }
Write-Host "Python: $py ($v)"

Step "2/5 Srodowisko wirtualne .venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & cmd /c "$py -m venv .venv" }
$venvPy = ".venv\Scripts\python.exe"
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q -e ".[dev]"
if ($LASTEXITCODE -ne 0) { Fail "pip install nie przeszedl - wklej komunikat do Claude." }

Step "3/5 Konfiguracja .env"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Utworzono .env (puste klucze = tryb fixtures/MOCK dla API, pliki klienta = LIVE_DATA)" }

Step "4/5 Testy"
& $venvPy -m pytest -q
if ($LASTEXITCODE -ne 0) { Fail "Testy nie przeszly." }

Step "5/5 Start API na http://localhost:8000  (Ctrl+C zatrzymuje)"
$server = Start-Process -FilePath $venvPy -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -PassThru -WindowStyle Minimized
$healthy = $false
for ($i = 0; $i -lt 20; $i++) { try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3; $healthy = $true; break } catch { Start-Sleep 1 } }
if (-not $healthy) { Fail "API nie odpowiada. Sprawdz okno uvicorn." }
Write-Host "HEALTH: $($h.status) v$($h.version)"

# demo: upload pliku klienta -> run -> raport HTML
Write-Host "Demo: upload data\fixtures\client_export_pl.csv -> /run -> raport"
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$form = New-Object System.Net.Http.MultipartFormDataContent
$bytes = [System.IO.File]::ReadAllBytes("$PSScriptRoot\data\fixtures\client_export_pl.csv")
$fc = New-Object System.Net.Http.ByteArrayContent(,$bytes)
$fc.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("text/csv")
$form.Add($fc, "file", "client_export_pl.csv")
$form.Add((New-Object System.Net.Http.StringContent("DemoSklep")), "client_name")
$up = $client.PostAsync("http://127.0.0.1:8000/upload", $form).Result.Content.ReadAsStringAsync().Result | ConvertFrom-Json
Write-Host "UPLOAD: rows=$($up.rows) confidence=$($up.confidence)"
$run = Invoke-RestMethod -Uri "http://127.0.0.1:8000/run" -Method Post -ContentType "application/json" -Body ($up.run_payload | ConvertTo-Json -Compress)
Write-Host "RUN: $($run.run_status) $($run.data_status) records=$($run.records_processed) margin=$($run.metrics.margin_pct)%"

Start-Process "http://127.0.0.1:8000/reports/ecommerce_demo/latest?fmt=html"
Start-Process "http://127.0.0.1:8000/docs"
Write-Host ""
Write-Host "GOTOWE. Raport otwarty w przegladarce. n8n (opcjonalnie, w drugim oknie):  npx n8n   -> http://localhost:5678" -ForegroundColor Green
Write-Host "Serwer dziala w zminimalizowanym oknie (PID $($server.Id)). Zamknij je, aby zatrzymac."
Read-Host "Enter, aby zamknac to okno (serwer zostaje)"
