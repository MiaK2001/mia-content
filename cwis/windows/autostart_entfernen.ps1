$taskName = "CWIS Whale Monitor"
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -like "*-m cwis*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }   # beendet NUR CWIS-Prozesse
Write-Host "CWIS-Autostart entfernt und CWIS-Prozesse beendet."
