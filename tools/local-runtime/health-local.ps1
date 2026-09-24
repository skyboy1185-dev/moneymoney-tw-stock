$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$addressFile = Join-Path $ProjectRoot ".local-runtime/lan-address.txt"
$publicAddressFile = Join-Path $ProjectRoot ".local-runtime/public-address.txt"
$frontendUrl = if (Test-Path -LiteralPath $addressFile) { (Get-Content -LiteralPath $addressFile -Raw).Trim() } else { "http://127.0.0.1:3000" }
$checks = @(@{ Name = "backend"; Url = "http://127.0.0.1:8000/api/v1/health" }, @{ Name = "frontend-lan"; Url = "$frontendUrl/login" })
if (Test-Path -LiteralPath $publicAddressFile) {
    $publicUrl = (Get-Content -LiteralPath $publicAddressFile -Raw).Trim()
    if ($publicUrl) { $checks += @{ Name = "frontend-public"; Url = "$publicUrl/login" } }
}
$failed = $false
foreach ($check in $checks) {
    try { $status = (Invoke-WebRequest -Uri $check.Url -UseBasicParsing -TimeoutSec 5).StatusCode; [pscustomobject]@{ Service = $check.Name; Status = $status; Healthy = $status -eq 200 } }
    catch { [pscustomobject]@{ Service = $check.Name; Status = 0; Healthy = $false }; $failed = $true }
}
if ($failed) { exit 1 }
