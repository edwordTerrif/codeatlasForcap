@echo off
rem CodeAtlas launcher for Windows: create a virtualenv, install packages, start the server, open the browser.
cd /d "%~dp0backend"
if not exist .venv (
  echo [1/3] Creating Python virtual environment...
  python -m venv .venv || (echo Please install Python 3.10+ first: https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo [2/3] Installing packages...
pip install -q -r requirements.txt
echo [3/3] Starting server at http://localhost:8000  (press Ctrl+C to stop)
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:8000"
python -m uvicorn app.main:app --port 8000
pause
