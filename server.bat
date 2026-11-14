@echo off
rem Runs Lumi as a server on this PC without opening the browser.
cd /d "%~dp0"
if not exist .venv ( echo Lumi is not installed yet. Run setup.bat first. & pause & exit /b 1 )
title Lumi server
.venv\Scripts\python -m assistant
pause
