param([string]$BackupRoot = "D:\TWSE-backups", [int]$RetentionDays = 14)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$Database = Join-Path $ProjectRoot "backend/data/moneymoney-backend.db"
if (-not (Test-Path -LiteralPath $Database)) { throw "Local database not found: $Database" }
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMddTHHmmss"; $snapshot = Join-Path $BackupRoot "moneymoney-backend-$stamp.db"
$env:TASK_SQLITE_SOURCE = $Database; $env:TASK_SQLITE_TARGET = $snapshot
@'
import os, sqlite3
source=sqlite3.connect(os.environ["TASK_SQLITE_SOURCE"]); target=sqlite3.connect(os.environ["TASK_SQLITE_TARGET"])
with target: source.backup(target)
assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
source.close(); target.close()
'@ | python -
$code = $LASTEXITCODE; Remove-Item Env:TASK_SQLITE_SOURCE, Env:TASK_SQLITE_TARGET
if ($code -ne 0) { throw "SQLite backup failed" }
$hash = (Get-FileHash -LiteralPath $snapshot -Algorithm SHA256).Hash
Set-Content -LiteralPath "$snapshot.sha256" -Value "$hash  $([IO.Path]::GetFileName($snapshot))" -Encoding ascii
$cutoff = (Get-Date).AddDays(-[Math]::Max(1, $RetentionDays))
Get-ChildItem -LiteralPath $BackupRoot -File | Where-Object { $_.LastWriteTime -lt $cutoff -and $_.Name -like "moneymoney-backend-*" } | Remove-Item -Force
Write-Output "Backup complete: $snapshot"
