@rem start.bat
@echo off
rem Starts Callback and opens it in the browser.
rem Close this window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Callback is not set up yet. Double-click setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
