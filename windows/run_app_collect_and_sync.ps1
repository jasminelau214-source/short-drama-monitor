param(
    [string]$OutputRoot = "D:\ShortDramaCollector",
    [string]$AdbExe = "C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    [string]$AdbSerial = $env:JSM_ADB_SERIAL,
    [int]$UiDumpTimeoutSec = 10,
    [string]$BaseUrl = "http://127.0.0.1:4173",
    [switch]$AllowProductionWrite
)

$ErrorActionPreference = "Stop"
$CollectionDate = Get-Date -Format "yyyy-MM-dd"
$RunStartedAt = Get-Date
$RunId = "app-" + (Get-Date -Format "yyyyMMdd_HHmmssfff")

$collectorDir = Join-Path $PSScriptRoot "collectors"
$syncScript = Join-Path $PSScriptRoot "collector_sync.ps1"
$manifestDir = Join-Path $OutputRoot "manifests"
New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
$manifestPath = Join-Path $manifestDir ("app-{0}-{1}.json" -f $CollectionDate, $RunId)

if (-not (Test-Path $syncScript)) {
    throw "SYNC_SCRIPT_NOT_FOUND: $syncScript"
}
if ($UiDumpTimeoutSec -lt 1 -or $UiDumpTimeoutSec -gt 120) {
    throw "INVALID_ADB_TIMEOUT: $UiDumpTimeoutSec"
}

$definitions = @(
    [PSCustomObject]@{
        Platform = "NetShort"
        Script = (Join-Path $collectorDir "netshort_collector_v6.ps1")
        Prefix = "netshort_top10_"
        ExpectedVersion = "netshort-v6-integration"
        ExpectedMethod = "APP_UI_XML"
        ExpectedTarget = "daily_top_all"
    },
    [PSCustomObject]@{
        Platform = "MoboReels"
        Script = (Join-Path $collectorDir "moboreels_collector_v3.ps1")
        Prefix = "moboreels_top10_"
        ExpectedVersion = "moboreels-v3-integration"
        ExpectedMethod = "APP_UI_XML_SCROLL"
        ExpectedTarget = "daily_top_all"
    }
)

$succeeded = @()
$failed = @()

foreach ($definition in $definitions) {
    $platform = $definition.Platform
    $script = $definition.Script
    $started = Get-Date

    try {
        if (-not (Test-Path $script)) {
            throw "COLLECTOR_SCRIPT_NOT_FOUND:$script"
        }

        Write-Host ""
        Write-Host ("=== Collecting {0} ===" -f $platform) -ForegroundColor Cyan

        $args = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $script,
            "-AdbExe", $AdbExe,
            "-OutputRoot", $OutputRoot,
            "-UiDumpTimeoutSec", [string]$UiDumpTimeoutSec
        )
        if ($AdbSerial) {
            $args += @("-AdbSerial", $AdbSerial)
        }

        & powershell.exe @args
        $collectorExit = $LASTEXITCODE
        if ($collectorExit -ne 0) {
            throw "COLLECTOR_EXIT_NONZERO:$platform:$collectorExit"
        }

        $platformDir = Join-Path (Join-Path $OutputRoot $CollectionDate) $platform
        if (-not (Test-Path $platformDir)) {
            throw "COLLECTOR_OUTPUT_DIR_MISSING:$platformDir"
        }

        $freshCutoff = $started.AddSeconds(-2)
        $output = Get-ChildItem -Path $platformDir -Filter ($definition.Prefix + "*.json") -File |
            Where-Object { $_.LastWriteTime -ge $freshCutoff } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if (-not $output) {
            throw "CURRENT_RUN_OUTPUT_NOT_FOUND:$platform"
        }

        $payload = Get-Content -Raw -Encoding UTF8 $output.FullName | ConvertFrom-Json
        if ([string]$payload.platform -ne $platform) {
            throw "OUTPUT_PLATFORM_MISMATCH:$platform:$($payload.platform)"
        }
        if ([string]$payload.collection_date -ne $CollectionDate) {
            throw "OUTPUT_DATE_MISMATCH:$platform:$($payload.collection_date)"
        }
        if (-not [bool]$payload.batch_complete) {
            throw "OUTPUT_BATCH_INCOMPLETE:$platform"
        }
        if ([string]$payload.source_type -ne "SHORT_DRAMA_APP") {
            throw "OUTPUT_SOURCE_TYPE_INVALID:$platform:$($payload.source_type)"
        }
        if ([string]$payload.collection_method -ne $definition.ExpectedMethod) {
            throw "OUTPUT_METHOD_MISMATCH:$platform:$($payload.collection_method)"
        }
        if ([string]$payload.target_key -ne $definition.ExpectedTarget) {
            throw "OUTPUT_TARGET_MISMATCH:$platform:$($payload.target_key)"
        }
        if ([string]$payload.collector_version -ne $definition.ExpectedVersion) {
            throw "OUTPUT_VERSION_MISMATCH:$platform:$($payload.collector_version)"
        }
        if (@($payload.rows).Count -ne [int]$payload.top_n) {
            throw "OUTPUT_TOPN_ROWCOUNT_MISMATCH:$platform"
        }

        $succeeded += [PSCustomObject]@{
            platform = $platform
            path = $output.FullName
            targetKey = [string]$payload.target_key
            sourceType = [string]$payload.source_type
            collectorVersion = [string]$payload.collector_version
            rowCount = @($payload.rows).Count
            completedAt = (Get-Date).ToString("o")
        }

        Write-Host ("PASS {0}: {1}" -f $platform, $output.FullName) -ForegroundColor Green
    }
    catch {
        $message = $_.Exception.Message
        $failed += [PSCustomObject]@{
            platform = $platform
            error = $message
            failedAt = (Get-Date).ToString("o")
        }
        Write-Host ("FAIL {0}: {1}" -f $platform, $message) -ForegroundColor Red
    }
}

$manifest = [ordered]@{
    runId = $RunId
    date = $CollectionDate
    startedAt = $RunStartedAt.ToString("o")
    completedAt = (Get-Date).ToString("o")
    sourceType = "SHORT_DRAMA_APP"
    productionWriteRequested = [bool]$AllowProductionWrite.IsPresent
    succeeded = @($succeeded)
    failed = @($failed)
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $manifestPath
Write-Host ""
Write-Host ("Manifest: " + $manifestPath) -ForegroundColor Cyan

if ($succeeded.Count -eq 0) {
    Write-Host "APP COLLECTION FAILED: no platform produced a valid current-run payload." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host ("Syncing {0} successful App target(s) by exact manifest..." -f $succeeded.Count) -ForegroundColor Cyan

if ($AllowProductionWrite.IsPresent) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $syncScript `
        -CollectionDate $CollectionDate `
        -Root $OutputRoot `
        -ManifestPath $manifestPath `
        -BaseUrl $BaseUrl `
        -AllowProductionWrite
}
else {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $syncScript `
        -CollectionDate $CollectionDate `
        -Root $OutputRoot `
        -ManifestPath $manifestPath `
        -BaseUrl $BaseUrl
}

$syncExit = $LASTEXITCODE
if ($syncExit -ne 0) {
    Write-Host ("APP SYNC FAILED: exit=" + $syncExit) -ForegroundColor Red
    exit 1
}

if ($failed.Count -gt 0) {
    Write-Host ("APP COLLECTION PARTIAL: synced=" + $succeeded.Count + " failed=" + $failed.Count) -ForegroundColor Yellow
    exit 2
}

Write-Host ("APP COLLECTION + SYNC COMPLETE: platforms=" + $succeeded.Count) -ForegroundColor Green
exit 0
