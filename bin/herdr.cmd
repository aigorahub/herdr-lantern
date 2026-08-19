@echo off
rem Windows sibling of bin/herdr. Keeps the mutate gate in front of callers
rem that are not running under Git Bash.
rem
rem bin/herdr has no file extension. A native Windows process that resolves
rem `herdr` on PATH uses PATHEXT, skips the extensionless wrapper, and finds
rem the real herdr.exe further down the PATH. The confirmation gate would be
rem bypassed with no message. This file is what that process finds instead.
rem
rem Git Bash still picks the extensionless bin/herdr, because a POSIX shell
rem matches the exact name first. The two wrappers share one gate: this one
rem only forwards.
rem
rem Known limit, measured: cmd.exe expands the arguments it forwards while it
rem parses its own command line, before this file runs, so nothing here can
rem prevent it. A lone percent survives, and so does an ampersand inside a
rem quoted argument. A %NAME% that matches a defined environment variable is
rem replaced by its value. Agents running under Lantern reach the POSIX
rem wrapper through Git Bash, which does not have this behaviour.

setlocal

set "LANTERN_SH="
for %%I in (sh.exe) do if not defined LANTERN_SH set "LANTERN_SH=%%~$PATH:I"
if not defined LANTERN_SH if exist "%ProgramFiles%\Git\bin\sh.exe" set "LANTERN_SH=%ProgramFiles%\Git\bin\sh.exe"
if not defined LANTERN_SH if exist "%ProgramFiles(x86)%\Git\bin\sh.exe" set "LANTERN_SH=%ProgramFiles(x86)%\Git\bin\sh.exe"
if not defined LANTERN_SH if exist "%LOCALAPPDATA%\Programs\Git\bin\sh.exe" set "LANTERN_SH=%LOCALAPPDATA%\Programs\Git\bin\sh.exe"

if not defined LANTERN_SH (
    echo lantern: no sh.exe found. Install Git for Windows, or put its bin>&2
    echo lantern: directory ^(usually "%ProgramFiles%\Git\bin"^) on PATH.>&2
    echo lantern: refusing to run herdr without the confirmation gate.>&2
    exit /b 127
)

"%LANTERN_SH%" "%~dp0herdr" %*
exit /b %ERRORLEVEL%
