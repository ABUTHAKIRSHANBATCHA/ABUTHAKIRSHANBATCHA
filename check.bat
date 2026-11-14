@echo off
rem Checks the installation and writes lumi-check.txt.
cd /d "%~dp0"
if not exist .venv ( echo Lumi is not installed yet. Run setup.bat first. & pause & exit /b 1 )
.venv\Scripts\python -m assistant.check
pause
