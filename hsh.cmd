@echo off
setlocal
if /I "%~1"=="evening" goto evening
if /I "%~1"=="nightly" goto evening
if /I "%~1"=="morning" goto morning
if not "%~1"=="" goto usage
herdr plugin action invoke aigora.lantern.open
exit /b %ERRORLEVEL%

:evening
herdr plugin action invoke aigora.lantern.evening
exit /b %ERRORLEVEL%

:morning
herdr plugin action invoke aigora.lantern.open
if errorlevel 1 exit /b %ERRORLEVEL%
herdr
exit /b %ERRORLEVEL%

:usage
echo usage: hsh [morning^|evening^|nightly] 1>&2
exit /b 2
