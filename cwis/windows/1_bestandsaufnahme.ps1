# CWIS Phase 1 – Bestandsaufnahme (rein lesend, verändert NICHTS)
# Aufruf:  powershell -ExecutionPolicy Bypass -File windows\1_bestandsaufnahme.ps1
# Ermittelt Python, Hardware, freie Ressourcen und belegte Ports, damit CWIS
# keinen Port und keine Ressourcen des bestehenden Trading-Bots beansprucht.

$ErrorActionPreference = "Continue"
$report = @()
function Add($k, $v) { $script:report += "{0,-28} {1}" -f $k, $v }

Add "Datum" (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
$os = Get-CimInstance Win32_OperatingSystem
Add "Betriebssystem" "$($os.Caption) $($os.Version)"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Add "CPU" "$($cpu.Name) ($($cpu.NumberOfLogicalProcessors) Threads)"
Add "CPU-Last aktuell" "$([math]::Round((Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average,1)) %"
Add "RAM gesamt / frei" ("{0:N1} GB / {1:N1} GB" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB))
$drive = Get-PSDrive -Name (Split-Path -Qualifier $PSScriptRoot).TrimEnd(':')
Add "Freier Speicher (Laufwerk)" ("{0:N1} GB" -f ($drive.Free/1GB))

foreach ($cmd in @("py -3 --version", "python --version")) {
  try { $v = Invoke-Expression $cmd 2>&1; Add "Python ($cmd)" $v } catch { Add "Python ($cmd)" "nicht gefunden" }
}
foreach ($tool in @("git", "code")) {
  $c = Get-Command $tool -ErrorAction SilentlyContinue
  Add "Werkzeug $tool" ($(if ($c) { $c.Source } else { "nicht gefunden" }))
}

Add "" ""
Add "Belegte TCP-Ports (LISTEN)" ""
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Sort-Object LocalPort -Unique |
  ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    Add ("  Port " + $_.LocalPort) ("{0} (PID {1}) an {2}" -f $p.ProcessName, $_.OwningProcess, $_.LocalAddress)
  }

$port = 8787
$busy = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
Add "" ""
Add "CWIS-Standardport $port" ($(if ($busy) { "BELEGT – bitte in config\config.toml einen anderen Port wählen" } else { "frei" }))

Add "" ""
Add "Laufende Python-Prozesse" "(nur Anzeige – werden nicht verändert)"
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue | ForEach-Object {
  Add ("  PID " + $_.ProcessId) ($_.CommandLine)
}

$out = Join-Path $PSScriptRoot "..\logs\bestandsaufnahme.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$report | Tee-Object -FilePath $out
Write-Host "`nBericht gespeichert: $out" -ForegroundColor Green
