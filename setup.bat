@echo off
rem Installs Lumi's packages, then asks the setup questions. Run again any time to change an answer.
cd /d "%~dp0"
if not defined LUMI_PY where py >nul 2>nul && set "LUMI_PY=py -3"
if not defined LUMI_PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "LUMI_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined LUMI_PY goto :have_python
echo Python is not installed. Run install.bat, which installs it for you, or get Python 3.10 or newer
echo from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1
:have_python
if not exist .venv %LUMI_PY% -m venv .venv
if not exist .venv\Scripts\python.exe goto :fail
echo Installing packages (the first time this takes a few minutes) ...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail
.venv\Scripts\python -m assistant.setup_wizard
if not "%~1"=="nopause" pause
goto :eof
:fail
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
