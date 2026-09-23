$checks = @(@{ Name = "backend"; Url = "http://127.0.0.1:8000/api/v1/health" }, @{ Name = "frontend"; Url = "http://127.0.0.1:3000/login" })
$failed = $false
foreach ($check in $checks) {
    try { $status = (Invoke-WebRequest -Uri $check.Url -UseBasicParsing -TimeoutSec 5).StatusCode; [pscustomobject]@{ Service = $check.Name; Status = $status; Healthy = $status -eq 200 } }
    catch { [pscustomobject]@{ Service = $check.Name; Status = 0; Healthy = $false }; $failed = $true }
}
if ($failed) { exit 1 }
