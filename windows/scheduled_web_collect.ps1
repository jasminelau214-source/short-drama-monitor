param(
    [string]$CollectionDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$Root = "D:\ShortDramaCollector",
    [string]$PythonCommand = "python",
    [string]$BaseUrl = "http://127.0.0.1:4173",
    [switch]$AllowProductionWrite,
    [int]$MaxAttempts = 3,
    [int]$RetryDelaySeconds = 300
)

$ErrorActionPreference = "Stop"
$mutex = $null
$lockTaken = $false
$logWriter = $null

function Write-RunLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    if ($logWriter) {
        $logWriter.WriteLine($line)
        $logWriter.Flush()
    }
}

function Load-AdminPassword {
    if ($env:SHORT_DRAMA_ADMIN_PASSWORD -and $env:SHORT_DRAMA_ADMIN_PASSWORD.Trim()) {
        return [string]$env:SHORT_DRAMA_ADMIN_PASSWORD
    }

    $credentialDir = Join-Path $env:LOCALAPPDATA "ShortDramaMonitor"
    $passwordFile = Join-Path $credentialDir "admin_password.txt"
    if (-not (Test-Path $passwordFile)) {
        throw "SCHEDULED_PASSWORD_NOT_CONFIGURED: run windows\install_web_collect_task.ps1 once"
    }

    $encrypted = Get-Content -Raw -Encoding UTF8 $passwordFile
    $secure = $encrypted | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

try {
    if ($MaxAttempts -lt 1) { $MaxAttempts = 1 }
    if ($RetryDelaySeconds -lt 0) { $RetryDelaySeconds = 0 }

    $logDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logPath = Join-Path $logDir ("web-collect-{0}-{1}.log" -f $CollectionDate, (Get-Date -Format "HHmmss"))
    $logWriter = New-Object System.IO.StreamWriter($logPath, $true, (New-Object System.Text.UTF8Encoding($false)))

    $mutex = New-Object System.Threading.Mutex($false, "Local\ShortDramaMonitorWebCollect")
    $lockTaken = $mutex.WaitOne(0)
    if (-not $lockTaken) {
        Write-RunLog "SKIP: another collection run is already active."
        exit 10
    }

    $runner = Join-Path $PSScriptRoot "run_web_collect_and_sync.ps1"
    if (-not (Test-Path $runner)) { throw "RUNNER_NOT_FOUND: $runner" }

    $plainPassword = Load-AdminPassword
    $env:SHORT_DRAMA_ADMIN_PASSWORD = $plainPassword
    $plainPassword = $null

    Write-RunLog "Scheduled collection started. date=$CollectionDate root=$Root maxAttempts=$MaxAttempts"

    $lastExit = 1
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-RunLog "Attempt $attempt/$MaxAttempts started."
        if ($AllowProductionWrite.IsPresent) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
                -CollectionDate $CollectionDate `
                -Root $Root `
                -PythonCommand $PythonCommand `
                -BaseUrl $BaseUrl `
                -AllowProductionWrite 2>&1 | ForEach-Object { Write-RunLog ([string]$_) }
        }
        else {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
                -CollectionDate $CollectionDate `
                -Root $Root `
                -PythonCommand $PythonCommand `
                -BaseUrl $BaseUrl 2>&1 | ForEach-Object { Write-RunLog ([string]$_) }
        }
        $lastExit = $LASTEXITCODE

        if ($lastExit -eq 0) {
            Write-RunLog "SUCCESS: all configured targets collected and synced."
            exit 0
        }

        if ($lastExit -eq 2) {
            Write-RunLog "PARTIAL: successful targets were synced; failed web targets remain for retry."
        }
        else {
            Write-RunLog "FAILURE: runner exit=$lastExit"
        }

        if ($attempt -lt $MaxAttempts) {
            Write-RunLog "Retrying after $RetryDelaySeconds seconds."
            Start-Sleep -Seconds $RetryDelaySeconds
        }
    }

    throw "SCHEDULED_COLLECTION_EXHAUSTED: finalExit=$lastExit attempts=$MaxAttempts"
}
catch {
    Write-RunLog ("FATAL: " + $_.Exception.Message)
    exit 1
}
finally {
    $env:SHORT_DRAMA_ADMIN_PASSWORD = $null
    if ($lockTaken -and $mutex) {
        try { $mutex.ReleaseMutex() } catch {}
    }
    if ($mutex) { $mutex.Dispose() }
    if ($logWriter) { $logWriter.Dispose() }
}
