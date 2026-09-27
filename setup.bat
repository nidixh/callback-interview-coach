@rem setup.bat
@echo off
rem Sets up Callback on Windows.
rem Double-click this file, or type setup.bat in a terminal opened in the project folder.
rem Nothing needs installing first: it fetches Python 3.11, Ollama, the libraries and the models, then opens the app.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 echo Setup stopped before the end. Read the message above, then run setup.bat again.
pause
