@echo off
cd /d "%~dp0"
title NetShort Collector V6 Integration
echo NetShort Collector V6 Integration
echo.
echo Keep BlueStacks open. The collector fails closed on device, focus, semantic or Top10 audit errors.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0netshort_collector_v6.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
echo Collector exit code: %EXITCODE%
exit /b %EXITCODE%
