@echo off
cd /d "%~dp0"

echo Checking for process on port 8097...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8097 ^| findstr LISTENING') do (
    echo Killing PID %%a on port 8097...
    taskkill /PID %%a /F >nul 2>&1
)

call .venv\Scripts\activate.bat
echo Starting ZKTeco Web UI on http://localhost:8097
python -m web.run_web --port 8097
pause
