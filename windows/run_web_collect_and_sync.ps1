param(
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector",
    [string]$PythonCommand = "python"
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

    Write-Host "Step 1/2  Collecting configured official-web targets..." -ForegroundColor Cyan
    & $PythonCommand $collector --date $CollectionDate --root $Root
    if ($LASTEXITCODE -ne 0) { throw "OFFICIAL_WEB_COLLECTION_FAILED: exit=$LASTEXITCODE" }

    Write-Host ""
    Write-Host "Step 2/2  Syncing all complete platform + ranking targets..." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $sync -CollectionDate $CollectionDate -Root $Root
    if ($LASTEXITCODE -ne 0) { throw "COLLECTOR_SYNC_FAILED: exit=$LASTEXITCODE" }

    Write-Host ""
    Write-Host "WEB COLLECTION + SYNC COMPLETE" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "WEB COLLECTION + SYNC FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
