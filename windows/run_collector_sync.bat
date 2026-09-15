@echo off
cd /d "%~dp0"
title Short Drama Collector Backend Sync
echo Short Drama Collector Backend Sync
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0collector_sync.ps1"
