$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
try {
    $startAction = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'start-local.ps1')`" -NoBuild" -WorkingDirectory $ProjectRoot
    Register-ScheduledTask -TaskName "TWSE-Local-Startup" -Action $startAction -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $UserId) -Settings $settings -Description "Start local TWSE after logon" -Force -ErrorAction Stop | Out-Null
    $backupAction = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'backup-local.ps1')`"" -WorkingDirectory $ProjectRoot
    Register-ScheduledTask -TaskName "TWSE-Local-Backup" -Action $backupAction -Trigger (New-ScheduledTaskTrigger -Daily -At 3:45PM) -Settings $settings -Description "Create a verified daily SQLite backup and retain 14 days" -Force -ErrorAction Stop | Out-Null
    Write-Output "Installed scheduled tasks: TWSE-Local-Startup, TWSE-Local-Backup"
} catch {
    Unregister-ScheduledTask -TaskName "TWSE-Local-Startup" -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "TWSE-Local-Backup" -Confirm:$false -ErrorAction SilentlyContinue
    $startup = [Environment]::GetFolderPath("Startup")
    $launcher = Join-Path $startup "TWSE-Local.cmd"
    $supervisor = Join-Path $PSScriptRoot "local-supervisor.ps1"
    Set-Content -LiteralPath $launcher -Value "@start `"`" `"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisor`"" -Encoding ascii
    Write-Output "Task Scheduler unavailable; installed Startup supervisor: $launcher"
}
