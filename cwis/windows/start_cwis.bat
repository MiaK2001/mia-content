@echo off
REM CWIS manuell starten (Fenster offen lassen). Dashboard: http://127.0.0.1:8787
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m cwis
pause
