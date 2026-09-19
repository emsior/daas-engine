<#
===================================================================================================
 uksc-collector.ps1  -  READ-ONLY collector of technical evidence for UKSC art. 8 (Windows)
 Part of DaaS Engine (github.com/emsior/daas-engine)  -  MIT License  -  Proch PC (prochpc.pl)
===================================================================================================
 CONTRACT
   - READ-ONLY. No Set-*, Remove-*, Stop-*, registry writes, service changes. Nothing is modified.
   - No network. No telemetry. Nothing leaves this machine until YOU upload the JSON.
   - No dependencies. Windows PowerShell 5.1+ / PowerShell 7+. Windows 10/11, Server 2016+.
   - Output: <Out>\uksc_<host>_<stamp>.json  (+ .sha256 sidecar with the file hash)
   - Without administrator rights the script still runs; admin-only checks are reported as
     NA_NO_ADMIN (never guessed as PASS/FAIL).
   - ASCII only in this file (encoding safety across PS versions).

 USAGE
   powershell -ExecutionPolicy Bypass -File .\uksc-collector.ps1 [-Out C:\uksc] [-Raw]
   Run "as administrator" for full coverage.

 SCHEMA: schemas/uksc_collector_v1.json  (schema_version 1.0). Control catalog: tools/uksc_fixtures.py
===================================================================================================
#>
[CmdletBinding()]
param(
    [string]$Out = (Join-Path $env:SystemDrive 'uksc'),
    [switch]$Raw
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
Set-StrictMode -Off

$SCHEMA_VERSION    = '1.0'
$COLLECTOR_VERSION = '0.1.0'
$PZU = 'OWU PZU Cyber UZ/162/2023'
$nowUtc = [DateTime]::UtcNow
$stamp  = $nowUtc.ToString('yyyyMMdd_HHmmss')
$isoNow = $nowUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$IsAdmin = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$null = New-Item -ItemType Directory -Force -Path $Out -ErrorAction SilentlyContinue
$rawDir = Join-Path $Out 'raw'
if ($Raw) { $null = New-Item -ItemType Directory -Force -Path $rawDir -ErrorAction SilentlyContinue }

$script:Checks = New-Object System.Collections.Generic.List[object]

# ---------------------------------------------------------------- helpers
function Get-Sha256Hex([string]$text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))) -replace '-', '').ToLower() }
    finally { $sha.Dispose() }
}

function ConvertTo-JsonStringLiteral([string]$s) {
    # byte-identical to Python json.dumps(str, ensure_ascii=True)
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    foreach ($ch in $s.ToCharArray()) {
        $c = [int]$ch
        switch ($c) {
            34  { [void]$sb.Append('\"'); continue }
            92  { [void]$sb.Append('\\'); continue }
            8   { [void]$sb.Append('\b'); continue }
            12  { [void]$sb.Append('\f'); continue }
            10  { [void]$sb.Append('\n'); continue }
            13  { [void]$sb.Append('\r'); continue }
            9   { [void]$sb.Append('\t'); continue }
        }
        if ($c -lt 32 -or $c -gt 127) { [void]$sb.Append(('\u{0:x4}' -f $c)) } else { [void]$sb.Append($ch) }
    }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function ConvertTo-CanonicalJson($obj) {
    # sorted keys (ordinal), no whitespace, ASCII-only -> identical bytes to
    # Python json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True)
    if ($null -eq $obj) { return 'null' }
    if ($obj -is [bool]) { return $(if ($obj) { 'true' } else { 'false' }) }
    if ($obj -is [string]) { return (ConvertTo-JsonStringLiteral $obj) }
    if ($obj -is [System.ValueType]) { return ([string]$obj).Replace(',', '.') }
    if ($obj -is [System.Collections.IDictionary]) {
        $keys = New-Object 'System.Collections.Generic.List[string]'
        foreach ($k in $obj.Keys) { $keys.Add([string]$k) }
        $keys.Sort([System.StringComparer]::Ordinal)
        $parts = foreach ($k in $keys) { (ConvertTo-JsonStringLiteral $k) + ':' + (ConvertTo-CanonicalJson $obj[$k]) }
        return '{' + ($parts -join ',') + '}'
    }
    if ($obj -is [System.Collections.IEnumerable]) {
        $parts = foreach ($x in $obj) { ConvertTo-CanonicalJson $x }
        return '[' + ($parts -join ',') + ']'
    }
    return (ConvertTo-JsonStringLiteral ([string]$obj))
}

function Add-Check {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$Ref,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][ValidateSet('PASS','FAIL','WARN','MANUAL','NA_NO_ADMIN','ERROR')][string]$Status,
        [hashtable]$Evidence = @{},
        [string]$Threshold = $null
    )
    $ev = [ordered]@{}
    foreach ($k in ($Evidence.Keys | Sort-Object)) { $ev[$k] = $Evidence[$k] }
    $script:Checks.Add([ordered]@{
        control_id            = $Id
        uksc_ref              = $Ref
        title                 = $Title
        status                = $Status
        evidence              = $ev
        evidence_source       = 'powershell'
        evidence_collected_at = $isoNow
        evidence_hash         = (Get-Sha256Hex (ConvertTo-CanonicalJson $Evidence))
        reference_threshold   = $Threshold
    })
}

function Invoke-Check {
    # Runs a check body; any exception -> ERROR (never a guessed status). $AdminOnly -> NA_NO_ADMIN when not elevated.
    param([string]$Id, [string]$Ref, [string]$Title, [bool]$AdminOnly, [string]$Threshold, [scriptblock]$Body)
    if ($AdminOnly -and -not $IsAdmin) {
        Add-Check -Id $Id -Ref $Ref -Title $Title -Status 'NA_NO_ADMIN' -Evidence @{ reason = 'collector run without administrator rights' } -Threshold $Threshold
        return
    }
    try {
        $r = & $Body
        Add-Check -Id $Id -Ref $Ref -Title $Title -Status $r.Status -Evidence $r.Evidence -Threshold $Threshold
    } catch {
        Add-Check -Id $Id -Ref $Ref -Title $Title -Status 'ERROR' -Evidence @{ error = ($_.Exception.Message -replace '[\r\n]+', ' ') } -Threshold $Threshold
    }
}

function Save-Raw([string]$name, [scriptblock]$body) {
    if (-not $Raw) { return }
    try { (& $body | Out-String -Width 4096) | Set-Content -Path (Join-Path $rawDir $name) -Encoding UTF8 } catch {}
}

function Get-RegValue([string]$path, [string]$name) {
    try { return (Get-ItemProperty -Path $path -Name $name -ErrorAction Stop).$name } catch { return $null }
}

# ---------------------------------------------------------------- host
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$host_info = [ordered]@{
    name          = $env:COMPUTERNAME
    os            = [string]$os.Caption
    build         = [string]$os.BuildNumber
    domain_joined = [bool]$cs.PartOfDomain
    is_admin_run  = [bool]$IsAdmin
}

Write-Host "uksc-collector $COLLECTOR_VERSION  host=$($env:COMPUTERNAME)  admin=$IsAdmin  out=$Out"

# ================================================================ ENC / INT  (art. 8 ust. 1 pkt 2 lit. k ; pkt 5 lit. c)
Invoke-Check 'ENC-01' 'art. 8 ust. 1 pkt 2 lit. k' 'Szyfrowanie woluminu systemowego (BitLocker)' $true $null {
    $v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
    $on = ($v.ProtectionStatus -eq 'On')
    @{ Status = $(if ($on) { 'PASS' } else { 'FAIL' }); Evidence = @{ volume = $env:SystemDrive; protection = [string]$v.ProtectionStatus; method = [string]$v.EncryptionMethod; pct = [int]$v.EncryptionPercentage } }
}
Invoke-Check 'INT-01' 'art. 8 ust. 1 pkt 5 lit. c' 'Secure Boot wlaczony' $true $null {
    $sb = Confirm-SecureBootUEFI -ErrorAction Stop
    @{ Status = $(if ($sb) { 'PASS' } else { 'FAIL' }); Evidence = @{ secure_boot = [bool]$sb } }
}
Invoke-Check 'INT-02' 'art. 8 ust. 1 pkt 5 lit. c' 'TPM obecny i gotowy' $true $null {
    $t = Get-Tpm -ErrorAction Stop
    $ver = $null; try { $ver = [string](Get-CimInstance -Namespace root/cimv2/Security/MicrosoftTpm -ClassName Win32_Tpm -ErrorAction Stop).SpecVersion.Split(',')[0] } catch {}
    @{ Status = $(if ($t.TpmPresent -and $t.TpmReady) { 'PASS' } else { 'FAIL' }); Evidence = @{ present = [bool]$t.TpmPresent; ready = [bool]$t.TpmReady; version = $ver } }
}
Invoke-Check 'INT-03' 'art. 8 ust. 1 pkt 5 lit. c' 'Defender Tamper Protection' $true $null {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    @{ Status = $(if ($mp.IsTamperProtected) { 'PASS' } else { 'FAIL' }); Evidence = @{ tamper_protected = [bool]$mp.IsTamperProtected } }
}
Invoke-Check 'INT-04' 'art. 8 ust. 1 pkt 5 lit. c' 'BCD: testsigning / nointegritychecks wylaczone' $true $null {
    $bcd = (& bcdedit /enum 2>$null | Out-String)
    $ts = [bool]($bcd -match '(?i)testsigning\s+Yes'); $ni = [bool]($bcd -match '(?i)nointegritychecks\s+Yes')
    @{ Status = $(if ($ts -or $ni) { 'FAIL' } else { 'PASS' }); Evidence = @{ testsigning = $ts; nointegritychecks = $ni } }
}
Invoke-Check 'INT-05' 'art. 8 ust. 1 pkt 5 lit. c' 'Sterowniki bez podpisu' $true $null {
    $txt = (& pnputil /enum-drivers 2>$null | Out-String)
    $blocks = $txt -split '(?m)^\s*$' | Where-Object { $_ -match '(?i)Published Name|Opublikowana nazwa' }
    $unsigned = @($blocks | Where-Object { $_ -match '(?i)(Signer Name|Nazwa osoby podpisuj)[^\r\n]*:\s*$' })
    $sample = @($unsigned | ForEach-Object { if ($_ -match '(?i)(Published Name|Opublikowana nazwa)\s*:\s*(\S+)') { $Matches[2] } } | Select-Object -First 5)
    @{ Status = $(if ($unsigned.Count -eq 0) { 'PASS' } else { 'WARN' }); Evidence = @{ unsigned_count = [int]$unsigned.Count; sample = $sample } }
}

# ================================================================ ACC  (art. 8 ust. 1 pkt 2 lit. n)
Invoke-Check 'ACC-01' 'art. 8 ust. 1 pkt 2 lit. n' 'Konta w grupie Administratorzy' $true 'referencja: <= 2 konta imienne + wbudowane' {
    $m = @()
    try { $m = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop) } catch { $m = @(Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop) }
    $names = @($m | ForEach-Object { [string]$_.Name })
    $st = if ($names.Count -le 3) { 'PASS' } elseif ($names.Count -le 5) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ count = [int]$names.Count; members = $names } }
}
Invoke-Check 'ACC-02' 'art. 8 ust. 1 pkt 2 lit. n' 'Konto Gosc wylaczone' $false $null {
    $g = Get-LocalUser | Where-Object { $_.SID.Value -like 'S-1-5-21-*-501' } | Select-Object -First 1
    $en = if ($g) { [bool]$g.Enabled } else { $false }
    @{ Status = $(if ($en) { 'FAIL' } else { 'PASS' }); Evidence = @{ guest_enabled = $en } }
}
Invoke-Check 'ACC-03' 'art. 8 ust. 1 pkt 2 lit. n' 'Konta wlaczone bez wymaganego hasla' $false $null {
    $bad = @(Get-LocalUser | Where-Object { $_.Enabled -and -not $_.PasswordRequired -and $_.Name -notmatch '^(WDAGUtilityAccount|DefaultAccount)$' } | ForEach-Object { $_.Name })
    @{ Status = $(if ($bad.Count -eq 0) { 'PASS' } else { 'FAIL' }); Evidence = @{ count = [int]$bad.Count; accounts = $bad } }
}
Invoke-Check 'ACC-04' 'art. 8 ust. 1 pkt 2 lit. n' 'Polityka hasel (dlugosc, wiek, blokada)' $true 'referencja: min. 12 znakow, blokada po <= 10 probach' {
    $txt = (& net accounts 2>$null | Out-String)
    $minLen = 0; if ($txt -match '(?im)^(Minimum password length|Minimalna d[^:]*):\s*(\d+)') { $minLen = [int]$Matches[2] }
    $maxAge = $null; if ($txt -match '(?im)^(Maximum password age[^:]*|Maksymalny okres[^:]*):\s*(\d+|Unlimited|Nieograniczony)') { $maxAge = $Matches[2] }
    $lock   = $null; if ($txt -match '(?im)^(Lockout threshold|Pr.g blokady[^:]*):\s*(\d+|Never|Nigdy)') { $lock = $Matches[2] }
    $hist   = $null; if ($txt -match '(?im)^(Length of password history[^:]*|D[^:]*historii[^:]*):\s*(\d+|None|Brak)') { $hist = $Matches[2] }
    $lockN = 0; if ($lock -match '^\d+$') { $lockN = [int]$lock }
    $st = if (($minLen -ge 12) -and ($lockN -ge 1 -and $lockN -le 10)) { 'PASS' } elseif ($minLen -ge 8) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ min_length = $minLen; max_age_days = [string]$maxAge; lockout_threshold = [string]$lock; history = [string]$hist } }
}
Invoke-Check 'ACC-05' 'art. 8 ust. 1 pkt 2 lit. n' 'UAC wlaczony z monitem' $false $null {
    $p = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    $lua = Get-RegValue $p 'EnableLUA'; $cpa = Get-RegValue $p 'ConsentPromptBehaviorAdmin'; $sd = Get-RegValue $p 'PromptOnSecureDesktop'
    $st = if ($lua -eq 1 -and $cpa -ne 0) { 'PASS' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ enable_lua = [int]$lua; consent_prompt_admin = [int]$cpa; secure_desktop = [int]$sd } }
}

# ================================================================ MON  (art. 8 ust. 1 pkt 2 lit. g)
Invoke-Check 'MON-01' 'art. 8 ust. 1 pkt 2 lit. g' 'Dziennik Security wlaczony, rozmiar i retencja' $true 'referencja: >= 64 MB, bez nadpisywania < 30 dni' {
    $lg = Get-WinEvent -ListLog Security -ErrorAction Stop
    $oldest = $null
    try { $e = Get-WinEvent -LogName Security -Oldest -MaxEvents 1 -ErrorAction Stop; if ($e) { $oldest = [int]((Get-Date) - $e.TimeCreated).TotalDays } } catch {}
    $mb = [int]($lg.MaximumSizeInBytes / 1MB)
    $st = if ($lg.IsEnabled -and $mb -ge 64 -and ($null -eq $oldest -or $oldest -ge 30)) { 'PASS' } elseif ($lg.IsEnabled) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ enabled = [bool]$lg.IsEnabled; max_size_mb = $mb; oldest_event_days = $oldest } }
}
Invoke-Check 'MON-02' 'art. 8 ust. 1 pkt 2 lit. g' 'Polityka audytu: logowanie i zarzadzanie kontami' $true $null {
    $txt = (& auditpol /get /category:* 2>$null | Out-String)
    $get = { param($rx) if ($txt -match $rx) { $Matches[1].Trim() } else { 'unknown' } }
    $logon = & $get '(?im)^\s*(?:Logon|Logowanie)\s{2,}(.+)$'
    $acct  = & $get '(?im)^\s*(?:User Account Management|Zarz[^\r\n]*kontami u[^\r\n]*)\s{2,}(.+)$'
    $ok = ($logon -match '(?i)success|sukces') -and ($acct -match '(?i)success|sukces')
    @{ Status = $(if ($ok) { 'PASS' } else { 'FAIL' }); Evidence = @{ logon = $logon; account_management = $acct } }
}

# ================================================================ UPD  (art. 8 ust. 1 pkt 5 lit. b ; zal. 4 pkt 16)
Invoke-Check 'UPD-01' 'art. 8 ust. 1 pkt 5 lit. b' 'Dni od ostatniej poprawki systemu' $false "$PZU`: krytyczne poprawki <= 30 dni" {
    $hf = Get-HotFix -ErrorAction Stop | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending | Select-Object -First 1
    if (-not $hf) { throw 'no hotfix with InstalledOn' }
    $days = [int]((Get-Date) - $hf.InstalledOn).TotalDays
    $st = if ($days -le 30) { 'PASS' } elseif ($days -le 60) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ last_hotfix = $hf.InstalledOn.ToString('yyyy-MM-dd'); days_since = $days; last_hotfix_id = [string]$hf.HotFixID } }
}
Invoke-Check 'UPD-02' 'art. 8 ust. 1 pkt 5 lit. b' 'Brak oczekujacego restartu po aktualizacji' $false $null {
    $p1 = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    $p2 = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
    $pend = ($p1 -or $p2)
    @{ Status = $(if ($pend) { 'WARN' } else { 'PASS' }); Evidence = @{ reboot_pending = $pend } }
}
Invoke-Check 'UPD-03' 'zal. 4 pkt 16' 'Wersja systemu wspierana przez producenta' $false $null {
    # Minimal EOL table (server-side table in DaaS is the source of truth; this is a first-line signal only)
    $b = [int]$os.BuildNumber
    $eol = $null
    if ($os.Caption -match 'Windows 10' -and $b -le 19045) { $eol = '2025-10-14' }
    if ($os.Caption -match 'Windows 7|Windows 8|Server 2008|Server 2012') { $eol = 'expired' }
    @{ Status = $(if ($eol) { 'FAIL' } else { 'PASS' }); Evidence = @{ os = [string]$os.Caption; build = [string]$b; supported = ($null -eq $eol); eol = $eol } }
}

# ================================================================ AV  (zal. 4 pkt 13)
Invoke-Check 'AV-01' 'zal. 4 pkt 13' 'Ochrona antywirusowa aktywna (Defender lub inny)' $false "$PZU`: aktywny antywirus na wszystkich punktach koncowych" {
    $third = @()
    try { $third = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object { [string]$_.displayName } | Where-Object { $_ -notmatch 'Defender' }) } catch {}
    $rt = $null; $prod = 'Microsoft Defender'
    try { $rt = [bool](Get-MpComputerStatus -ErrorAction Stop).RealTimeProtectionEnabled } catch {}
    $ok = ($rt -eq $true) -or ($third.Count -gt 0)
    @{ Status = $(if ($ok) { 'PASS' } else { 'FAIL' }); Evidence = @{ product = $prod; realtime = $rt; third_party = $third } }
}
Invoke-Check 'AV-02' 'zal. 4 pkt 13' 'Wiek sygnatur antywirusa' $false 'referencja: <= 7 dni' {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    $age = [int]$mp.AntivirusSignatureAge; $scan = $null; if ($mp.FullScanEndTime) { $scan = [int]((Get-Date) - $mp.FullScanEndTime).TotalDays }
    $st = if ($age -le 7) { 'PASS' } elseif ($age -le 30) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ signature_age_days = $age; last_full_scan_days = $scan } }
}

# ================================================================ BKP / MFA  (pkt 2 lit. f, zal. 4 pkt 10-11 ; pkt 2 lit. l)
Invoke-Check 'BKP-01' 'art. 8 ust. 1 pkt 2 lit. f; zal. 4 pkt 10' 'Agent kopii zapasowej obecny na stacji' $false "$PZU`: kopia do izolowanego srodowiska co <= 7 dni" {
    $known = 'VeeamEndpointBackupSvc|VeeamDeploySvc|AcronisAgent|mms|ActiveBackup|Synology|SBE|wbengine|Backblaze|Carbonite|MSP360|CBBackup|Datto|StorageCraft|ShadowProtect|NAKIVO|Duplicati|Macrium|ReflectService'
    $agents = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $known -or $_.DisplayName -match $known } | ForEach-Object { [string]$_.Name } | Sort-Object -Unique)
    @{ Status = $(if ($agents.Count -gt 0) { 'PASS' } else { 'FAIL' }); Evidence = @{ agents = $agents; last_backup_hint = $null } }
}
Add-Check -Id 'BKP-02' -Ref 'zal. 4 pkt 11' -Title 'Test odtworzenia kopii zapasowej' -Status 'MANUAL' -Threshold "$PZU`: test odtworzenia co <= 365 dni" `
    -Evidence @{ attestation_hint = 'protokol z testu odtworzenia (data, zakres, wynik) - pole last_test_date' }
$hfb = Get-RegValue 'HKLM:\SOFTWARE\Policies\Microsoft\PassportForWork' 'Enabled'
Add-Check -Id 'MFA-01' -Ref 'art. 8 ust. 1 pkt 2 lit. l' -Title 'MFA dla dostepu zdalnego i kont uprzywilejowanych' -Status 'MANUAL' -Threshold "$PZU`: MFA dla zdalnego dostepu" `
    -Evidence @{ attestation_hint = 'raport rejestracji MFA z Entra ID / VPN (v1.1: automatycznie przez Graph API)'; hello_for_business_policy = ($hfb -eq 1) }

# ================================================================ FW / RDP  (art. 8 ust. 1 pkt 5 lit. a)
Invoke-Check 'FW-01' 'art. 8 ust. 1 pkt 5 lit. a' 'Zapora Windows wlaczona na wszystkich profilach' $false "$PZU`: aktywna zapora sieciowa" {
    $prof = [ordered]@{}
    foreach ($p in (Get-NetFirewallProfile -ErrorAction Stop)) { $prof[[string]$p.Name] = [bool]$p.Enabled }
    $all = ($prof.Values | Where-Object { -not $_ }).Count -eq 0
    $rdpRules = @(Get-NetFirewallRule -DisplayGroup 'Remote Desktop*' -Enabled True -Direction Inbound -ErrorAction SilentlyContinue)
    $rdpProf = @($rdpRules | ForEach-Object { [string]$_.Profile } | Sort-Object -Unique)
    @{ Status = $(if ($all) { 'PASS' } else { 'FAIL' }); Evidence = @{ profiles = $prof; enabled_all_profiles = $all; rdp_rule_present = ($rdpRules.Count -gt 0); rdp_rule_profiles = $rdpProf } }
}
Invoke-Check 'RDP-01' 'art. 8 ust. 1 pkt 5 lit. a' 'RDP wylaczony albo chroniony (NLA + zapora)' $false "$PZU`: RDP wylaczony, chyba ze chroniony MFA" {
    $deny = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' 'fDenyTSConnections'
    $nla  = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' 'UserAuthentication'
    $listen = $false; try { $listen = @(Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction Stop).Count -gt 0 } catch {}
    $enabled = ($deny -eq 0) -or $listen
    $st = if (-not $enabled) { 'PASS' } elseif ($nla -eq 1) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ rdp_enabled = $enabled; listening_3389 = $listen; nla_required = ($nla -eq 1) } }
}

# ================================================================ HYG  (art. 8 ust. 1 pkt 2 lit. j)
Invoke-Check 'HYG-01' 'art. 8 ust. 1 pkt 2 lit. j' 'Blokada sesji po bezczynnosci' $false 'referencja: <= 15 min' {
    $t = Get-RegValue 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' 'InactivityTimeoutSecs'
    $ssTimeout = Get-RegValue 'HKCU:\Control Panel\Desktop' 'ScreenSaveTimeOut'; $ssSecure = Get-RegValue 'HKCU:\Control Panel\Desktop' 'ScreenSaverIsSecure'
    $eff = 0; if ($t) { $eff = [int]$t } elseif ($ssSecure -eq 1 -and $ssTimeout) { $eff = [int]$ssTimeout }
    $st = if ($eff -gt 0 -and $eff -le 900) { 'PASS' } elseif ($eff -gt 0) { 'WARN' } else { 'FAIL' }
    @{ Status = $st; Evidence = @{ inactivity_timeout_sec = $eff; screensaver_secure = ($ssSecure -eq 1) } }
}
Invoke-Check 'HYG-02' 'art. 8 ust. 1 pkt 2 lit. j' 'SMBv1 wylaczony' $true $null {
    $c = Get-SmbServerConfiguration -ErrorAction Stop
    @{ Status = $(if ($c.EnableSMB1Protocol) { 'FAIL' } else { 'PASS' }); Evidence = @{ smb1_enabled = [bool]$c.EnableSMB1Protocol } }
}
Invoke-Check 'HYG-03' 'art. 8 ust. 1 pkt 2 lit. j' 'Wykluczenia Defendera obejmujace cale drzewa' $true $null {
    $pref = Get-MpPreference -ErrorAction Stop
    $ex = @(@($pref.ExclusionPath) + @($pref.ExclusionProcess) + @($pref.ExclusionExtension) | Where-Object { $_ })
    $tree = @($ex | Where-Object { $_ -match '(?i)^[A-Z]:\\?$' -or $_ -match '(?i)\\Users\\?$' -or $_ -match '(?i)\\(Temp|AppData|Downloads|Program Files)\\?$' })
    @{ Status = $(if ($tree.Count -eq 0) { 'PASS' } else { 'FAIL' }); Evidence = @{ exclusions_total = [int]$ex.Count; tree_exclusions = $tree } }
}
Invoke-Check 'HYG-04' 'art. 8 ust. 1 pkt 2 lit. j' 'Wpisy autostartu / zadania / uslugi bez podpisu' $true $null {
    function Test-Signed([string]$cmd) {
        if (-not $cmd) { return $true }
        $exe = $null
        if ($cmd -match '^"([^"]+)"') { $exe = $Matches[1] } elseif ($cmd -match '^(\S+\.exe)') { $exe = $Matches[1] }
        if (-not $exe) { return $true }
        $exe = [Environment]::ExpandEnvironmentVariables($exe)
        if (-not (Test-Path $exe)) { return $true }
        try { return ((Get-AuthenticodeSignature -FilePath $exe -ErrorAction Stop).Status -eq 'Valid') } catch { return $true }
    }
    $runKeys = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run')
    $ua = 0
    foreach ($k in $runKeys) { if (Test-Path $k) { $props = Get-ItemProperty $k; foreach ($n in ($props.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' })) { if (-not (Test-Signed ([string]$n.Value))) { $ua++ } } } }
    $ut = 0
    foreach ($t in (Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.State -ne 'Disabled' -and $_.TaskPath -notmatch '^\\Microsoft\\' })) {
        foreach ($a in $t.Actions) { if ($a.Execute -and -not (Test-Signed ([string]$a.Execute))) { $ut++ } }
    }
    $us = 0
    foreach ($s in (Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object { $_.PathName -and $_.PathName -notmatch '(?i)\\Windows\\' })) { if (-not (Test-Signed ([string]$s.PathName))) { $us++ } }
    $tot = $ua + $ut + $us
    @{ Status = $(if ($tot -eq 0) { 'PASS' } elseif ($tot -le 3) { 'WARN' } else { 'FAIL' }); Evidence = @{ unsigned_autoruns = $ua; unsigned_tasks = $ut; unsigned_services = $us } }
}

# ================================================================ AST  (art. 8 ust. 1 pkt 2 lit. m ; zal. 4 pkt 1)
$software = @()
foreach ($k in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
    try {
        $software += Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -and -not $_.SystemComponent } |
            ForEach-Object { [ordered]@{ name = [string]$_.DisplayName; version = [string]$_.DisplayVersion; publisher = [string]$_.Publisher } }
    } catch {}
}
$software = @($software | Sort-Object { $_.name } -Unique)
$hw = [ordered]@{}
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"
    $bios = Get-CimInstance Win32_BIOS
    $hw = [ordered]@{ cpu = [string]$cpu.Name; ram_gb = [int]([math]::Round($cs.TotalPhysicalMemory / 1GB)); disk_gb = [int]([math]::Round($disk.Size / 1GB)); serial = [string]$bios.SerialNumber }
} catch {}
Add-Check -Id 'AST-01' -Ref 'art. 8 ust. 1 pkt 2 lit. m; zal. 4 pkt 1' -Title 'Inwentarz stacji i oprogramowania zebrany' -Status 'PASS' `
    -Evidence @{ software_count = [int]$software.Count; hardware = ($hw.Count -gt 0) }

# ================================================================ GOV  (manual attestations - governance is outside the workstation)
Add-Check -Id 'GOV-01' -Ref 'art. 8 ust. 1 pkt 2 lit. a' -Title 'Polityka bezpieczenstwa / dokumentacja SZBI' -Status 'MANUAL' -Evidence @{ attestation_hint = 'dokument polityki z data zatwierdzenia i wlascicielem' }
Add-Check -Id 'GOV-02' -Ref 'art. 8 ust. 1 pkt 4' -Title 'Procedura zarzadzania incydentami (24h/72h/1 mies.)' -Status 'MANUAL' -Evidence @{ attestation_hint = 'procedura z kontaktem do CSIRT sektorowego' }
Add-Check -Id 'GOV-03' -Ref 'art. 8 ust. 1 pkt 2 lit. i' -Title 'Szkolenia z cyberbezpieczenstwa (w tym kierownik, raz w roku)' -Status 'MANUAL' -Evidence @{ attestation_hint = 'lista szkolen z datami i podpisami' }
Add-Check -Id 'GOV-04' -Ref 'art. 8 ust. 1 pkt 2 lit. e' -Title 'Rejestr dostawcow ICT i klauzule bezpieczenstwa' -Status 'MANUAL' -Evidence @{ attestation_hint = 'wykaz dostawcow + umowa z MSP (art. 14)' }

# ================================================================ raw dumps (optional, -Raw)
Save-Raw '01_system.txt' { $os | Format-List Caption, Version, BuildNumber, InstallDate; Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 20 }
Save-Raw '02_defender.txt' { try { Get-MpComputerStatus | Format-List } catch { 'n/a' } }
Save-Raw '03_accounts.txt'  { Get-LocalUser | Format-Table Name, Enabled, PasswordRequired, LastLogon -Auto }

# ================================================================ package
$hashes = @($script:Checks | ForEach-Object { $_.evidence_hash } | Sort-Object)
$pkgHash = Get-Sha256Hex ($hashes -join '')
$package = [ordered]@{
    schema_version    = $SCHEMA_VERSION
    collector_version = $COLLECTOR_VERSION
    collected_at_utc  = $isoNow
    host              = $host_info
    checks            = @($script:Checks)
    inventory         = [ordered]@{ software = @($software); hardware = $hw }
    package_sha256    = $pkgHash
}
$file = Join-Path $Out ("uksc_{0}_{1}.json" -f $env:COMPUTERNAME, $stamp)
$json = $package | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($file, $json, (New-Object Text.UTF8Encoding($false)))
$fileHash = (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$file.sha256", "$fileHash  $(Split-Path $file -Leaf)`n", (New-Object Text.UTF8Encoding($false)))

$summary = $script:Checks | Group-Object status | ForEach-Object { "{0}={1}" -f $_.Name, $_.Count }
Write-Host ("done: {0} checks [{1}]" -f $script:Checks.Count, ($summary -join ' '))
Write-Host "package: $file"
Write-Host "sha256 : $fileHash"
if (-not $IsAdmin) { Write-Host 'NOTE: run as administrator for full coverage (NA_NO_ADMIN checks).' -ForegroundColor Yellow }
