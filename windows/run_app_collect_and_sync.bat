@echo off
cd /d "%~dp0"
title JSM App Collect + Sync (Integration)
echo JSM App Collect + Sync - Integration
echo Default backend: http://127.0.0.1:4173
echo Production writes require an explicit PowerShell -AllowProductionWrite flag.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_app_collect_and_sync.ps1"
exit /b %EXITCODE%
