@echo off
setlocal
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
for /d %%P in ("%LocalAppData%\Programs\Python\Python*") do (
  if exist "%%~fP\python.exe" set "LANTERN_PYTHON=%%~fP\python.exe"
)
if defined LANTERN_PYTHON goto use_path
echo codex-headless: Python 3 command not found 1>&2
exit /b 127

:use_py
py -3 "%~dp0codex_headless.py" %*
exit /b %ERRORLEVEL%

:use_python
python "%~dp0codex_headless.py" %*
exit /b %ERRORLEVEL%

:use_path
"%LANTERN_PYTHON%" "%~dp0codex_headless.py" %*
exit /b %ERRORLEVEL%
