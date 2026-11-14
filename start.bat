@echo off
rem Double-click to start the assistant on Windows. Runs setup the first time.
cd /d "%~dp0"
if not exist .venv ( call setup.bat )
if not exist config.json ( .venv\Scripts\python -m assistant.setup_wizard )
start "" http://127.0.0.1:8765
.venv\Scripts\python -m assistant
pause
