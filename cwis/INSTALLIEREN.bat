@echo off
chcp 65001 >nul
title CWIS Installation
echo ============================================
echo   CWIS - Crypto Whale Intelligence System
echo   Installation (nur Beobachtung, keine Trades)
echo ============================================
echo.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Recurse -File | Unblock-File"
echo [1/3] Bestandsaufnahme (nur lesen)...
powershell -NoProfile -ExecutionPolicy Bypass -File "windows\1_bestandsaufnahme.ps1"
echo.
echo [2/3] Installation in eigenem Ordner (.venv)...
powershell -NoProfile -ExecutionPolicy Bypass -File "windows\2_installieren.ps1"
if errorlevel 1 (
  echo.
  echo FEHLER: Installation fehlgeschlagen. Ist Python 3.11 oder neuer installiert?
  echo Download: https://www.python.org/downloads/  ^(Haken bei "Add python.exe to PATH" setzen^)
  pause
  exit /b 1
)
echo.
set /p AUTO="[3/3] CWIS automatisch bei jeder Windows-Anmeldung starten? (j/n): "
if /i "%AUTO%"=="j" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "windows\3_autostart_einrichten.ps1"
  powershell -NoProfile -Command "Start-ScheduledTask -TaskName \"CWIS Whale Monitor\""
  echo CWIS laeuft jetzt im Hintergrund.
) else (
  echo Manueller Start jederzeit mit: windows\start_cwis.bat
  start "" "windows\start_cwis.bat"
)
echo.
echo Dashboard oeffnen: http://127.0.0.1:8787  (erste Werte nach ca. 1 Minute)
timeout /t 20 >nul
start "" "http://127.0.0.1:8787"
pause
