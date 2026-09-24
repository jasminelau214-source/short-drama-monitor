param(
    [string]$OutputRoot = "D:\ShortDramaCollector",
    [string]$AdbExe = "C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    [string]$AdbSerial = $env:JSM_ADB_SERIAL,
    [int]$UiDumpTimeoutSec = 10
)

$ErrorActionPreference = "Stop"
$StartedAt = Get-Date
$Date = Get-Date -Format "yyyy-MM-dd"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$AcceptanceRoot = Join-Path (Join-Path $OutputRoot $Date) "Acceptance"
New-Item -ItemType Directory -Path $AcceptanceRoot -Force | Out-Null
$ManifestPath = Join-Path $AcceptanceRoot "app_device_acceptance_$Stamp.json"

$CollectorDir = Join-Path $PSScriptRoot "collectors"
$Definitions = @(
    [PSCustomObject]@{
        Platform = "NetShort"
        Script = Join-Path $CollectorDir "netshort_collector_v6.ps1"
        Prefix = "netshort_top10_"
        Version = "netshort-v6-integration"
        Method = "APP_UI_XML"
        Target = "daily_top_all"
        EvidenceField = "ui_xml"
    },
    [PSCustomObject]@{
        Platform = "MoboReels"
        Script = Join-Path $CollectorDir "moboreels_collector_v3.ps1"
        Prefix = "moboreels_top10_"
        Version = "moboreels-v3-integration"
        Method = "APP_UI_XML_SCROLL"
        Target = "daily_top_all"
        EvidenceField = "ui_xml_pages"
    }
)

function Fail-Result([string]$Platform,[string]$Message) {
    return [PSCustomObject]@{
        platform = $Platform
        status = "FAIL"
        error = $Message
        output = ""
        evidenceFiles = @()
        sha256 = ""
    }
}

function Resolve-EvidenceFiles($Payload,[string]$Field) {
    $files = @()
    if ($Field -eq "ui_xml") {
        $value = [string]$Payload.evidence.ui_xml
        if ($value) { $files += $value }
    }
    else {
        foreach ($value in @($Payload.evidence.ui_xml_pages)) {
            if ([string]$value) { $files += [string]$value }
        }
    }
    if ([string]$Payload.evidence.screenshot) {
        $files += [string]$Payload.evidence.screenshot
    }
    return @($files | Select-Object -Unique)
}

$Results = @()

foreach ($Definition in $Definitions) {
    $Platform = $Definition.Platform
    $RunStarted = Get-Date
    Write-Host ""
    Write-Host "=== Device acceptance: $Platform ===" -ForegroundColor Cyan

    try {
        if (-not (Test-Path $Definition.Script)) {
            throw "COLLECTOR_SCRIPT_NOT_FOUND:$($Definition.Script)"
        }

        $Args = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $Definition.Script,
            "-AdbExe", $AdbExe,
            "-OutputRoot", $OutputRoot,
            "-UiDumpTimeoutSec", [string]$UiDumpTimeoutSec
        )
        if ($AdbSerial) {
            $Args += @("-AdbSerial", $AdbSerial)
        }

        & powershell.exe @Args
        $ExitCode = $LASTEXITCODE
        if ($ExitCode -ne 0) {
            throw "COLLECTOR_EXIT_NONZERO:$ExitCode"
        }

        $PlatformDir = Join-Path (Join-Path $OutputRoot $Date) $Platform
        if (-not (Test-Path $PlatformDir)) {
            throw "OUTPUT_DIR_MISSING:$PlatformDir"
        }

        $FreshCutoff = $RunStarted.AddSeconds(-2)
        $Output = Get-ChildItem -Path $PlatformDir -Filter ($Definition.Prefix + "*.json") -File |
            Where-Object { $_.LastWriteTime -ge $FreshCutoff } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if (-not $Output) {
            throw "CURRENT_RUN_OUTPUT_NOT_FOUND"
        }

        $Payload = Get-Content -Raw -Encoding UTF8 $Output.FullName | ConvertFrom-Json

        if ([string]$Payload.platform -ne $Platform) {
            throw "PLATFORM_MISMATCH:$($Payload.platform)"
        }
        if ([string]$Payload.source_type -ne "SHORT_DRAMA_APP") {
            throw "SOURCE_TYPE_INVALID:$($Payload.source_type)"
        }
        if ([string]$Payload.collection_method -ne $Definition.Method) {
            throw "METHOD_MISMATCH:$($Payload.collection_method)"
        }
        if ([string]$Payload.collector_version -ne $Definition.Version) {
            throw "VERSION_MISMATCH:$($Payload.collector_version)"
        }
        if ([string]$Payload.target_key -ne $Definition.Target) {
            throw "TARGET_MISMATCH:$($Payload.target_key)"
        }
        if (-not [bool]$Payload.batch_complete) {
            throw "BATCH_INCOMPLETE"
        }
        if ([int]$Payload.top_n -ne 10 -or @($Payload.rows).Count -ne 10) {
            throw "TOP10_INCOMPLETE"
        }
        if ($Payload.evidence.originalSourceType -ne "SHORT_DRAMA_APP") {
            throw "ORIGINAL_SOURCE_INVALID"
        }
        if ($Payload.evidence.semanticVerified -ne $true) {
            throw "TARGET_SEMANTIC_UNVERIFIED"
        }
        if ($Payload.evidence.appFocusVerified -ne $true) {
            throw "APP_FOCUS_UNVERIFIED"
        }
        if (@($Payload.rank_conflicts).Count -gt 0) {
            throw "RANK_CONFLICT"
        }

        $Ranks = @($Payload.rows | ForEach-Object { [int]$_.rank } | Sort-Object)
        if (($Ranks -join ",") -ne ((1..10) -join ",")) {
            throw "RANK_SEQUENCE_INVALID:$($Ranks -join ',')"
        }

        $Titles = @($Payload.rows | ForEach-Object { [string]$_.title })
        if (@($Titles | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
            throw "BLANK_TITLE"
        }
        if (@($Titles | Group-Object | Where-Object { $_.Count -gt 1 }).Count -gt 0) {
            throw "DUPLICATE_TITLE"
        }

        $EvidenceFiles = Resolve-EvidenceFiles $Payload $Definition.EvidenceField
        if ($Definition.Method -eq "APP_UI_XML" -and $EvidenceFiles.Count -lt 1) {
            throw "UI_EVIDENCE_MISSING"
        }
        if ($Definition.Method -eq "APP_UI_XML_SCROLL" -and @($Payload.evidence.ui_xml_pages).Count -lt 1) {
            throw "UI_PAGE_EVIDENCE_MISSING"
        }

        foreach ($File in $EvidenceFiles) {
            if (-not (Test-Path -LiteralPath $File)) {
                throw "EVIDENCE_FILE_MISSING:$File"
            }
            if ((Get-Item -LiteralPath $File).Length -le 0) {
                throw "EVIDENCE_FILE_EMPTY:$File"
            }
        }

        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Output.FullName).Hash.ToLowerInvariant()

        $Results += [PSCustomObject]@{
            platform = $Platform
            status = "PASS"
            error = ""
            output = $Output.FullName
            collectorVersion = [string]$Payload.collector_version
            collectionMethod = [string]$Payload.collection_method
            targetKey = [string]$Payload.target_key
            rowCount = @($Payload.rows).Count
            evidenceFiles = @($EvidenceFiles)
            sha256 = $Hash
        }

        Write-Host "PASS $Platform" -ForegroundColor Green
    }
    catch {
        $Results += Fail-Result $Platform $_.Exception.Message
        Write-Host "FAIL $Platform :: $($_.Exception.Message)" -ForegroundColor Red
    }
}

$Passed = @($Results | Where-Object { $_.status -eq "PASS" })
$Failed = @($Results | Where-Object { $_.status -ne "PASS" })

$Manifest = [ordered]@{
    acceptanceVersion = "app-device-acceptance-v1"
    productionWrite = $false
    date = $Date
    startedAt = $StartedAt.ToString("o")
    completedAt = (Get-Date).ToString("o")
    adbSerialRequested = $AdbSerial
    outputRoot = $OutputRoot
    results = @($Results)
    summary = [ordered]@{
        total = $Results.Count
        passed = $Passed.Count
        failed = $Failed.Count
        status = if ($Failed.Count -eq 0 -and $Passed.Count -eq $Definitions.Count) { "PASS" } else { "FAIL" }
    }
}

$Manifest | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $ManifestPath

Write-Host ""
Write-Host "Acceptance manifest: $ManifestPath" -ForegroundColor Cyan
Write-Host ("Result: " + $Manifest.summary.status) -ForegroundColor $(if ($Manifest.summary.status -eq "PASS") { "Green" } else { "Red" })

if ($Manifest.summary.status -eq "PASS") {
    exit 0
}
exit 2
