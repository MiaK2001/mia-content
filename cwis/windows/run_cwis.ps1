# Dauerbetrieb mit automatischem Neustart nach Absturz/Netzausfall.
# Läuft mit niedriger Priorität (BelowNormal), damit der Trading-Bot Vorrang hat.
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
while ($true) {
  $p = Start-Process -FilePath $python -ArgumentList "-m", "cwis" -WorkingDirectory $root -NoNewWindow -PassThru -RedirectStandardError "logs\stderr.log"
  try { $p.PriorityClass = "BelowNormal" } catch {}
  $p.WaitForExit()
  $code = $p.ExitCode
  Add-Content "logs\supervisor.log" "$(Get-Date -Format s) CWIS beendet mit Code $code"
  # 2 = Konfigurationsfehler, 3 = Port belegt -> nicht endlos neu starten
  if ($code -eq 2 -or $code -eq 3) { Add-Content "logs\supervisor.log" "Abbruch: bitte Konfiguration/Port prüfen."; exit $code }
  Start-Sleep -Seconds 30
}
