@echo off
rem LifeLab production entry: uvicorn on :8000, WITHOUT --reload.
rem Started by Task Scheduler "At logon"; safe to double-click too (no pause --
rem otherwise a supervised daemon would hang forever on the keypress prompt).
rem Log: %USERPROFILE%\.lifelab\server.log, rotated to .old above 5 MB.
rem
rem ASCII-only ON PURPOSE. cmd.exe parses this file under the OEM code page,
rem where UTF-8 Chinese comments get mis-decoded -- and a mis-decoded byte can
rem leak out of a `rem` line and be executed as a command. Keep it ASCII.
setlocal
set "SCRIPT_DIR=%~dp0"
set "BACKEND=%SCRIPT_DIR%.."
set "PY=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "LOGDIR=%USERPROFILE%\.lifelab"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\server.log"
if exist "%LOG%" for %%A in ("%LOG%") do if %%~zA GTR 5242880 move /y "%LOG%" "%LOG%.old" >nul

rem Single process (no --workers): one user, no CPU-bound paths, and the login
rem rate limiter needs a single authoritative counter.
cd /d "%BACKEND%"
echo [%DATE% %TIME%] uvicorn starting >> "%LOG%"
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] uvicorn exited with %ERRORLEVEL% >> "%LOG%"
