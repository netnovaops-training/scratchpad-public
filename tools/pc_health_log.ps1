<#
pc_health_log.ps1 - one line a minute about the PC's fans, GPU, CPU and disks, kept 30 days, to find what
shakes and clicks every 10-15 minutes (Jason, 2026-11-17: "constant, every 10 to 15 minutes"; PC under a year old).

How to run:  powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_health_log.ps1
Scheduled:   Windows task "PC health log", every minute (user level, no admin).
Writes:      D:\pc-health\YYYY-MM-DD.csv (one file a day); files older than 30 days are deleted.
How to test: run it twice by hand; today's file gets two rows.

Columns: time; GPU fan %, temp C, power W (nvidia-smi, no admin needed); CPU %; for each physical disk its
transfers/sec and % idle (Windows counters, no admin). A hard drive waking up shows as a disk going from 100%
idle for 20+ minutes to busy. Fans: if LibreHardwareMonitor runs with Options > Remote Web Server on (port 8085,
this PC only), every fan's RPM is added as extra columns; without it those columns are simply missing.
OUTCOME 2026-11-17: written and test-run (two rows) before the shake was identified.
#>
$dir = 'D:\pc-health'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$row = [ordered]@{ time = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') }

$g = (& nvidia-smi --query-gpu=fan.speed,temperature.gpu,power.draw --format=csv,noheader,nounits 2>$null) -split ',\s*'
if ($g.Count -ge 3) { $row.gpu_fan_pct = $g[0]; $row.gpu_temp_c = $g[1]; $row.gpu_power_w = $g[2] }

try {
    $c = Get-Counter -Counter '\Processor(_Total)\% Processor Time', '\PhysicalDisk(*)\Disk Transfers/sec', '\PhysicalDisk(*)\% Idle Time' -ErrorAction Stop
    foreach ($s in $c.CounterSamples) {
        if ($s.InstanceName -eq '_total' -and $s.Path -match 'physicaldisk') { continue }
        $name = if ($s.Path -match 'processor') { 'cpu_pct' }
                elseif ($s.Path -match 'transfers') { 'disk_' + ($s.InstanceName -replace '[^0-9a-z]', '') + '_xfers' }
                else { 'disk_' + ($s.InstanceName -replace '[^0-9a-z]', '') + '_idle_pct' }
        $row[$name] = [math]::Round($s.CookedValue, 1)
    }
} catch { $row.counters = 'unavailable' }

try {
    $j = Invoke-RestMethod -Uri 'http://127.0.0.1:8085/data.json' -TimeoutSec 3
    function Walk($n, $path) {
        if ($n.Type -eq 'Fan' -or ($n.Text -match '^(Fan|CPU|System|Pump)' -and $n.Value -match 'RPM')) {
            $row['fan_' + (($path + '_' + $n.Text) -replace '[^0-9A-Za-z]+', '_').Trim('_')] = ($n.Value -replace '[^0-9.]', '')
        }
        foreach ($k in $n.Children) { Walk $k ($path + '_' + $n.Text) }
    }
    Walk $j ''
} catch { }   # LibreHardwareMonitor not running yet: fan columns are left out

# Grafana feed: the same numbers as Prometheus metrics in windows_exporter's textfile folder (scraped by the NUC's
# Prometheus, 90 days). Disk activity is already in windows_exporter (physical_disk), so only GPU and fans go here.
# Needs pc_health_admin_setup.ps1 run once (write permission + textfile collector); until then this part is skipped.
$prom = 'C:\Program Files\windows_exporter\textfile_inputs'
try {
    $lines = @('# HELP pc_health_value GPU and fan readings from pc_health_log.ps1', '# TYPE pc_health_value gauge')
    foreach ($k in $row.Keys) {
        if ($k -match '^(gpu_|fan_)' -and "$($row[$k])" -match '^[0-9.]+$') { $lines += "pc_health_value{sensor=`"$k`"} $($row[$k])" }
    }
    $tmp = Join-Path $prom 'pc_health.prom.tmp'
    [IO.File]::WriteAllText($tmp, ($lines -join "`n") + "`n")
    Move-Item $tmp (Join-Path $prom 'pc_health.prom') -Force -ErrorAction Stop
} catch { }

$file = Join-Path $dir ((Get-Date -Format 'yyyy-MM-dd') + '.csv')
$obj = [pscustomobject]$row
if (Test-Path $file) { $obj | Export-Csv $file -Append -NoTypeInformation -Force } else { $obj | Export-Csv $file -NoTypeInformation }
Get-ChildItem $dir -Filter '*.csv' | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item -Force
