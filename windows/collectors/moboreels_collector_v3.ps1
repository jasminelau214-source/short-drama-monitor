# MoboReels Collector V3 - Integration hardened
# Production validation build.
# Verified route:
# BlueStacks -> MoboReels -> Charts -> Trending Series
# -> UI XML -> scroll -> merge ranks 1-10.
#
# V2 hardens:
# - content-desc line parsing
# - integer rank audit
# - duplicate handling
# - UI dump retries
# - startup overlay handling
# - screenshot remains evidence-only

param(
    [string]$AdbExe = "C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    [string]$OutputRoot = "D:\ShortDramaCollector",
    [string]$AdbSerial = $env:JSM_ADB_SERIAL,
    [int]$UiDumpTimeoutSec = 10
)

$ErrorActionPreference = "Stop"

$Package = "com.changdu.mobovideo"
$CollectorVersion = "moboreels-v3-integration"

if (-not (Test-Path $AdbExe)) {
    throw "ADB_NOT_FOUND: $AdbExe"
}

$Today = Get-Date -Format "yyyy-MM-dd"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Root = Join-Path (Join-Path $OutputRoot $Today) "MoboReels"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$Log = Join-Path $Root "collector_v3_$Stamp.log"

function Log([string]$Message,[string]$Color="Gray") {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $Message"
    Write-Host $line -ForegroundColor $Color
    Add-Content -Encoding UTF8 -Path $Log -Value $line
}

function Host-Adb([string[]]$CommandArgs) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return @(& $AdbExe @CommandArgs 2>&1 | ForEach-Object { "$_" })
    } finally {
        $ErrorActionPreference = $old
    }
}

$devicesText = (Host-Adb @("devices")) -join "`n"
$availableDevices = @()
foreach ($line in ($devicesText -split "`r?`n")) {
    if ($line -match "^(\S+)\s+device$") {
        $availableDevices += $matches[1]
    }
}

if ($AdbSerial) {
    if ($availableDevices -notcontains $AdbSerial) {
        throw "REQUESTED_ADB_DEVICE_NOT_AVAILABLE:$AdbSerial"
    }
    $Serial = $AdbSerial
} else {
    $blueStacksDevices = @($availableDevices | Where-Object { $_ -match "^127\.0\.0\.1:\d+$" })
    if ($blueStacksDevices.Count -eq 0) {
        throw "NO_BLUESTACKS_ADB_DEVICE"
    }
    if ($blueStacksDevices.Count -gt 1) {
        throw ("MULTIPLE_BLUESTACKS_DEVICES:" + ($blueStacksDevices -join ","))
    }
    $Serial = $blueStacksDevices[0]
}
Log "ADB device: $Serial" "Green"

function Adb([string[]]$CommandArgs) {
    return Host-Adb (@("-s",$Serial) + $CommandArgs)
}


# UIAutomator may hang indefinitely on some BlueStacks/App states.
# Run evidence-critical ADB commands in a child process with a hard timeout.
function Invoke-AdbWithTimeout {
    param(
        [string[]]$CommandArgs,
        [int]$TimeoutSec = $UiDumpTimeoutSec
    )

    if ($TimeoutSec -lt 1 -or $TimeoutSec -gt 120) {
        throw "INVALID_ADB_TIMEOUT:$TimeoutSec"
    }

    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()

    try {
        $allArgs = @("-s",$Serial) + $CommandArgs
        $argLine = ($allArgs | ForEach-Object {
            if ($_ -match '[\s"]') {
                '"' + ($_ -replace '"','\"') + '"'
            } else {
                $_
            }
        }) -join ' '

        $process = Start-Process -FilePath $AdbExe -ArgumentList $argLine -PassThru -NoNewWindow `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr

        if (-not $process.WaitForExit($TimeoutSec * 1000)) {
            try { $process.Kill() } catch {}
            try { [void]$process.WaitForExit(2000) } catch {}
            return @{
                TimedOut = $true
                ExitCode = $null
                Output = ""
                Error = "TIMEOUT"
            }
        }

        return @{
            TimedOut = $false
            ExitCode = $process.ExitCode
            Output = (Get-Content $stdout -Raw -ErrorAction SilentlyContinue)
            Error = (Get-Content $stderr -Raw -ErrorAction SilentlyContinue)
        }
    }
    finally {
        Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue
    }
}

function Current-Focus {
    $out = (Adb @("shell","dumpsys","window","windows")) -join "`n"
    $m = [regex]::Match($out,'mCurrentFocus=.*? ([A-Za-z0-9._-]+/[A-Za-z0-9._$-]+)')
    if ($m.Success) { return $m.Groups[1].Value }
    return ""
}

function Assert-App-Focus {
    $focus = Current-Focus
    if (-not $focus -or -not $focus.StartsWith("$Package/")) {
        throw "APP_FOCUS_MISMATCH:$focus"
    }
    return $focus
}

function Title-Key([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    return (($Value -replace '[^A-Za-z0-9]+','').ToLowerInvariant())
}

function Try-Dump-Ui([string]$Name,[int]$Attempts=4) {
    $remote = "/sdcard/$Name.xml"
    $local = Join-Path $Root "$Name.xml"

    for ($i=1; $i -le $Attempts; $i++) {
        if (Test-Path $local) {
            Remove-Item $local -Force -ErrorAction SilentlyContinue
        }
            $null = Adb @("shell","rm","-f",$remote)

            $dumpResult = Invoke-AdbWithTimeout `
                -CommandArgs @("shell","uiautomator","dump","--compressed",$remote) `
                -TimeoutSec $UiDumpTimeoutSec

            if ($dumpResult.TimedOut) {
                Log "UI dump attempt $i/$Attempts timed out after $UiDumpTimeoutSec seconds." "Yellow"
                Start-Sleep -Seconds 2
                continue
            }
            if ($dumpResult.ExitCode -ne 0) {
                Log "UI dump attempt $i/$Attempts failed exit=$($dumpResult.ExitCode) stderr=[$($dumpResult.Error)]" "Yellow"
                Start-Sleep -Seconds 2
                continue
            }

            Start-Sleep -Milliseconds 700

            $pullResult = Invoke-AdbWithTimeout `
                -CommandArgs @("pull",$remote,$local) `
                -TimeoutSec $UiDumpTimeoutSec

            if ($pullResult.TimedOut) {
                Log "UI pull attempt $i/$Attempts timed out after $UiDumpTimeoutSec seconds." "Yellow"
                Start-Sleep -Seconds 2
                continue
            }
            if ($pullResult.ExitCode -ne 0) {
                Log "UI pull attempt $i/$Attempts failed exit=$($pullResult.ExitCode) stderr=[$($pullResult.Error)]" "Yellow"
                Start-Sleep -Seconds 2
                continue
            }

            $dumpOut = [string]$dumpResult.Output
            $pullOut = [string]$pullResult.Output

        if ((Test-Path $local) -and (Get-Item $local).Length -gt 100) {
            Log "UI dump OK: $Name" "Green"
            return $local
        }

        Log "UI dump attempt $i/$Attempts failed. dump=[$dumpOut] pull=[$pullOut]" "Yellow"
        Start-Sleep -Seconds 2
    }

    return ""
}

function Load-Xml([string]$Path) {
    if (-not $Path -or -not (Test-Path $Path)) { return $null }

    try {
        $doc = New-Object System.Xml.XmlDocument
        $doc.PreserveWhitespace = $false
        $doc.Load($Path)
        return $doc
    } catch {
        Log "XML parse failed: $($_.Exception.Message)" "Yellow"
        return $null
    }
}

function Find-DescPrefix($Doc,[string]$Prefix) {
    if ($null -eq $Doc) { return $null }

    foreach ($n in $Doc.SelectNodes("//node")) {
        $d = $n.GetAttribute("content-desc")
        if ($d -and $d.StartsWith($Prefix,[System.StringComparison]::OrdinalIgnoreCase)) {
            return $n
        }
    }
    return $null
}

function Bounds-Center($Node) {
    if ($null -eq $Node) { return $null }

    $b = $Node.GetAttribute("bounds")
    if ($b -match "\[(\d+),(\d+)\]\[(\d+),(\d+)\]") {
        return @{
            X = [int](([int]$matches[1] + [int]$matches[3]) / 2)
            Y = [int](([int]$matches[2] + [int]$matches[4]) / 2)
        }
    }

    return $null
}

function Short-Touch([int]$X,[int]$Y) {
    $null = Adb @("shell","input","touchscreen","swipe","$X","$Y","$X","$Y","150")
    Start-Sleep -Seconds 3
}

function Click-Node($Node) {
    $c = Bounds-Center $Node
    if ($null -eq $c) { throw "NODE_BOUNDS_MISSING" }

    Log "short-touch: $($c.X),$($c.Y)"
    Short-Touch $c.X $c.Y
}

function Dismiss-StartupOverlay($Doc) {
    if ($null -eq $Doc) { return $false }

    foreach ($n in $Doc.SelectNodes("//node")) {
        if ($n.GetAttribute("content-desc") -eq "Dismiss") {
            Log "Startup overlay detected; sending Android Back." "Yellow"
            $null = Adb @("shell","input","keyevent","4")
            Start-Sleep -Seconds 2
            return $true
        }
    }

    return $false
}

function Try-Screenshot([string]$Name) {
    $remote = "/sdcard/$Name.png"
    $local = Join-Path $Root "$Name.png"

    try {
        $null = Adb @("shell","rm","-f",$remote)
        $null = Adb @("shell","screencap","-p",$remote)
        Start-Sleep -Milliseconds 500
        $null = Adb @("pull",$remote,$local)

        if ((Test-Path $local) -and (Get-Item $local).Length -gt 1000) {
            return $local
        }
    } catch {}

    if (Test-Path $local) {
        Remove-Item $local -Force -ErrorAction SilentlyContinue
    }

    return ""
}

function Parse-RankingCards($Doc) {
    $items = @()
    if ($null -eq $Doc) { return @() }

    foreach ($n in $Doc.SelectNodes('//node[@class="android.view.View"]')) {
        $desc = $n.GetAttribute("content-desc")
        if ([string]::IsNullOrWhiteSpace($desc)) { continue }

        # XML DOM already decodes &#10; into actual line breaks.
        # Normalize CRLF/CR/LF, trim, and remove blank lines.
        $normalized = $desc -replace "`r`n","`n"
        $normalized = $normalized -replace "`r","`n"

        $lines = @(
            $normalized.Split([char]10) |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )

        if ($lines.Count -lt 4) { continue }

        $rank = 0
        if (-not [int]::TryParse($lines[0],[ref]$rank)) { continue }
        if ($rank -lt 1 -or $rank -gt 10) { continue }

        $title = $lines[1]
        $heat = $lines[2]

        if ([string]::IsNullOrWhiteSpace($title)) { continue }
        if ($heat -notmatch '^\d+(?:\.\d+)?[KMB]$') { continue }

        $synopsis = $lines[$lines.Count - 1]

        $middle = @()
        if ($lines.Count -gt 4) {
            $middle = @($lines[3..($lines.Count - 2)])
        }

        $badges = @()
        $tags = @()

        foreach ($x in $middle) {
            if ($x -match '^(?i:Up by\s+\d+|Down by\s+\d+|NEW|HOT)$') {
                $badges += $x
            } else {
                $tags += $x
            }
        }

        $items += [pscustomobject]@{
            rank = $rank
            title = $title
            heat = $heat
            badges = @($badges)
            tags = @($tags)
            synopsis = $synopsis
        }
    }

    return @($items)
}

# 1) Clean launch.
Log "Force-stopping MoboReels..."
$null = Adb @("shell","am","force-stop",$Package)
Start-Sleep -Milliseconds 800

Log "Launching MoboReels..."
$launch = Adb @("shell","monkey","-p",$Package,"-c","android.intent.category.LAUNCHER","1")
Add-Content -Encoding UTF8 -Path $Log -Value ($launch -join "`n")
Start-Sleep -Seconds 7
$null = Assert-App-Focus

# 2) Read startup state.
$startXml = Try-Dump-Ui "moboreels_start_$Stamp" 4
if (-not $startXml) { throw "START_UI_DUMP_FAILED" }

$doc = Load-Xml $startXml
if ($null -eq $doc) { throw "START_XML_PARSE_FAILED" }

if (Dismiss-StartupOverlay $doc) {
    $startXml = Try-Dump-Ui "moboreels_after_dismiss_$Stamp" 4
    if (-not $startXml) { throw "POST_DISMISS_UI_DUMP_FAILED" }

    $doc = Load-Xml $startXml
    if ($null -eq $doc) { throw "POST_DISMISS_XML_PARSE_FAILED" }
}

# 3) Enter Charts.
$charts = Find-DescPrefix $doc "Charts"
if ($null -eq $charts) { throw "CHARTS_NODE_NOT_FOUND" }

if ($charts.GetAttribute("selected") -ne "true") {
    Log "Entering Charts..."
    Click-Node $charts

    $chartsXml = Try-Dump-Ui "moboreels_charts_$Stamp" 4
    if (-not $chartsXml) { throw "CHARTS_UI_DUMP_FAILED" }

    $doc = Load-Xml $chartsXml
}

$charts = Find-DescPrefix $doc "Charts"
if ($null -eq $charts -or $charts.GetAttribute("selected") -ne "true") {
    throw "CHARTS_NOT_SELECTED"
}

# 4) Enter Trending Series.
$trending = Find-DescPrefix $doc "Trending Series"
if ($null -eq $trending) { throw "TRENDING_SERIES_NODE_NOT_FOUND" }

if ($trending.GetAttribute("selected") -ne "true") {
    Log "Entering Trending Series..."
    Click-Node $trending

    $trendingXml = Try-Dump-Ui "moboreels_trending_$Stamp" 4
    if (-not $trendingXml) { throw "TRENDING_UI_DUMP_FAILED" }

    $doc = Load-Xml $trendingXml
}

$trending = Find-DescPrefix $doc "Trending Series"
if ($null -eq $trending -or $trending.GetAttribute("selected") -ne "true") {
    throw "TRENDING_SERIES_NOT_SELECTED"
}

Log "Trending Series confirmed." "Green"
$null = Assert-App-Focus

# 5) Collect ranks 1-10.
$ByRank = @{}
$rankConflicts = @()
$xmlEvidence = @()
$page = 1
$staleRounds = 0

while ($page -le 8 -and $ByRank.Count -lt 10) {
    $pageXml = Try-Dump-Ui ("moboreels_page{0}_{1}" -f $page,$Stamp) 4
    if (-not $pageXml) {
        Log "Page $page XML unavailable; scrolling and retrying." "Yellow"
    } else {
        $xmlEvidence += $pageXml
        $null = Assert-App-Focus
        $pageDoc = Load-Xml $pageXml
        if ($null -eq $pageDoc) { throw "PAGE_XML_PARSE_FAILED:$page" }

        $pageCharts = Find-DescPrefix $pageDoc "Charts"
        $pageTrending = Find-DescPrefix $pageDoc "Trending Series"
        if (
            $null -eq $pageCharts -or
            $pageCharts.GetAttribute("selected") -ne "true" -or
            $null -eq $pageTrending -or
            $pageTrending.GetAttribute("selected") -ne "true"
        ) {
            throw "TARGET_SEMANTIC_LOST_PAGE:$page"
        }

        $cards = @(Parse-RankingCards $pageDoc)
        $before = $ByRank.Count

        foreach ($card in $cards) {
            $rankKey = [int]$card.rank
            if ($ByRank.ContainsKey($rankKey)) {
                $existing = $ByRank[$rankKey]
                if ((Title-Key $existing.title) -ne (Title-Key $card.title)) {
                    $rankConflicts += [pscustomobject]@{
                        rank = $rankKey
                        first_title = $existing.title
                        later_title = $card.title
                        page = $page
                    }
                    Log "Rank conflict #${rankKey}: [$($existing.title)] vs [$($card.title)]" "Red"
                }
            } else {
                $ByRank[$rankKey] = $card
                Log "Captured #$($card.rank): $($card.title)" "Green"
            }
        }

        if ($ByRank.Count -eq $before) {
            $staleRounds++
        } else {
            $staleRounds = 0
        }
    }

    if ($ByRank.Count -ge 10) { break }
    if ($staleRounds -ge 3) { break }

    Log "Scrolling ranking list..."
    $null = Adb @("shell","input","touchscreen","swipe","450","1350","450","650","500")
    Start-Sleep -Seconds 3
    $page++
}

$rows = @()
foreach ($r in 1..10) {
    if ($ByRank.ContainsKey($r)) {
        $rows += $ByRank[$r]
    }
}

# 6) Audit.
$presentRanks = @()
foreach ($row in $rows) {
    $rv = 0
    if ([int]::TryParse([string]$row.rank,[ref]$rv)) {
        $presentRanks += $rv
    }
}

$missing = @()
foreach ($expectedRank in 1..10) {
    if ($presentRanks -notcontains $expectedRank) {
        $missing += $expectedRank
    }
}

$dupRanks = @(
    $presentRanks |
    Group-Object |
    Where-Object { $_.Count -gt 1 } |
    ForEach-Object { [int]$_.Name }
)

$dupTitles = @(
    $rows |
    Where-Object { $_.title } |
    Group-Object title |
    Where-Object { $_.Count -gt 1 } |
    ForEach-Object { $_.Name }
)

$blankTitles = @(
    $rows | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.title) }
)

$complete = (
    $rows.Count -eq 10 -and
    $presentRanks.Count -eq 10 -and
    $missing.Count -eq 0 -and
    $dupRanks.Count -eq 0 -and
    $dupTitles.Count -eq 0 -and
    $blankTitles.Count -eq 0 -and
    $rankConflicts.Count -eq 0
)

# Evidence screenshot is optional.
$shot = Try-Screenshot "moboreels_top10_$Stamp"

$result = [ordered]@{
    platform = "MoboReels"
    source_type = "SHORT_DRAMA_APP"
    source_id = "shortapp_moboreels"
    target_key = "daily_top_all"
    top_n = 10
    category = "All"
    ranking_type = "Trending Series"
    collection_method = "APP_UI_XML_SCROLL"
    collector_version = $CollectorVersion
    collection_date = $Today
    collected_at = (Get-Date).ToString("s")
    adb_serial = $Serial
    batch_complete = $complete
    missing_ranks = @($missing)
    duplicate_ranks = @($dupRanks)
    duplicate_titles = @($dupTitles)
    rank_conflicts = @($rankConflicts)
    evidence = [ordered]@{
        originalSourceType = "SHORT_DRAMA_APP"
        semanticVerified = $true
        appFocusVerified = $true
        targetLabel = "Charts > Trending Series"
        screenshot = $shot
        ui_xml_pages = @($xmlEvidence)
    }
    rows = @($rows)
}

$jsonPath = Join-Path $Root "moboreels_top10_$Stamp.json"
$result | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $jsonPath

$csvPath = Join-Path $Root "moboreels_top10_$Stamp.csv"
$rows | ForEach-Object {
    [pscustomobject]@{
        rank = $_.rank
        title = $_.title
        heat = $_.heat
        badges = ($_.badges -join " | ")
        tags = ($_.tags -join " | ")
        synopsis = $_.synopsis
    }
} | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "MoboReels Collector V3 complete" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "Rows: $($rows.Count)"
Write-Host "Complete: $complete"
Write-Host "Missing ranks: $($missing -join ', ')"
Write-Host "JSON: $jsonPath"
Write-Host "CSV : $csvPath"
Write-Host "PNG : $shot"
Write-Host ""

if ($complete) {
    Write-Host "RESULT: PASS" -ForegroundColor Green
    exit 0
} else {
    Write-Host "RESULT: AUDIT_FAILED" -ForegroundColor Red
    exit 2
}