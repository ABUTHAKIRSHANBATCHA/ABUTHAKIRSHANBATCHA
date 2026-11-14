@echo off
rem Installs Ollama, downloads a model that fits this PC and makes it Lumi's brain.
rem Usage:  install-ollama.bat             picks a model for you
rem         install-ollama.bat llama3.1:8b uses the model you name
cd /d "%~dp0"
if not exist .venv ( echo Lumi is not installed yet. Run install.bat or setup.bat first. & pause & exit /b 1 )

rem Keep the large model files beside Lumi instead of on drive C: (only if not already chosen).
if not defined OLLAMA_MODELS (
  set "OLLAMA_MODELS=%~dp0ollama-models"
  setx OLLAMA_MODELS "%~dp0ollama-models" >nul
  tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul && (
    echo Ollama is already running. To store models in %~dp0ollama-models, quit Ollama from the
    echo tray icon near the clock now, then press a key. Otherwise just press a key.
    pause >nul
  )
)
.venv\Scripts\python -m assistant.ollama_setup %*
pause
