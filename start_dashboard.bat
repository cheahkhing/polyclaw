@echo off
REM ─────────────────────────────────────────────
REM  Polyclaw Dashboard Launcher
REM ─────────────────────────────────────────────

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found at .venv\
    echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting Polyclaw Dashboard...
echo.
.venv\Scripts\python.exe -m polyclaw dashboard
