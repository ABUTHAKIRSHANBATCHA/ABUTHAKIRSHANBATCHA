@echo off
rem Makes Lumi start by itself each time you sign in to Windows.
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Lumi.bat"
> "%LINK%" echo @echo off
>> "%LINK%" echo start "Lumi server" /min "%~dp0server.bat"
echo Lumi will now start automatically when you sign in to Windows.
echo To undo this, run autostart-off.bat.
pause
