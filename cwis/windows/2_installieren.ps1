# CWIS Installation – eigenes, isoliertes Python-Venv im CWIS-Ordner.
# Installiert NICHTS systemweit und berührt den Trading-Bot nicht.
# Aufruf:  powershell -ExecutionPolicy Bypass -File windows\2_installieren.ps1

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$py = $null
foreach ($c in @("py -3.12", "py -3.13", "py -3.11", "py -3", "python")) {
  try {
    $v = Invoke-Expression "$c -c `"import sys; print(sys.version_info >= (3, 11))`"" 2>$null
    if ($v -eq "True") { $py = $c; break }
  } catch {}
}
if (-not $py) { Write-Error "Python >= 3.11 nicht gefunden. Bitte von https://www.python.org installieren."; exit 1 }
Write-Host "Verwende Python: $py"

if (-not (Test-Path ".venv")) { Invoke-Expression "$py -m venv .venv" }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if (-not (Test-Path "config\config.toml")) {
  Copy-Item "config\config.example.toml" "config\config.toml"
  Write-Host "config\config.toml angelegt (bei Bedarf Port anpassen)."
}
Write-Host "`nTests:" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pytest -q
Write-Host "`nErreichbarkeit der Datenquellen:" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m cwis --check-sources
Write-Host "`nFertig. Start: windows\start_cwis.bat  |  Autostart: windows\3_autostart_einrichten.ps1" -ForegroundColor Green
