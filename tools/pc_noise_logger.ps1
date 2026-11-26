<#
pc_noise_logger.ps1 - every 5 seconds: every fan's RPM, CPU/GPU temperature and load, and the two hard drives'
activity, plus WHICH processes were doing I/O when a hard drive was busy (2026-11-23, T0759 follow-up).

Why: Jason, 11-23: "The noise is really bad when something spins up. I think it's a fan, but it may also be a
hdd". What was known before this:
  - Windows' disk-sleep timer is 20 min, but the drives' spin-up counters did not move in 3 days of SMART logs
    (pc_drive_health.ps1), so the platters are NOT spinning down and up.
  - One Seagate (G: or M:) parks its heads in bursts of about 3,500 times an hour (once a second) for hours
    (11-21 20-23 h, 11-22 13-18 h and 22-01 h, 11-23 08-11 h). Each park is a click. Something touches the drive
    about once a second in those hours, and the drive parks again at once.
  - No fan data at all: HWiNFO runs but shares nothing, and LibreHardwareMonitor was not running.
This log answers both: a fan ramp shows as RPM jumping with a load/temperature jump; a drive burst shows as
G:/M: transfers with the processes doing I/O at that moment.

How to run:  scheduled task "PC noise logger" (SYSTEM, at startup, restarts on failure), registered once by
             pc_noise_logger_setup.ps1 (Jason, admin). Motherboard fans need admin; as a user only the GPU fan shows.
Writes:      D:\pc-health\noise-YYYY-MM-DD.csv (one row per 5 s; files older than 30 days deleted)
             C:\Program Files\windows_exporter\textfile_inputs\pc_noise.prom (Grafana pc-noise dashboard:
             pc_noise_fan_rpm, pc_noise_temp_c, pc_noise_load_pct, pc_noise_disk_tps)
Reads only:  LibreHardwareMonitorLib (copied to C:\ProgramData\pc-noise\lhm by the setup), performance counters.
             Changes no fan curve, no drive setting.
Variables: $lib (LHM folder), $pc (LHM Computer), $disk (drive letter -> PerformanceCounter), $row (one CSV row).
#>
$ErrorActionPreference = 'Continue'
$lib = 'C:\ProgramData\pc-noise\lhm'
if (-not (Test-Path "$lib\LibreHardwareMonitorLib.dll")) {
    $lib = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter LibreHardwareMonitorLib.dll -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).DirectoryName
}
Add-Type -Path "$lib\LibreHardwareMonitorLib.dll"
$pc = New-Object LibreHardwareMonitor.Hardware.Computer
$pc.IsCpuEnabled = $true; $pc.IsGpuEnabled = $true; $pc.IsMotherboardEnabled = $true; $pc.IsControllerEnabled = $true
$pc.Open()

$dir = 'D:\pc-health'
$prom = 'C:\Program Files\windows_exporter\textfile_inputs'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# one PerformanceCounter per hard drive letter (instance names look like "0 G:")
$disk = @{}
$cat = New-Object System.Diagnostics.PerformanceCounterCategory('PhysicalDisk')
foreach ($letter in 'G:', 'M:') {
    $inst = $cat.GetInstanceNames() | Where-Object { $_ -match [regex]::Escape($letter) } | Select-Object -First 1
    if ($inst) { $disk[$letter] = New-Object System.Diagnostics.PerformanceCounter('PhysicalDisk', 'Disk Transfers/sec', $inst); [void]$disk[$letter].NextValue() }
}

function Get-Sensors {
    $out = New-Object System.Collections.Generic.List[object]
    foreach ($h in $pc.Hardware) {
        $h.Update()
        $all = @($h.Sensors) + @($h.SubHardware | ForEach-Object { $_.Update(); $_.Sensors })
        foreach ($s in $all) {
            if ($null -eq $s.Value) { continue }
            $t = "$($s.SensorType)"
            $keep = ($t -eq 'Fan') -or ($t -eq 'Control') -or
                    ($t -eq 'Temperature' -and $s.Name -match 'Tctl|Package|Core \(Tctl|GPU Core|GPU Hot') -or
                    ($t -eq 'Load' -and $s.Name -match '^CPU Total$|^GPU Core$')
            if ($keep) { $out.Add([pscustomobject]@{ hw = "$($h.HardwareType)"; type = $t; name = $s.Name; value = [math]::Round([double]$s.Value, 1) }) }
        }
    }
    $out
}

function Get-TopIO {
    # processes doing I/O right now (all disks; asked only when a hard drive is busy, so it points at the cause)
    Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin '_Total', 'Idle' -and $_.IODataBytesPersec -gt 0 } |
        Sort-Object IODataBytesPersec -Descending | Select-Object -First 4 |
        ForEach-Object { '{0}:{1}KB' -f $_.Name, [int]($_.IODataBytesPersec / 1KB) }
}

$header = '"time","fans_rpm","fan_control_pct","cpu_c","gpu_c","cpu_load","gpu_load","G_tps","M_tps","io_top"'
while ($true) {
    try {
        $now = Get-Date
        $sens = Get-Sensors
        $fans = ($sens | Where-Object type -eq 'Fan' | ForEach-Object { "$($_.name)=$([int]$_.value)" }) -join ';'
        $ctl = ($sens | Where-Object type -eq 'Control' | ForEach-Object { "$($_.name)=$([int]$_.value)" }) -join ';'
        $cpuC = ($sens | Where-Object { $_.type -eq 'Temperature' -and $_.hw -eq 'Cpu' } | Measure-Object value -Maximum).Maximum
        $gpuC = ($sens | Where-Object { $_.type -eq 'Temperature' -and $_.hw -like 'Gpu*' } | Measure-Object value -Maximum).Maximum
        $cpuL = ($sens | Where-Object { $_.type -eq 'Load' -and $_.hw -eq 'Cpu' } | Select-Object -First 1).value
        $gpuL = ($sens | Where-Object { $_.type -eq 'Load' -and $_.hw -like 'Gpu*' } | Select-Object -First 1).value
        $tps = @{}
        foreach ($k in $disk.Keys) { $tps[$k] = [math]::Round($disk[$k].NextValue(), 1) }
        $io = ''
        if (($tps['G:'] -gt 0) -or ($tps['M:'] -gt 0)) { $io = (Get-TopIO) -join ';' }
        $f = Join-Path $dir ('noise-{0:yyyy-MM-dd}.csv' -f $now)
        if (-not (Test-Path $f)) { Set-Content -Path $f -Value $header -Encoding UTF8 }
        Add-Content -Path $f -Encoding UTF8 -Value ('"{0:yyyy-MM-dd HH:mm:ss}","{1}","{2}","{3}","{4}","{5}","{6}","{7}","{8}","{9}"' -f
            $now, $fans, $ctl, $cpuC, $gpuC, $cpuL, $gpuL, $tps['G:'], $tps['M:'], $io)
        if (Test-Path $prom) {
            $lines = @('# HELP pc_noise_fan_rpm fan speed', '# TYPE pc_noise_fan_rpm gauge')
            $sens | Where-Object type -eq 'Fan' | ForEach-Object { $lines += 'pc_noise_fan_rpm{{fan="{0}"}} {1}' -f ($_.name -replace '"', ''), $_.value }
            $lines += 'pc_noise_temp_c{{part="cpu"}} {0}' -f [double]$cpuC
            $lines += 'pc_noise_temp_c{{part="gpu"}} {0}' -f [double]$gpuC
            $lines += 'pc_noise_load_pct{{part="cpu"}} {0}' -f [double]$cpuL
            $lines += 'pc_noise_load_pct{{part="gpu"}} {0}' -f [double]$gpuL
            foreach ($k in $tps.Keys) { $lines += 'pc_noise_disk_tps{{disk="{0}"}} {1}' -f $k.TrimEnd(':'), $tps[$k] }
            $tmp = Join-Path $prom 'pc_noise.prom.tmp'
            Set-Content -Path $tmp -Value $lines -Encoding ASCII
            Move-Item -Force $tmp (Join-Path $prom 'pc_noise.prom')   # whole-file swap: the exporter never reads half a file
        }
        if ($now.Minute -eq 0 -and $now.Second -lt 5) {
            Get-ChildItem $dir -Filter 'noise-*.csv' | Where-Object LastWriteTime -lt $now.AddDays(-30) | Remove-Item -Force
        }
    } catch {
        Add-Content -Path (Join-Path $dir 'noise-errors.log') -Value ("{0:s} {1}" -f (Get-Date), $_.Exception.Message)
    }
    Start-Sleep -Seconds 5
}
