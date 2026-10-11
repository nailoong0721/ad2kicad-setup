@echo off
setlocal
set "AD2KICAD_PAUSE=1"
if not "%~1"=="" set "AD2KICAD_PAUSE=0"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Setup-AD2KiCad.ps1" %*
set "AD2KICAD_EXIT=%ERRORLEVEL%"
if "%AD2KICAD_PAUSE%"=="1" pause
exit /b %AD2KICAD_EXIT%
