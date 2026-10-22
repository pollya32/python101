@echo off
rem Runs the .ps1 file with the same name as this .bat file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1" %*
echo.
pause
