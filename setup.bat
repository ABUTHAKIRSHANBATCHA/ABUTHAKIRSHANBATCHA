@echo off
rem First-time setup on Windows: installs what is needed, then asks a few questions.
cd /d "%~dp0"
where py >nul 2>nul || (echo Python is not installed. Get Python 3.10 or newer from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv ( py -3 -m venv .venv || goto :fail )
echo Installing packages ...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt || goto :fail
.venv\Scripts\python -m assistant.setup_wizard
pause
goto :eof
:fail
echo Setup failed. Check your internet connection and try again.
pause
