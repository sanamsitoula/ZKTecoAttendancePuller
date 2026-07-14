@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ── ZKTeco Attendance Puller — database backup (Windows) ──────────────────
rem Reads connection details from db_config.json and writes a timestamped
rem plain-SQL dump into db_backup\.

set "CONFIG_FILE=db_config.json"
if not exist "%CONFIG_FILE%" (
    echo ERROR: %CONFIG_FILE% not found.
    echo Create it from the example: copy db_config.json.example db_config.json
    exit /b 1
)

if not exist "db_backup" mkdir "db_backup"

rem ── read host/port/dbname/user/password out of db_config.json via python ──
for /f "usebackq tokens=1* delims==" %%A in (`python -c "import json;c=json.load(open('db_config.json'));print('HOST='+str(c.get('host','localhost')));print('PORT='+str(c.get('port',5432)));print('DBNAME='+str(c.get('dbname','zkteco')));print('DBUSER='+str(c.get('user','postgres')));print('DBPASS='+str(c.get('password','')))"`) do (
    set "%%A=%%B"
)

rem ── build today's date as YYYYMMDD (locale-independent via PowerShell) ────
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%D"

set "BACKUP_FILE=db_backup\%DBNAME%_%TODAY%.sql"

echo Backing up database "%DBNAME%" on %HOST%:%PORT% to %BACKUP_FILE% ...

set "PGPASSWORD=%DBPASS%"
pg_dump -h %HOST% -p %PORT% -U %DBUSER% -d %DBNAME% -f "%BACKUP_FILE%"
set "PGPASSWORD="

if errorlevel 1 (
    echo.
    echo ERROR: pg_dump failed. Check that pg_dump is on PATH and credentials are correct.
    exit /b 1
)

echo.
echo Backup complete: %BACKUP_FILE%
