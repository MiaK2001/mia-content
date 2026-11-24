@echo off
REM Aktualisiert die Binance-Reserven-Wallets (offizielle Proof-of-Reserves-Liste). Andere Boersen bleiben erhalten.
cd /d "%~dp0.."
".venv\Scripts\python.exe" tools\import_cex_labels.py --binance-only
echo Bitte CWIS neu starten, damit die Labels geladen werden.
pause
