# NetShort Collector V6 - Integration hardened
# Fixes V3:
# 1) Renames $home -> $homeState to avoid collision with PowerShell's read-only $HOME variable.
# 2) Removes the fragile remote `test -s` check that falsely treated successful UI dumps as failures.
# 3) Pulls the XML immediately after `uiautomator dump` and validates the local file instead.
# 4) Keeps retry/wait logic, XML DOM parsing, and BlueStacks-compatible short-touch navigation.

param(
    [string]$AdbExe = "C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    [string]$OutputRoot = "D:\ShortDramaCollector",
    [string]$AdbSerial = $env:JSM_ADB_SERIAL
)

$ErrorActionPreference = "Stop"

$Package = "com.netshort.abroad"
$CollectorVersion = "netshort-v6-integration"

if (-not (Test-Path $AdbExe)) {
    throw "ADB_NOT_FOUND: $AdbExe"
}

$Today = Get-Date -Format "yyyy-MM-dd"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Root = Join-Path (Join-Path $OutputRoot $Today) "NetShort"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$Log = Join-Path $Root "collector_v6_$Stamp.log"

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

function Try-Dump-Ui([string]$Name,[int]$Attempts=4) {
    $remote = "/sdcard/$Name.xml"
    $local = Join-Path $Root "$Name.xml"

    for ($i=1; $i -le $Attempts; $i++) {
        try {
            if (Test-Path $local) {
                Remove-Item $local -Force -ErrorAction SilentlyContinue
            }

            $null = Adb @("shell","rm","-f",$remote)
            $dumpOut = (Adb @("shell","uiautomator","dump","--compressed",$remote)) -join "`n"

            Start-Sleep -Milliseconds 700

            # Pull directly. BlueStacks HD-Adb's remote `sh -c test -s ...`
            # check is unreliable in this environment even when the dump exists.
            $pullOut = (Adb @("pull",$remote,$local)) -join "`n"

            if ((Test-Path $local) -and (Get-Item $local).Length -gt 100) {
                Log "UI dump OK: $Name" "Green"
                return $local
            }

            Log "UI dump attempt $i/$Attempts failed. dump=[$dumpOut] pull=[$pullOut]" "Yellow"
        } catch {
            Log "UI dump attempt $i/$Attempts exception: $($_.Exception.Message)" "Yellow"
        }

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

function Find-NodeByAttr($Doc,[string]$Attr,[string]$Value) {
    if ($null -eq $Doc) { return $null }
    foreach ($n in $Doc.SelectNodes("//node")) {
        if ($n.GetAttribute($Attr) -eq $Value) { return $n }
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
    Start-Sleep -Seconds 2
}

function Click-Node($Node) {
    $c = Bounds-Center $Node
    if ($null -eq $c) { throw "NODE_BOUNDS_MISSING" }
    Log "short-touch: $($c.X),$($c.Y)"
    Short-Touch $c.X $c.Y
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
    if (Test-Path $local) { Remove-Item $local -Force -ErrorAction SilentlyContinue }
    return ""
}

function Wait-For-HomeUi([int]$MaxAttempts=15) {
    for ($i=1; $i -le $MaxAttempts; $i++) {
        $focus = Current-Focus
        Log "Home wait $i/$MaxAttempts; focus=$focus"

        $xml = Try-Dump-Ui ("netshort_wait_{0}_{1}" -f $i,$Stamp) 3
        if (-not $xml) {
            Log "No usable UI XML yet; retrying..." "Yellow"
            Start-Sleep -Seconds 2
            continue
        }

        $doc = Load-Xml $xml
        if ($null -eq $doc) {
            Start-Sleep -Seconds 2
            continue
        }

        $outer = $doc.OuterXml
        if ($outer -match "(?i)Do you like NetShort|rate us|Notice|use a real device") {
            Log "Blocking modal detected; sending Android Back." "Yellow"
            $null = Adb @("shell","input","keyevent","4")
            Start-Sleep -Seconds 2
            continue
        }

        $rankings = Find-NodeByAttr $doc "content-desc" "Rankings"
        if ($null -ne $rankings) {
            return @{Xml=$xml;Doc=$doc;Rankings=$rankings}
        }

        Log "NetShort UI loaded but Rankings not present yet." "Yellow"
        Start-Sleep -Seconds 2
    }

    return $null
}

# 1) Clean launch.
Log "Force-stopping NetShort..."
$null = Adb @("shell","am","force-stop",$Package)
Start-Sleep -Milliseconds 800

Log "Launching NetShort..."
$launch = Adb @("shell","monkey","-p",$Package,"-c","android.intent.category.LAUNCHER","1")
Add-Content -Encoding UTF8 -Path $Log -Value ($launch -join "`n")
Start-Sleep -Seconds 7
$null = Assert-App-Focus

# 2) Wait for home UI.
$homeState = Wait-For-HomeUi
if ($null -eq $homeState) {
    throw "RANKINGS_NODE_NOT_FOUND_AFTER_WAIT"
}

$doc = $homeState.Doc
$rankingsNode = $homeState.Rankings

# 3) Enter Rankings.
if ($rankingsNode.GetAttribute("selected") -eq "true") {
    Log "Rankings already selected." "Green"
} else {
    Log "Entering Rankings..."
    Click-Node $rankingsNode
}

# 4) Confirm Rankings.
$rankXml = ""
$rankDoc = $null
for ($i=1; $i -le 6; $i++) {
    $rankXml = Try-Dump-Ui ("netshort_rankings_confirm_{0}_{1}" -f $i,$Stamp) 3
    $rankDoc = Load-Xml $rankXml
    if ($null -ne $rankDoc) {
        $rn = Find-NodeByAttr $rankDoc "content-desc" "Rankings"
        if ($null -ne $rn -and $rn.GetAttribute("selected") -eq "true") { break }
    }
    Start-Sleep -Seconds 2
}

if ($null -eq $rankDoc) { throw "RANKINGS_CONFIRM_XML_FAILED" }

$rankingsNode2 = Find-NodeByAttr $rankDoc "content-desc" "Rankings"
if ($null -eq $rankingsNode2 -or $rankingsNode2.GetAttribute("selected") -ne "true") {
    throw "RANKINGS_NOT_SELECTED_AFTER_CLICK"
}

# 5) Confirm Top Trending.
$topTrendingNode = Find-NodeByAttr $rankDoc "text" "Top Trending"
if ($null -eq $topTrendingNode) {
    throw "TOP_TRENDING_NODE_NOT_FOUND"
}

if ($topTrendingNode.GetAttribute("selected") -ne "true") {
    Log "Selecting Top Trending..."
    Click-Node $topTrendingNode
    Start-Sleep -Seconds 2

    $rankXml = Try-Dump-Ui "netshort_toptrending_$Stamp" 4
    $rankDoc = Load-Xml $rankXml
    $topTrendingNode = Find-NodeByAttr $rankDoc "text" "Top Trending"
}

if ($null -eq $topTrendingNode -or $topTrendingNode.GetAttribute("selected") -ne "true") {
    throw "TOP_TRENDING_NOT_SELECTED"
}

Log "Top Trending confirmed." "Green"
$null = Assert-App-Focus

# 6) Evidence.
$finalXml = Join-Path $Root "netshort_top10_$Stamp.xml"
Copy-Item -LiteralPath $rankXml -Destination $finalXml -Force
$shot = Try-Screenshot "netshort_top10_$Stamp"

if ($shot) {
    Log "Screenshot saved: $shot" "Green"
} else {
    Log "Screenshot unavailable; XML collection continues." "Yellow"
}
Log "Final XML: $finalXml"

# 7) Parse Top10.
$doc = Load-Xml $finalXml
$containers = @($doc.SelectNodes('//node[@resource-id="com.netshort.abroad:id/clContainer"]'))
$rows = @()

foreach ($container in $containers) {
    $rankNode = $container.SelectSingleNode('.//node[@resource-id="com.netshort.abroad:id/tv_rank_position"]')
    $titleNode = $container.SelectSingleNode('.//node[@resource-id="com.netshort.abroad:id/title"]')
    $scoreNode = $container.SelectSingleNode('.//node[@resource-id="com.netshort.abroad:id/ranking_score"]')
    $followersNode = $container.SelectSingleNode('.//node[@resource-id="com.netshort.abroad:id/tvRankLabel"]')

    if ($null -eq $rankNode -or $null -eq $titleNode) { continue }

    $rank = 0
    [void][int]::TryParse($rankNode.GetAttribute("text"), [ref]$rank)
    if ($rank -lt 1 -or $rank -gt 10) { continue }

    $tags = @()
    foreach ($t in $container.SelectNodes('.//node[@class="android.widget.TextView"]')) {
        $rid = $t.GetAttribute("resource-id")
        $text = $t.GetAttribute("text").Trim()
        if (-not $text) { continue }

        if ($rid -in @(
            "com.netshort.abroad:id/tv_rank_position",
            "com.netshort.abroad:id/title",
            "com.netshort.abroad:id/ranking_score",
            "com.netshort.abroad:id/tvRankLabel"
        )) { continue }

        if ($tags -notcontains $text) { $tags += $text }
    }

    $rows += [pscustomobject]@{
        rank = $rank
        title = $titleNode.GetAttribute("text")
        heat = if ($scoreNode) { $scoreNode.GetAttribute("text") } else { "" }
        followers = if ($followersNode) { $followersNode.GetAttribute("text") } else { "" }
        tags = @($tags)
    }
}

$rows = @($rows | Sort-Object rank)

# Robust audit: explicitly normalize rank values to integers.
$presentRanks = @()
foreach ($row in $rows) {
    $rv = 0
    if ([int]::TryParse([string]$row.rank, [ref]$rv)) {
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
    $blankTitles.Count -eq 0
)

$result = [ordered]@{
    platform = "NetShort"
    source_type = "SHORT_DRAMA_APP"
    source_id = "shortapp_netshort"
    target_key = "daily_top_all"
    top_n = 10
    category = "All"
    ranking_type = "Top Trending"
    collection_method = "APP_UI_XML"
    collector_version = $CollectorVersion
    collection_date = $Today
    collected_at = (Get-Date).ToString("s")
    adb_serial = $Serial
    batch_complete = $complete
    missing_ranks = @($missing)
    duplicate_ranks = @($dupRanks)
    duplicate_titles = @($dupTitles)
    rank_conflicts = @()
    evidence = [ordered]@{
        originalSourceType = "SHORT_DRAMA_APP"
        semanticVerified = $true
        appFocusVerified = $true
        targetLabel = "Rankings > Top Trending"
        screenshot = $shot
        ui_xml = $finalXml
    }
    rows = @($rows)
}

$jsonPath = Join-Path $Root "netshort_top10_$Stamp.json"
$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $jsonPath

$csvPath = Join-Path $Root "netshort_top10_$Stamp.csv"
$rows | ForEach-Object {
    [pscustomobject]@{
        rank = $_.rank
        title = $_.title
        heat = $_.heat
        followers = $_.followers
        tags = ($_.tags -join " | ")
    }
} | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "NetShort Collector V5 complete" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "Rows: $($rows.Count)"
Write-Host "Complete: $complete"
Write-Host "Missing ranks: $($missing -join ', ')"
Write-Host "JSON: $jsonPath"
Write-Host "CSV : $csvPath"
Write-Host "XML : $finalXml"
Write-Host "PNG : $shot"
Write-Host ""

if ($complete) {
    Write-Host "RESULT: PASS" -ForegroundColor Green
    exit 0
} else {
    Write-Host "RESULT: AUDIT_FAILED" -ForegroundColor Red
    exit 2
}