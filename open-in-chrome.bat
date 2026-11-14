@echo off
rem Opens a page in Google Chrome when it is installed, otherwise in the default browser.
set "CHROME="
for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_SZ"') do set "CHROME=%%B"
if not defined CHROME for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_SZ"') do set "CHROME=%%B"
if defined CHROME ( start "" "%CHROME%" %1 ) else ( start "" %1 )
