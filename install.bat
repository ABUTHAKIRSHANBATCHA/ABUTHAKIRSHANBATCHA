@echo off
rem Full install: copies Lumi to E:\Lumi (or the folder you give), installs it,
rem asks the setup questions and puts a shortcut on the Desktop.
rem Usage:  install.bat            installs to E:\Lumi
rem         install.bat D:\Lumi    installs somewhere else
setlocal
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=E:\Lumi"
set "DRIVE=%TARGET:~0,2%"

echo.
echo  Installing Lumi to %TARGET%
echo.
if not exist "%DRIVE%\" (
  echo  Drive %DRIVE% was not found. Plug it in, or run:  install.bat D:\Lumi
  pause & exit /b 1
)
where py >nul 2>nul || (
  echo  Python is not installed. Get Python 3.10 or newer from
  echo  https://www.python.org/downloads/  and tick "Add python.exe to PATH", then run this again.
  pause & exit /b 1
)

rem Copy the program. Your settings, keys and memory in an existing install are kept.
robocopy "%~dp0." "%TARGET%" /E /XD .git .venv __pycache__ .pytest_cache data /XF config.json secrets.env lumi-check.txt /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo  Copying the files failed. Check that you can write to %TARGET%.
  pause & exit /b 1
)

cd /d "%TARGET%"
call setup.bat
if not exist "%TARGET%\.venv\Scripts\python.exe" (
  echo  Installation did not finish. See the messages above.
  pause & exit /b 1
)

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
