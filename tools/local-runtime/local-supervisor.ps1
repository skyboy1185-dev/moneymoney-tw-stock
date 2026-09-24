$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$RuntimeRoot = Join-Path $ProjectRoot ".local-runtime"
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$log = Join-Path $RuntimeRoot "supervisor.log"
$mutex = New-Object System.Threading.Mutex($false, "Local\TWSELocalSupervisor")
if (-not $mutex.WaitOne(0, $false)) { exit 0 }
function Write-SupervisorLog([string]$Message) {
    "$(Get-Date -Format o) $Message" | Add-Content -LiteralPath $log
}
try {
    & (Join-Path $PSScriptRoot "start-local.ps1") -NoBuild *>> $log
    $failureCount = 0
    $restartFailures = 0
    $nextRestartAllowed = Get-Date
    $nextBackup = (Get-Date).Date.AddHours(15).AddMinutes(45)
    if ($nextBackup -le (Get-Date)) { $nextBackup = $nextBackup.AddDays(1) }
    Write-SupervisorLog "Supervisor started; health checks run every 60 seconds."
    while ($true) {
        Start-Sleep -Seconds 60
        $healthOutput = & (Join-Path $PSScriptRoot "health-local.ps1") 2>&1
        $healthy = $LASTEXITCODE -eq 0
        if ($healthy) {
            if ($failureCount -gt 0 -or $restartFailures -gt 0) { Write-SupervisorLog "Services healthy again." }
            $failureCount = 0
            $restartFailures = 0
            $nextRestartAllowed = Get-Date
        } else {
            $failureCount++
            Write-SupervisorLog "Health check failed ($failureCount/2): $($healthOutput | Out-String)"
            if ($failureCount -ge 2 -and (Get-Date) -ge $nextRestartAllowed) {
                Write-SupervisorLog "Restarting local web services after consecutive health failures."
                & (Join-Path $PSScriptRoot "stop-local.ps1") *>> $log
                & (Join-Path $PSScriptRoot "start-local.ps1") -NoBuild *>> $log
                $healthOutput = & (Join-Path $PSScriptRoot "health-local.ps1") 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-SupervisorLog "Automatic restart succeeded."
                    $failureCount = 0
                    $restartFailures = 0
                    $nextRestartAllowed = Get-Date
                } else {
                    $restartFailures++
                    $delayMinutes = if ($restartFailures -lt 3) { 1 } else { 5 }
                    $nextRestartAllowed = (Get-Date).AddMinutes($delayMinutes)
                    Write-SupervisorLog "Automatic restart failed ($restartFailures); retry after $delayMinutes minute(s): $($healthOutput | Out-String)"
                }
            }
        }
        if ((Get-Date) -ge $nextBackup) {
            try {
                & (Join-Path $PSScriptRoot "backup-local.ps1") *>> $log
                Write-SupervisorLog "Daily backup completed."
            } catch {
                Write-SupervisorLog "Backup failed: $_"
            }
            do { $nextBackup = $nextBackup.AddDays(1) } while ($nextBackup -le (Get-Date))
        }
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
