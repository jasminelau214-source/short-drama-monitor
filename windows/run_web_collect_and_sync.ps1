param(
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector",
    [string]$PythonCommand = "python",
    [string]$BaseUrl = "http://127.0.0.1:4173",
    [switch]$AllowProductionWrite
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $collector = Join-Path $repoRoot "run_official_web_collect.py"
    $sync = Join-Path $PSScriptRoot "collector_sync.ps1"

    if (-not (Test-Path $collector)) { throw "WEB_COLLECTOR_NOT_FOUND: $collector" }
    if (-not (Test-Path $sync)) { throw "SYNC_SCRIPT_NOT_FOUND: $sync" }

    Write-Host "Short Drama Official Web -> Collect + Sync" -ForegroundColor Cyan
    Write-Host "Date: $CollectionDate"
    Write-Host "Root: $Root"
    Write-Host ""

    $manifestDir = Join-Path $Root "manifests"
    New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
    $manifestPath = Join-Path $manifestDir ("web-{0}-{1}.json" -f $CollectionDate, (Get-Date -Format "yyyyMMdd_HHmmssfff"))

    Write-Host "Step 1/2  Collecting configured official-web targets..." -ForegroundColor Cyan
    & $PythonCommand $collector --date $CollectionDate --root $Root --manifest $manifestPath
    $collectExit = $LASTEXITCODE
    if ($collectExit -ne 0) {
        Write-Host "One or more web targets failed collection. Complete targets will still be synced." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Step 2/2  Syncing all complete platform + ranking targets..." -ForegroundColor Cyan
    if ($AllowProductionWrite.IsPresent) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $sync `
            -CollectionDate $CollectionDate -Root $Root -ManifestPath $manifestPath `
            -BaseUrl $BaseUrl -AllowProductionWrite
    }
    else {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $sync `
            -CollectionDate $CollectionDate -Root $Root -ManifestPath $manifestPath `
            -BaseUrl $BaseUrl
    }
    $syncExit = $LASTEXITCODE
    if ($syncExit -ne 0) { throw "COLLECTOR_SYNC_FAILED: exit=$syncExit" }

    Write-Host ""
    if ($collectExit -eq 0) {
        Write-Host "WEB COLLECTION + SYNC COMPLETE" -ForegroundColor Green
        exit 0
    }

    Write-Host "WEB SYNC COMPLETE WITH COLLECTION FAILURES" -ForegroundColor Yellow
    Write-Host "Successful targets were preserved and synced. Failed targets can be retried independently." -ForegroundColor Yellow
    exit 2
}
catch {
    Write-Host ""
    Write-Host "WEB COLLECTION + SYNC FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
