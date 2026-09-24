@echo off
cd /d "%~dp0"
title JSM App Device Acceptance
echo JSM App Device Acceptance
echo.
echo This validation collects NetShort V6 and MoboReels V3 locally.
echo It does NOT sync or write to production.
echo Keep BlueStacks open and do not touch the emulator until PASS or FAIL.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_app_device_acceptance.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
echo Exit code: %EXITCODE%
pause
exit /b %EXITCODE%
