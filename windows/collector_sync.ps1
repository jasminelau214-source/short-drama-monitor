param(
    [string]$BaseUrl = "https://short-drama-monitor.onrender.com",
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-CollectorJsonFiles {
    $dateDir = Join-Path $Root $CollectionDate
    if (-not (Test-Path $dateDir)) { throw "COLLECTOR_DATE_DIR_NOT_FOUND: $dateDir" }

    $allValid = @()
    Get-ChildItem -Path $dateDir -Directory | ForEach-Object {
        $platformDir = $_
        Get-ChildItem -Path $platformDir.FullName -Filter "*.json" -File | ForEach-Object {
            try {
                $parsed = Get-Content -Raw -Encoding UTF8 $_.FullName | ConvertFrom-Json
                if ($parsed.platform -and $parsed.batch_complete -and $parsed.rows -and $parsed.rows.Count -gt 0) {
                    $targetKey = if ($parsed.target_key) { [string]$parsed.target_key } else { "daily_top_all" }
                    $allValid += [PSCustomObject]@{
                        File = $_
                        Platform = [string]$parsed.platform
                        RowCount = [int]$parsed.rows.Count
                        TopN = if ($parsed.top_n) { [int]$parsed.top_n } else { [int]$parsed.rows.Count }
                        TargetKey = $targetKey
                        SourceType = if ($parsed.source_type) { [string]$parsed.source_type } else { "SHORT_DRAMA_APP" }
                        LastWriteTime = $_.LastWriteTime
                    }
                }
            }
            catch {
                Write-Host ("Skip invalid JSON: " + $_.FullName) -ForegroundColor DarkYellow
            }
        }
    }

    if ($allValid.Count -eq 0) { throw "NO_COMPLETE_COLLECTOR_JSON: $dateDir" }

    # Keep the latest complete file for every platform + target pair.
    # This lets one platform carry several ranking lists without one file overwriting another.
    $selected = @()
    $allValid | Group-Object Platform, TargetKey | ForEach-Object {
        $selected += $_.Group | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }
    return @($selected | Sort-Object Platform, TargetKey)
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
    $targetKey = if ($parsed.target_key) { [string]$parsed.target_key } else { "daily_top_all" }
    $sourceType = if ($parsed.source_type) { [string]$parsed.source_type } else { "SHORT_DRAMA_APP" }
    Write-Host ""
    Write-Host ("Importing {0} / {1} / {2}: {3}" -f $parsed.platform, $targetKey, $sourceType, $File.Name) -ForegroundColor Cyan

    $response = Invoke-RestMethod `
        -Uri ($BaseUrl.TrimEnd("/") + "/api/admin/collector-import") `
        -Method Post `
        -Headers $headers `
        -ContentType "application/json; charset=utf-8" `
        -Body $raw `
        -TimeoutSec 180

    Write-Host ("PASS {0} / {1}: rows={2}, new={3}, status={4}" -f $response.platform, $targetKey, $response.rows, $response.newTitleCount, $response.status) -ForegroundColor Green
    if ($response.newTitles -and $response.newTitles.Count -gt 0 -and $sourceType -eq "SHORT_DRAMA_APP") {
        Write-Host ("New titles: " + ($response.newTitles -join " | ")) -ForegroundColor Yellow
    }
    return [PSCustomObject]@{
        Platform = [string]$response.platform
        TargetKey = $targetKey
        SourceType = $sourceType
        Rows = [int]$response.rows
        NewTitleCount = [int]$response.newTitleCount
        Status = [string]$response.status
        FactPublished = [bool]$response.factPublished
    }
}

try {
    Write-Host "Short Drama Collector -> Backend Sync V4 Multi-Platform Multi-Ranking" -ForegroundColor Cyan
    Write-Host "Date: $CollectionDate"
    Write-Host "Backend: $BaseUrl"
    Write-Host ""

    Write-Host "Step 1/3  Discovering complete collector JSON files..." -ForegroundColor Cyan
    $collectorFiles = Get-CollectorJsonFiles
    $collectorFiles | ForEach-Object {
        Write-Host ("{0} / {1} / {2}: {3} rows / Top{4} / {5}" -f $_.Platform, $_.TargetKey, $_.SourceType, $_.RowCount, $_.TopN, $_.File.FullName) -ForegroundColor Green
    }
    $platformCount = @($collectorFiles | Select-Object -ExpandProperty Platform -Unique).Count
    Write-Host ("Discovered targets: {0} across platforms: {1}" -f $collectorFiles.Count, $platformCount) -ForegroundColor Green
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
    Write-Host "Step 3/3  Syncing discovered ranking JSON to backend..." -ForegroundColor Cyan
    $results = @()
    foreach ($entry in $collectorFiles) {
        $results += Import-CollectorJson $entry.File $authHeader
    }

    Write-Host ""
    Write-Host "==============================" -ForegroundColor Cyan
    Write-Host "SYNC COMPLETE" -ForegroundColor Green
    Write-Host "==============================" -ForegroundColor Cyan
    $results | ForEach-Object {
        Write-Host ("{0} / {1}: rows={2}, new={3}, status={4}, factPublished={5}" -f $_.Platform, $_.TargetKey, $_.Rows, $_.NewTitleCount, $_.Status, $_.FactPublished)
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
