param(
    [string]$TaskName = "ShortDramaMonitor-WebCollect",
    [string]$DailyTime = "17:30",
    [string]$Root = "D:\ShortDramaCollector",
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

function Parse-DailyTime([string]$Value) {
    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($Value, 'HH:mm', $null, [Globalization.DateTimeStyles]::None, [ref]$parsed)) {
        throw "INVALID_DAILY_TIME: use HH:mm, e.g. 17:30"
    }
    return $parsed
}

try {
    $taskTime = Parse-DailyTime $DailyTime
    $runner = Join-Path $PSScriptRoot "scheduled_web_collect.ps1"
    if (-not (Test-Path $runner)) { throw "SCHEDULED_RUNNER_NOT_FOUND: $runner" }

    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Root "logs") -Force | Out-Null

    $credentialDir = Join-Path $env:LOCALAPPDATA "ShortDramaMonitor"
    New-Item -ItemType Directory -Path $credentialDir -Force | Out-Null
    $passwordFile = Join-Path $credentialDir "admin_password.txt"

    Write-Host "Configure unattended backend login." -ForegroundColor Cyan
    Write-Host "The password is encrypted with Windows DPAPI and can only be decrypted by this Windows user." -ForegroundColor DarkGray
    $securePassword = Read-Host "Backend admin password" -AsSecureString
    if (-not $securePassword -or $securePassword.Length -eq 0) { throw "ADMIN_PASSWORD_EMPTY" }
    $securePassword | ConvertFrom-SecureString | Set-Content -Encoding UTF8 $passwordFile

    $actionArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Root "{1}" -PythonCommand "{2}"' -f $runner, $Root, $PythonCommand
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
    $trigger = New-ScheduledTaskTrigger -Daily -At $taskTime
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Collect official-web short-drama rankings, audit them, and sync complete targets to the monitor." `
        -Force | Out-Null

    Write-Host ""
    Write-Host "TASK INSTALLED" -ForegroundColor Green
    Write-Host ("Task: " + $TaskName)
    Write-Host ("Daily time: " + $DailyTime)
    Write-Host ("Data root: " + $Root)
    Write-Host ("Logs: " + (Join-Path $Root "logs"))
    Write-Host ""
    Write-Host "The task runs under the current Windows user while that user is logged in." -ForegroundColor Yellow
    Write-Host "To test immediately: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
}
catch {
    Write-Host ""
    Write-Host "TASK INSTALL FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
