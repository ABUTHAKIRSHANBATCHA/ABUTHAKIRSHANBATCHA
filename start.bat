@echo off
rem Double-click to start the assistant on Windows.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto :fail
)
start "" http://127.0.0.1:8765
.venv\Scripts\python -m assistant
goto :eof
:fail
echo Setup failed. Install Python 3.10+ from python.org and try again.
pause
