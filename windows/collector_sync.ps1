param(
    [string]$BaseUrl = "https://short-drama-monitor.onrender.com",
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector"
)

$ErrorActionPreference = "Stop"

function Get-LatestCollectorJson([string]$Platform, [string]$Prefix) {
    $dir = Join-Path (Join-Path $Root $CollectionDate) $Platform
    if (-not (Test-Path $dir)) { throw "COLLECTOR_DIR_NOT_FOUND: $dir" }
    $file = Get-ChildItem -Path $dir -Filter "${Prefix}_*.json" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $file) { throw "COLLECTOR_JSON_NOT_FOUND: $dir\${Prefix}_*.json" }
    return $file
}

function Import-CollectorJson([System.IO.FileInfo]$File, [pscredential]$Credential) {
    $raw = Get-Content -Raw -Encoding UTF8 $File.FullName
    $parsed = $raw | ConvertFrom-Json
    if (-not $parsed.batch_complete) { throw "LOCAL_AUDIT_FAILED: $($File.FullName)" }

    $pair = "{0}:{1}" -f $Credential.UserName, $Credential.GetNetworkCredential().Password
    $auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    $headers = @{ Authorization = "Basic $auth" }

    Write-Host "Importing $($parsed.platform): $($File.Name)" -ForegroundColor Cyan
    $response = Invoke-RestMethod `
        -Uri ($BaseUrl.TrimEnd("/") + "/api/admin/collector-import") `
        -Method Post `
        -Headers $headers `
        -ContentType "application/json; charset=utf-8" `
        -Body $raw `
        -TimeoutSec 180

    Write-Host ("PASS {0}: rows={1}, new={2}, status={3}" -f $response.platform, $response.rows, $response.newTitleCount, $response.status) -ForegroundColor Green
    if ($response.newTitles -and $response.newTitles.Count -gt 0) {
        Write-Host ("New titles: " + ($response.newTitles -join " | ")) -ForegroundColor Yellow
    }
    return $response
}

Write-Host "Short Drama Collector -> Backend Sync" -ForegroundColor Cyan
Write-Host "Date: $CollectionDate"
Write-Host "Backend: $BaseUrl"
Write-Host ""

$credential = Get-Credential -UserName "admin" -Message "输入 short-drama-monitor 管理员账号/密码"
$netshort = Get-LatestCollectorJson "NetShort" "netshort_top10"
$moboreels = Get-LatestCollectorJson "MoboReels" "moboreels_top10"

$results = @()
$results += Import-CollectorJson $netshort $credential
$results += Import-CollectorJson $moboreels $credential

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "SYNC COMPLETE" -ForegroundColor Green
Write-Host "==============================" -ForegroundColor Cyan
$results | ForEach-Object {
    Write-Host ("{0}: rows={1}, new={2}, factPublished={3}" -f $_.platform, $_.rows, $_.newTitleCount, $_.factPublished)
}
Write-Host ""
Write-Host ("Check: " + $BaseUrl.TrimEnd("/") + "/") -ForegroundColor Cyan
