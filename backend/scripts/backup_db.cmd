@echo off
setlocal
rem Daily Postgres backup for LifeLab.
rem
rem ASCII-only ON PURPOSE: cmd.exe parses .cmd under the OEM codepage and would
rem mis-decode UTF-8 comments into stray commands. Keep this file ASCII.
rem
rem Custom format (-Fc): already compressed, and selectively restorable with
rem pg_restore. This is also the file we will restore onto the VPS next round,
rem which is why vectors never need re-embedding.
rem
rem pg_dump lives INSIDE the container (the host has none) and is the same major
rem version as the server (16.15) -> no version-mismatch warning.
rem
rem Backups contain real personal data -> they stay OUTSIDE the repo, under
rem %USERPROFILE%\.lifelab\backups. Only the newest %KEEP% are kept.
rem
rem Usage: backup_db.cmd [stamp]
rem   stamp  optional YYYYMMDD override, for testing retention without waiting
rem          14 days. Defaults to today's local date.

set "LIFELAB_HOME=%USERPROFILE%\.lifelab"
set "BACKUP_DIR=%LIFELAB_HOME%\backups"
set "LOG=%LIFELAB_HOME%\backup.log"
set "CONTAINER=lifelab-db"
set "DBNAME=lifelab"
set "DBUSER=lifelab"
set "KEEP=14"

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

rem %DATE% is locale-dependent (and its field order varies by region), so ask
rem PowerShell for an ISO stamp instead. One dump per day: a second run the same
rem day overwrites, which is what "daily backup" should mean.
set "STAMP=%~1"
if "%STAMP%"=="" for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "STAMP=%%D"
set "OUT=%BACKUP_DIR%\lifelab-%STAMP%.dump"
set "TMP=%OUT%.partial"

echo [%DATE% %TIME%] start %OUT%

rem Write to .partial, rename only after the dump is verified. A failed run must
rem never touch an already-good dump of the same day. (.partial is also outside
rem the lifelab-*.dump glob below, so it can never count as a backup.)
docker exec "%CONTAINER%" pg_dump -U %DBUSER% -d %DBNAME% -Fc > "%TMP%" 2>> "%LOG%"
if errorlevel 1 goto failed

rem A dump that "succeeded" but produced nothing is not a backup. Check size.
for %%A in ("%TMP%") do set "SIZE=%%~zA"
if "%SIZE%"=="0" goto failed

move /y "%TMP%" "%OUT%" >nul

rem Retention: names are lifelab-YYYYMMDD.dump, so plain reverse name order is
rem newest-first. Skip the newest %KEEP%, delete the rest.
for /f "skip=%KEEP% delims=" %%F in ('dir /b /o-n "%BACKUP_DIR%\lifelab-*.dump" 2^>nul') do (
  echo [%DATE% %TIME%] prune %%F >> "%LOG%"
  del "%BACKUP_DIR%\%%F"
)

echo [%DATE% %TIME%] ok %SIZE% bytes
>> "%LOG%" echo [%DATE% %TIME%] ok %OUT% %SIZE% bytes
exit /b 0

:failed
echo [%DATE% %TIME%] FAILED (see %LOG%)
>> "%LOG%" echo [%DATE% %TIME%] FAILED %OUT%
del "%TMP%" >nul 2>&1
exit /b 1
