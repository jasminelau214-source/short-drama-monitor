param(
    [string]$BaseUrl = "http://127.0.0.1:4173",
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector",
    [string]$ManifestPath = "",
    [int]$MaxCollectorAgeMinutes = 90,
    [switch]$AllowProductionWrite
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

try {
    $targetUri = [Uri]$BaseUrl
}
catch {
    throw "SYNC_BASE_URL_INVALID: $BaseUrl"
}
if ($targetUri.Scheme -notin @("http","https")) {
    throw "SYNC_BASE_URL_SCHEME_INVALID: $($targetUri.Scheme)"
}
$productionHosts = @("short-drama-monitor.onrender.com")
if (($productionHosts -contains $targetUri.Host) -and -not $AllowProductionWrite.IsPresent) {
    throw "PRODUCTION_WRITE_BLOCKED: pass -AllowProductionWrite explicitly to target $($targetUri.Host)"
}

function Get-CollectorJsonFiles {
    $dateDir = Join-Path $Root $CollectionDate
    if (-not (Test-Path $dateDir)) { throw "COLLECTOR_DATE_DIR_NOT_FOUND: $dateDir" }

    if ($ManifestPath) {
        if (-not (Test-Path $ManifestPath)) { throw "COLLECTOR_MANIFEST_NOT_FOUND: $ManifestPath" }
        $manifest = Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
        if ([string]$manifest.date -ne $CollectionDate) {
            throw "COLLECTOR_MANIFEST_DATE_MISMATCH: expected=$CollectionDate actual=$($manifest.date)"
        }
        $manifestFiles = @()
        foreach ($item in @($manifest.succeeded)) {
            $path = [string]$item.path
            if (-not $path -or -not (Test-Path $path)) {
                throw "COLLECTOR_MANIFEST_FILE_MISSING: $path"
            }
            $file = Get-Item -LiteralPath $path
            $parsed = Get-Content -Raw -Encoding UTF8 $file.FullName | ConvertFrom-Json
            if (-not $parsed.batch_complete) { throw "COLLECTOR_MANIFEST_INCOMPLETE: $path" }
            if ([string]$parsed.collection_date -ne $CollectionDate) {
                throw "COLLECTOR_FILE_DATE_MISMATCH: $path"
            }
            $manifestFiles += [PSCustomObject]@{
                File = $file
                Platform = [string]$parsed.platform
                RowCount = [int]$parsed.rows.Count
                TopN = if ($parsed.top_n) { [int]$parsed.top_n } else { [int]$parsed.rows.Count }
                TargetKey = if ($parsed.target_key) { [string]$parsed.target_key } else { "daily_top_all" }
                SourceType = if ($parsed.source_type) { [string]$parsed.source_type } else { "SHORT_DRAMA_APP" }
                LastWriteTime = $file.LastWriteTime
            }
        }
        if ($manifestFiles.Count -eq 0) { throw "COLLECTOR_MANIFEST_HAS_NO_SUCCEEDED_FILES: $ManifestPath" }
        $dupes = @($manifestFiles | Group-Object Platform, TargetKey | Where-Object { $_.Count -gt 1 })
        if ($dupes.Count -gt 0) { throw "COLLECTOR_MANIFEST_DUPLICATE_TARGET" }
        return @($manifestFiles | Sort-Object Platform, TargetKey)
    }

    if ($MaxCollectorAgeMinutes -lt 1 -or $MaxCollectorAgeMinutes -gt 1440) {
        throw "COLLECTOR_MAX_AGE_INVALID: $MaxCollectorAgeMinutes"
    }
    $freshCutoff = (Get-Date).AddMinutes(-$MaxCollectorAgeMinutes)

    $allValid = @()
    Get-ChildItem -Path $dateDir -Directory | ForEach-Object {
        $platformDir = $_
        Get-ChildItem -Path $platformDir.FullName -Filter "*.json" -File | ForEach-Object {
            if ($_.LastWriteTime -lt $freshCutoff) {
                Write-Host ("Skip stale collector JSON (> " + $MaxCollectorAgeMinutes + " min): " + $_.FullName) -ForegroundColor DarkYellow
            }
            else {
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

function Get-AdminPasswordPlain {
    $envPassword = [string]$env:SHORT_DRAMA_ADMIN_PASSWORD
    if ($envPassword -and $envPassword.Trim().Length -gt 0) {
        Write-Host "Admin password loaded from SHORT_DRAMA_ADMIN_PASSWORD for unattended sync." -ForegroundColor Green
        return $envPassword
    }

    Write-Host "Admin password (type here; the password will not be shown):" -ForegroundColor Yellow
    $securePassword = Read-Host -AsSecureString
    if (-not $securePassword -or $securePassword.Length -eq 0) { throw "ADMIN_PASSWORD_EMPTY" }
    return Convert-SecureStringToPlain $securePassword
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
    if ($ManifestPath) { Write-Host "Manifest-only sync: $ManifestPath" }
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
    $plainPassword = Get-AdminPasswordPlain
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
