@echo off
setlocal
cd /d "%~dp0"

echo [SETUP.BAT] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    pause
    exit /b 1
)

echo [SETUP.BAT] Dispatching setup.py...
python setup.py
if %errorlevel% neq 0 (
    echo Error: Setup failed.
    pause
    exit /b %errorlevel%
)

echo [SETUP.BAT] Setup complete.
pause
