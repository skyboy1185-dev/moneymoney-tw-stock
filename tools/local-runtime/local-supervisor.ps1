$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$RuntimeRoot = Join-Path $ProjectRoot ".local-runtime"
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$log = Join-Path $RuntimeRoot "supervisor.log"
$mutex = New-Object System.Threading.Mutex($false, "Local\TWSELocalSupervisor")
if (-not $mutex.WaitOne(0, $false)) { exit 0 }
try {
    & (Join-Path $PSScriptRoot "start-local.ps1") -NoBuild *>> $log
    while ($true) {
        $now = Get-Date
        $next = $now.Date.AddHours(15).AddMinutes(45)
        if ($next -le $now) { $next = $next.AddDays(1) }
        while ((Get-Date) -lt $next) { Start-Sleep -Seconds 60 }
        try { & (Join-Path $PSScriptRoot "backup-local.ps1") *>> $log }
        catch { "$(Get-Date -Format o) Backup failed: $_" | Add-Content -LiteralPath $log }
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
