<#
pc_drive_health.ps1 - every 5 minutes, each drive's SMART counters, to find the click (T0759, 2026-11-21).

Why: Jason hears a click/shake "every 10 to 15 minutes" (11-15, 11-17). The PC has two Seagate ST2000DM008 hard
drives (G: and M:). pc_health_log.ps1 shows they are almost never busy (one-minute samples, 11-21: G: 0 busy
minutes, M: 1), so the click is probably NOT reading or writing. Seagate desktop drives park their heads when
idle, and every park adds 1 to the drive's Load/Unload counter. If that counter climbs every 10-15 minutes, the
click is head parking. This logs the counters so the answer is in a file and in Grafana, not a guess.

How to run:  powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_drive_health.ps1
Scheduled:   task "PC drive health", every 5 minutes, as SYSTEM (SMART needs admin; registered once by
             pc_drive_admin_setup.ps1). Without admin it writes a row saying so and stops.
Writes:      D:\pc-health\drives-YYYY-MM-DD.csv (one row per drive per run; files older than 30 days are deleted)
             C:\Program Files\windows_exporter\textfile_inputs\pc_drives.prom (Grafana: pc_drive_value)
Reads only:  Get-PhysicalDisk + Get-StorageReliabilityCounter (Windows' own SMART view). Changes no drive setting.

Variables: $dir (CSV folder), $prom (windows_exporter textfile folder), $now (row time), $rows (one per drive),
$lines (Prometheus lines), $d (one physical disk), $c (its reliability counters), $model (short model name).
OUTCOME 2026-11-21: written; non-admin run tested (writes the "needs admin" row, exit 0). The admin run waits for
pc_drive_admin_setup.ps1 (Jason).
#>
$dir = 'D:\pc-health'
$prom = 'C:\Program Files\windows_exporter\textfile_inputs'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$rows = @()
$lines = @('# HELP pc_drive_value SMART counters per drive from pc_drive_health.ps1', '# TYPE pc_drive_value gauge')

foreach ($d in Get-PhysicalDisk) {
    $c = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
    $model = ($d.FriendlyName -replace '[^0-9A-Za-z]+', '_').Trim('_')
    # Every row has the SAME columns: Export-Csv -Append keeps the first header and silently drops unknown ones,
    # so a first "needs admin" row would have cut every later counter (found in the 11-21 test run).
    # 2026-11-23: + serial and the counters' own DeviceId (counter_disk). With two identical Seagates the counter rows
    # swapped between disks from one run to the next (11-21..11-23 CSVs), so a row now says which drive it really is.
    $row = [ordered]@{ time = $now; disk = $d.DeviceId; serial = "$($d.SerialNumber)".Trim(); counter_disk = "$($c.DeviceId)";
                       model = $model; media = "$($d.MediaType)"; temp_c = '';
                       power_on_hours = ''; start_stop_cycles = ''; load_unload_cycles = ''; read_errors = '';
                       wear_pct = ''; note = '' }
    if ($null -eq $c) {
        $row.note = 'needs admin (run as SYSTEM via the task)'
    } else {
        $row.temp_c = $c.Temperature
        $row.power_on_hours = $c.PowerOnHours
        $row.start_stop_cycles = $c.StartStopCycleCount
        $row.load_unload_cycles = $c.LoadUnloadCycleCount
        $row.read_errors = $c.ReadErrorsTotal
        $row.wear_pct = $c.Wear
        foreach ($k in 'temp_c', 'power_on_hours', 'start_stop_cycles', 'load_unload_cycles', 'read_errors', 'wear_pct') {
            if ("$($row[$k])" -match '^[0-9.]+$') {
                $lines += "pc_drive_value{disk=`"$($d.DeviceId)`",model=`"$model`",counter=`"$k`"} $($row[$k])"
            }
        }
    }
    $rows += [pscustomobject]$row
}

$file = Join-Path $dir ("drives2-" + (Get-Date -Format 'yyyy-MM-dd') + '.csv')   # drives2: new columns (serial, counter_disk)
if (Test-Path $file) { $rows | Export-Csv $file -Append -NoTypeInformation -Force } else { $rows | Export-Csv $file -NoTypeInformation }
Get-ChildItem $dir -Filter 'drives*-*.csv' | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item -Force

if ($lines.Count -gt 2) {   # only when real counters were read, so Grafana never shows a half-empty set
    try {
        $tmp = Join-Path $prom 'pc_drives.prom.tmp'
        [IO.File]::WriteAllText($tmp, ($lines -join "`n") + "`n")
        Move-Item $tmp (Join-Path $prom 'pc_drives.prom') -Force -ErrorAction Stop
    } catch { }
}
