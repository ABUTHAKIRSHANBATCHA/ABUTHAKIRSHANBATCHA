@echo off
rem Full install: installs what Lumi needs (Python, packages, and optionally Tailscale and
rem Ollama), copies Lumi to E:\Lumi (or the folder you give), asks the setup questions,
rem puts shortcuts on the Desktop and runs the installation check.
rem Usage:  install.bat            installs to E:\Lumi
rem         install.bat D:\Lumi    installs somewhere else
setlocal
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=E:\Lumi"
set "DRIVE=%TARGET:~0,2%"

echo.
echo  Installing Lumi to %TARGET%
echo.
if exist "%DRIVE%\" goto :drive_ok
echo  Drive %DRIVE% was not found. Plug it in, or run:  install.bat D:\Lumi
pause
exit /b 1
:drive_ok

rem ---- 1. Python
set "LUMI_PY="
where py >nul 2>nul && set "LUMI_PY=py -3"
if defined LUMI_PY goto :python_ok
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "LUMI_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined LUMI_PY goto :python_ok
echo  Python is not installed. Installing Python 3.12 ...
where winget >nul 2>nul || goto :no_winget
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "LUMI_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined LUMI_PY goto :python_ok
where py >nul 2>nul && set "LUMI_PY=py -3"
if defined LUMI_PY goto :python_ok
echo  Python was installed but this window cannot see it yet. Close this window and run install.bat again.
pause
exit /b 1
:no_winget
echo  I could not install Python automatically on this PC. Get Python 3.12 from
echo  https://www.python.org/downloads/  tick "Add python.exe to PATH", then run install.bat again.
pause
exit /b 1
:python_ok
echo  Python: %LUMI_PY%

rem ---- 2. Copy the program. Settings, keys and memory in an existing install are kept.
robocopy "%~dp0." "%TARGET%" /E /XD .git .venv __pycache__ .pytest_cache data ollama-models /XF config.json secrets.env lumi-check.txt /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :copy_failed
goto :copy_ok
:copy_failed
echo  Copying the files failed. Check that you can write to %TARGET%.
pause
exit /b 1
:copy_ok

rem ---- 3. Packages and setup questions
cd /d "%TARGET%"
call setup.bat nopause
if exist "%TARGET%\.venv\Scripts\python.exe" goto :venv_ok
echo  Installation did not finish. See the messages above.
pause
exit /b 1
:venv_ok

rem ---- 4. Optional extras
echo.
choice /c YN /m "  Install Tailscale, to reach Lumi from your phone anywhere (free)"
if errorlevel 2 goto :skip_tailscale
where winget >nul 2>nul && winget install -e --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements
echo  After it installs, open Tailscale and sign in. Install Tailscale on your phone with the same account.
:skip_tailscale
echo.
choice /c YN /m "  Install Ollama and a local AI model (free, private, several GB download)"
if errorlevel 2 goto :skip_ollama
call install-ollama.bat nopause
:skip_ollama

rem ---- 5. Desktop shortcuts
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); foreach($p in @(@('Lumi','start.bat'),@('Lumi check','check.bat'))){$s=$w.CreateShortcut((Join-Path $d ($p[0]+'.lnk'))); $s.TargetPath=(Join-Path '%TARGET%' $p[1]); $s.WorkingDirectory='%TARGET%'; $s.Save()}" >nul 2>nul

echo.
echo  Checking the installation ...
echo.
"%TARGET%\.venv\Scripts\python" -m assistant.check
echo.
echo  Lumi is installed in %TARGET%
echo  Start it with the "Lumi" shortcut on your Desktop, or %TARGET%\start.bat
echo.
pause
endlocal
