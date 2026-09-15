param(
    [string]$BaseUrl = "https://short-drama-monitor.onrender.com",
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-LatestCollectorJson([string]$Platform, [string]$Prefix) {
    $dir = Join-Path (Join-Path $Root $CollectionDate) $Platform
    if (-not (Test-Path $dir)) { throw "COLLECTOR_DIR_NOT_FOUND: $dir" }
    $file = Get-ChildItem -Path $dir -Filter "${Prefix}_*.json" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $file) { throw "COLLECTOR_JSON_NOT_FOUND: $dir\${Prefix}_*.json" }
    return $file
}

function Convert-SecureStringToPlain([Security.SecureString]$Secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Import-CollectorJson([System.IO.FileInfo]$File, [string]$AuthHeader) {
    $raw = Get-Content -Raw -Encoding UTF8 $File.FullName
    $parsed = $raw | ConvertFrom-Json
    if (-not $parsed.batch_complete) { throw "LOCAL_AUDIT_FAILED: $($File.FullName)" }

    $headers = @{ Authorization = $AuthHeader }
    Write-Host ""
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

try {
    Write-Host "Short Drama Collector -> Backend Sync V2" -ForegroundColor Cyan
    Write-Host "Date: $CollectionDate"
    Write-Host "Backend: $BaseUrl"
    Write-Host ""

    Write-Host "Step 1/3  Checking local collector files..." -ForegroundColor Cyan
    $netshort = Get-LatestCollectorJson "NetShort" "netshort_top10"
    $moboreels = Get-LatestCollectorJson "MoboReels" "moboreels_top10"
    Write-Host ("NetShort : " + $netshort.FullName) -ForegroundColor Green
    Write-Host ("MoboReels: " + $moboreels.FullName) -ForegroundColor Green
    Write-Host ""

    Write-Host "Step 2/3  Backend login" -ForegroundColor Cyan
    Write-Host "Username: admin"
    Write-Host "Admin password (type here; the password will not be shown):" -ForegroundColor Yellow
    $securePassword = Read-Host -AsSecureString
    if (-not $securePassword -or $securePassword.Length -eq 0) { throw "ADMIN_PASSWORD_EMPTY" }
    $plainPassword = Convert-SecureStringToPlain $securePassword
    $pair = "admin:$plainPassword"
    $auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    $authHeader = "Basic $auth"
    $plainPassword = $null
    $pair = $null
    Write-Host "Password received." -ForegroundColor Green

    Write-Host ""
    Write-Host "Step 3/3  Syncing Top10 JSON to backend..." -ForegroundColor Cyan
    $results = @()
    $results += Import-CollectorJson $netshort $authHeader
    $results += Import-CollectorJson $moboreels $authHeader

    Write-Host ""
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host "SYNC COMPLETE" -ForegroundColor Green
    Write-Host "==============================" -ForegroundColor Cyan
    $results | ForEach-Object {
        Write-Host ("{0}: rows={1}, new={2}, factPublished={3}" -f $_.platform, $_.rows, $_.newTitleCount, $_.factPublished)
    }
    Write-Host ""
    Write-Host ("Check: " + $BaseUrl.TrimEnd("/") + "/") -ForegroundColor Cyan
}
catch {
    Write-Host ""
    Write-Host "==============================" -ForegroundColor Red
    Write-Host "SYNC FAILED" -ForegroundColor Red
    Write-Host "==============================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Copy the red error line and send it to ChatGPT." -ForegroundColor Yellow
    exit 1
}
