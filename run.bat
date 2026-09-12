@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [RUN.BAT] Virtual environment not found. Running setup.bat first...
    call setup.bat
)

if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if not "%%b"=="" set "%%a=%%b"
    )
)

echo ========================================================
echo Starting My Doctor Viewer Full-Stack Services
echo Backend:  http://localhost:8000 (API and Docs: /docs)
echo Frontend: http://localhost:5173
echo ========================================================

:: Start backend in a separate terminal window
start "Backend API Server" cmd /k "call .venv\Scripts\activate.bat && cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

:: Start frontend in this terminal window
cd frontend
call npm run dev

echo [RUN.BAT] Shutting down...
