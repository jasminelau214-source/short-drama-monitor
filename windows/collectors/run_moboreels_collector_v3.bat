@echo off
cd /d "%~dp0"
title MoboReels Collector V3 Integration
echo MoboReels Collector V3 Integration
echo.
echo Keep BlueStacks open. The collector fails closed on device, focus, semantic, rank-conflict or Top10 audit errors.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0moboreels_collector_v3.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
echo Collector exit code: %EXITCODE%
exit /b %EXITCODE%
