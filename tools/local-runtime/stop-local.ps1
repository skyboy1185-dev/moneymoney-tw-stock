$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$RuntimeRoot = Join-Path $ProjectRoot ".local-runtime"
function Stop-ProcessTree([int]$ProcessId) {
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue)) { Stop-ProcessTree ([int]$child.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($name in @("frontend", "backend")) {
    $pidFile = Join-Path $RuntimeRoot "$name.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    Stop-ProcessTree ([int](Get-Content -LiteralPath $pidFile -Raw))
    Remove-Item -LiteralPath $pidFile -Force
}
Write-Output "Local TWSE services stopped."
