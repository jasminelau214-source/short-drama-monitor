@echo off
cd /d "%~dp0"
title Short Drama Collector Backend Sync V2
echo Short Drama Collector Backend Sync V2
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0collector_sync.ps1"
echo.
echo Press any key to close this window.
pause >nul
