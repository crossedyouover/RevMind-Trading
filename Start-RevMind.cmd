@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo RevMind's Python environment is missing. Please ask for setup assistance.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.dashboard --open-browser
if errorlevel 1 (
  echo Could not start RevMind. If it is already running, open http://127.0.0.1:8765
  pause
)
