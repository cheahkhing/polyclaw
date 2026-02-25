@echo off
REM Activate the Python virtual environment

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Virtual environment activated successfully!
) else (
    echo Error: Virtual environment not found at .venv\Scripts\activate.bat
    echo Please create a virtual environment first using: python -m venv .venv
    exit /b 1
)
