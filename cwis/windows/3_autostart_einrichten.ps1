# Richtet einen Windows-Task ein, der CWIS bei Anmeldung startet (unsichtbar, niedrige Priorität)
# und bei Fehlern automatisch neu startet. Benötigt KEINE Administratorrechte.
# Aufruf:  powershell -ExecutionPolicy Bypass -File windows\3_autostart_einrichten.ps1
$ErrorActionPreference = "Stop"
$taskName = "CWIS Whale Monitor"
$script = Join-Path $PSScriptRoot "run_cwis.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -Priority 7 `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
  -Description "CWIS – reines Beobachtungssystem, keine Trades" -Force | Out-Null
Write-Host "Task '$taskName' eingerichtet. Sofort starten: Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Green
Write-Host "Hinweis: Für Start ohne Anmeldung (nach Neustart) in der Aufgabenplanung 'Unabhängig von der Benutzeranmeldung ausführen' wählen."
