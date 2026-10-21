@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required. Install it from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend:app --host 127.0.0.1 --port 8000
pause
