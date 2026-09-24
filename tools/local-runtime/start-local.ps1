param([switch]$NoBuild)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$RuntimeRoot = Join-Path $ProjectRoot ".local-runtime"
$BackendRoot = Join-Path $ProjectRoot "backend"
$WebRoot = Join-Path $ProjectRoot "web"
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

function Test-RecordedProcess([string]$PidFile) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return $false }
    $recordedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
    return $null -ne (Get-Process -Id $recordedPid -ErrorAction SilentlyContinue)
}
function Rotate-Log([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 5MB) {
        $archive = "$Path.1"
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
        Move-Item -LiteralPath $Path -Destination $archive
    }
}
function Import-DotEnv([string]$Path) {
    $previous = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $previous }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        $name = $Matches[1]; $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
    return $previous
}
function Get-DotEnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        if ($Matches[1] -ne $Key) { continue }
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }
    return ""
}
function Restore-Environment([hashtable]$Previous) {
    foreach ($name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Previous[$name], "Process")
    }
}
function Get-LanAddress {
    $configuration = Get-NetIPConfiguration -ErrorAction Stop |
        Where-Object { $_.NetAdapter.Status -eq "Up" -and $null -ne $_.IPv4DefaultGateway -and $null -ne $_.IPv4Address } |
        Sort-Object { $_.NetAdapter.InterfaceMetric } |
        Select-Object -First 1
    if ($null -eq $configuration) { throw "No active LAN adapter with an IPv4 default gateway was found" }
    return $configuration.IPv4Address.IPAddress
}
function Set-TemporaryEnvironment([hashtable]$Values) {
    $previous = @{}
    foreach ($name in $Values.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $Values[$name], "Process")
    }
    return $previous
}

$Python = (Get-Command python.exe -ErrorAction Stop).Source
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$Node = (Get-Command node.exe -ErrorAction Stop).Source
& $Python -c "import reportlab" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install "reportlab>=4.2,<5"
    if ($LASTEXITCODE -ne 0) { throw "Unable to install ReportLab required for PDF downloads" }
}
$BackendPid = Join-Path $RuntimeRoot "backend.pid"
$FrontendPid = Join-Path $RuntimeRoot "frontend.pid"
$BackendScannerToken = Get-DotEnvValue (Join-Path $BackendRoot ".env") "ADAPTIVE_ELECTRONIC_SCANNER_TOKEN"
if ($BackendScannerToken.Length -lt 32) {
    throw "backend/.env must contain an ADAPTIVE_ELECTRONIC_SCANNER_TOKEN of at least 32 characters"
}
$LanAddress = Get-LanAddress
$LanBaseUrl = "http://${LanAddress}:3000"
$LanAddressFile = Join-Path $RuntimeRoot "lan-address.txt"
Set-Content -LiteralPath $LanAddressFile -Value $LanBaseUrl -Encoding ascii
$Desktop = [Environment]::GetFolderPath("Desktop")
if ($Desktop) {
    $shortcutContent = @(
        "[InternetShortcut]", "URL=$LanBaseUrl/?symbol=2408&view=prepost-analysis",
        "IconFile=C:\Windows\System32\SHELL32.dll", "IconIndex=13"
    )
    Set-Content -LiteralPath (Join-Path $Desktop "TWSE-Local.url") -Value $shortcutContent -Encoding ascii
    Get-ChildItem -LiteralPath $Desktop -Filter "TWSE*.url" -File | ForEach-Object {
        Set-Content -LiteralPath $_.FullName -Value $shortcutContent -Encoding ascii
    }
}
if (-not (Test-RecordedProcess $BackendPid)) {
    $out = Join-Path $RuntimeRoot "backend.out.log"; $err = Join-Path $RuntimeRoot "backend.err.log"
    Rotate-Log $out; Rotate-Log $err
    $backendEnvironment = Set-TemporaryEnvironment @{
        ADAPTIVE_ELECTRONIC_SCANNER_URL = "$LanBaseUrl/api/adaptive-electronic/scan"
        ADAPTIVE_ELECTRONIC_SCANNER_TIMEOUT_SECONDS = "300"
        AI_STOCK_SCANNER_URL = "$LanBaseUrl/api/ai"
        PATTERN_ROBOT_SCANNER_URL = "$LanBaseUrl/api/pattern-robot/scanner"
        PUBLIC_WEB_URL = $LanBaseUrl
        ROCKET_RADAR_SCANNER_URL = "$LanBaseUrl/api/rocket-radar/scan"
    }
    try {
        $process = Start-Process -FilePath $Python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $BackendRoot -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    } finally {
        Restore-Environment $backendEnvironment
    }
    Set-Content -LiteralPath $BackendPid -Value $process.Id -Encoding ascii
}
$backendReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try { if ((Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $backendReady = $true; break } } catch { Start-Sleep -Seconds 1 }
}
if (-not $backendReady) { throw "Local backend did not become healthy. Check $RuntimeRoot\backend.err.log" }

if (-not $NoBuild -and -not (Test-Path -LiteralPath (Join-Path $WebRoot ".next/BUILD_ID"))) {
    & $Npm run build --prefix $WebRoot
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed" }
}
if (-not (Test-RecordedProcess $FrontendPid)) {
    $standaloneRoot = Join-Path $WebRoot ".next/standalone"
    $standaloneServer = Join-Path $standaloneRoot "server.js"
    if (-not (Test-Path -LiteralPath $standaloneServer)) {
        throw "Frontend standalone build is missing. Run npm run build in $WebRoot"
    }
    $standaloneNext = Join-Path $standaloneRoot ".next"
    New-Item -ItemType Directory -Path $standaloneNext -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $WebRoot ".next/static") -Destination $standaloneNext -Recurse -Force
    $publicRoot = Join-Path $WebRoot "public"
    if (Test-Path -LiteralPath $publicRoot) {
        Copy-Item -LiteralPath $publicRoot -Destination $standaloneRoot -Recurse -Force
    }
    $out = Join-Path $RuntimeRoot "frontend.out.log"; $err = Join-Path $RuntimeRoot "frontend.err.log"
    Rotate-Log $out; Rotate-Log $err
    $previousEnvironment = Import-DotEnv (Join-Path $WebRoot ".env.local")
    $previousHostname = $env:HOSTNAME; $previousPort = $env:PORT
    $previousScannerToken = $env:ADAPTIVE_ELECTRONIC_SCANNER_TOKEN
    $env:HOSTNAME = $LanAddress; $env:PORT = "3000"
    $env:ADAPTIVE_ELECTRONIC_SCANNER_TOKEN = $BackendScannerToken
    try {
        $process = Start-Process -FilePath $Node -ArgumentList @($standaloneServer) -WorkingDirectory $WebRoot -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    } finally {
        $env:HOSTNAME = $previousHostname; $env:PORT = $previousPort
        $env:ADAPTIVE_ELECTRONIC_SCANNER_TOKEN = $previousScannerToken
        Restore-Environment $previousEnvironment
    }
    Set-Content -LiteralPath $FrontendPid -Value $process.Id -Encoding ascii
}
$frontendReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try { if ((Invoke-WebRequest -Uri "$LanBaseUrl/login" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $frontendReady = $true; break } } catch { Start-Sleep -Seconds 1 }
}
if (-not $frontendReady) { throw "Local frontend did not become healthy. Check $RuntimeRoot\frontend.err.log" }
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path -LiteralPath $Tailscale) {
    try {
        $tailscaleStatus = (& $Tailscale status --json 2>$null | Out-String) | ConvertFrom-Json
        if ($tailscaleStatus.BackendState -eq "Running" -and $tailscaleStatus.Self.Online -and $tailscaleStatus.Self.DNSName) {
            & $Tailscale funnel --bg --https=443 $LanBaseUrl 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $publicUrl = "https://$($tailscaleStatus.Self.DNSName.TrimEnd('.'))"
                Set-Content -LiteralPath (Join-Path $RuntimeRoot "public-address.txt") -Value $publicUrl -Encoding ascii
                if ($Desktop) {
                    Set-Content -LiteralPath (Join-Path $Desktop "TWSE-public-url.txt") -Value @(
                        "TWSE Public URL", "URL: $publicUrl", "Username: admin", "Password: 111"
                    ) -Encoding utf8
                }
            }
        }
    } catch {
        "$(Get-Date -Format o) Tailscale Funnel update failed: $_" | Add-Content -LiteralPath (Join-Path $RuntimeRoot "supervisor.log")
    }
}
Write-Output "Local TWSE is ready: $LanBaseUrl"
