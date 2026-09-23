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
function Restore-Environment([hashtable]$Previous) {
    foreach ($name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Previous[$name], "Process")
    }
}

$Python = (Get-Command python.exe -ErrorAction Stop).Source
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$BackendPid = Join-Path $RuntimeRoot "backend.pid"
$FrontendPid = Join-Path $RuntimeRoot "frontend.pid"
if (-not (Test-RecordedProcess $BackendPid)) {
    $out = Join-Path $RuntimeRoot "backend.out.log"; $err = Join-Path $RuntimeRoot "backend.err.log"
    Rotate-Log $out; Rotate-Log $err
    $process = Start-Process -FilePath $Python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $BackendRoot -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
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
    $env:HOSTNAME = "127.0.0.1"; $env:PORT = "3000"
    try {
        $process = Start-Process -FilePath $Node -ArgumentList @($standaloneServer) -WorkingDirectory $WebRoot -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    } finally {
        $env:HOSTNAME = $previousHostname; $env:PORT = $previousPort
        Restore-Environment $previousEnvironment
    }
    Set-Content -LiteralPath $FrontendPid -Value $process.Id -Encoding ascii
}
$frontendReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try { if ((Invoke-WebRequest -Uri "http://127.0.0.1:3000/login" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $frontendReady = $true; break } } catch { Start-Sleep -Seconds 1 }
}
if (-not $frontendReady) { throw "Local frontend did not become healthy. Check $RuntimeRoot\frontend.err.log" }
Write-Output "Local TWSE is ready: http://127.0.0.1:3000"
