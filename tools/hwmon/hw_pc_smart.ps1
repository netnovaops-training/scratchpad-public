<#
hw_pc_smart.ps1 - every drive's SMART health into Grafana and a CSV log, once a minute (2026-11-24).

Why: Jason, 11-24: "PC fan noise is louder; I need to monitor all the fans and the HDDs and SSDs now, and view
logs." He expects a hardware failure within a month. Fans, CPU/GPU temperatures and loads already come from
pc_noise_logger.ps1 (LibreHardwareMonitor, every 5 s, pc_noise.prom). Windows' own SMART view
(pc_drive_health.ps1, Get-StorageReliabilityCounter) has NO reallocated / pending / uncorrectable sectors and no
NVMe media errors, which are the numbers that say a drive is dying. smartctl has them.

How to run:  scheduled task "PC hardware SMART" (SYSTEM, every minute), registered once by hwmon_pc_admin_setup.ps1
             (Jason, admin). By hand (admin PowerShell): powershell -NoProfile -ExecutionPolicy Bypass -File <this>
             Test without admin or smartctl: -Fixture <folder of smartctl JSON files named pd<N>.json>.
Writes:      C:\Program Files\windows_exporter\textfile_inputs\hw_smart.prom   metric hw_smart{machine,drive,serial,
             model,kind,field}  (same names as the NUC's nuc_smart_prom.py, so one dashboard shows both)
             D:\pc-health\smart-YYYY-MM-DD.csv   one row per drive per real read (30 days kept)
             C:\ProgramData\hwmon\last.json      last good read per drive (re-used between HDD reads)
             D:\pc-health\smart-errors.log       failures only
Reads only:  smartctl -H -A -i (no self-test, no setting changed). HARD DRIVES ARE READ ONLY EVERY 10 MINUTES and
             with -n standby: the click Jason hears is head parking (load_unload climbs ~34/hour since 11-23 17:00),
             and polling a parked Seagate every minute could add loads of its own. SSDs are read every minute.
Variables: $smartctl (exe), $state (drive key -> last fields), $rows (CSV rows), $lines (Prometheus lines),
           $d (one Get-PhysicalDisk entry), $j (smartctl JSON), $f (ordered field -> number).
OUTCOME 2026-11-24: written; tested with -Fixture (Seagate ATA + Samsung NVMe JSON). The real run waits for
hwmon_pc_admin_setup.ps1 (smartctl 7.5 install + SYSTEM task).
#>
param([string]$Fixture = '')
$ErrorActionPreference = 'Continue'
$smartctl = 'C:\Program Files\smartmontools\bin\smartctl.exe'
$dir = 'D:\pc-health'
$prom = 'C:\Program Files\windows_exporter\textfile_inputs'
$stateFile = 'C:\ProgramData\hwmon\last.json'
if ($Fixture) { $prom = $Fixture; $stateFile = Join-Path $Fixture 'last.json'; $dir = $Fixture }
New-Item -ItemType Directory -Force -Path $dir, (Split-Path $stateFile) | Out-Null
$errLog = Join-Path $dir 'smart-errors.log'
$now = Get-Date

function Write-Err($msg) { Add-Content -Path $errLog -Value ('{0:s} {1}' -f (Get-Date), $msg) }

if (-not $Fixture -and -not (Test-Path $smartctl)) { Write-Err 'smartctl missing: run hwmon_pc_admin_setup.ps1 as admin'; exit 1 }

# ATA attribute id -> field name. Seagate packs extra numbers into the raw value of 1, 7, 188 and 195, so those
# are left out on purpose (their raw numbers look alarming and mean nothing).
$ataMap = @{ 5 = 'reallocated'; 197 = 'pending'; 198 = 'uncorrectable'; 187 = 'reported_uncorrect';
             199 = 'crc_errors'; 193 = 'load_unload'; 4 = 'start_stop'; 10 = 'spin_retry'; 184 = 'end_to_end' }
$nvmeMap = [ordered]@{ media_errors = 'media_errors'; percentage_used = 'percentage_used'; available_spare = 'available_spare';
             critical_warning = 'critical_warning'; unsafe_shutdowns = 'unsafe_shutdowns';
             num_err_log_entries = 'err_log_entries'; data_units_written = 'data_units_written' }

function Get-Fields($j) {
    # smartctl JSON -> ordered field -> number. Missing sections give fewer fields, never a fake 0.
    $f = [ordered]@{}
    if ($null -ne $j.smart_status) { $f.passed = [int][bool]$j.smart_status.passed }
    if ($null -ne $j.temperature.current) { $f.temp_c = [double]$j.temperature.current }
    if ($null -ne $j.power_on_time.hours) { $f.power_on_hours = [double]$j.power_on_time.hours }
    foreach ($a in @($j.ata_smart_attributes.table)) {
        if ($null -ne $a -and $ataMap.ContainsKey([int]$a.id)) { $f[$ataMap[[int]$a.id]] = [double]$a.raw.value }
    }
    $n = $j.nvme_smart_health_information_log
    if ($null -ne $n) {
        foreach ($k in $nvmeMap.Keys) { if ($null -ne $n.$k) { $f[$nvmeMap[$k]] = [double]$n.$k } }
    }
    $f
}

function Read-Drive($num, $isNvme) {
    if ($Fixture) {
        $p = Join-Path $Fixture "pd$num.json"
        if (-not (Test-Path $p)) { return $null }
        return Get-Content $p -Raw | ConvertFrom-Json
    }
    $type = if ($isNvme) { 'nvme' } else { 'auto' }
    # -n standby: a sleeping drive is not woken (smartctl exits with bit 1 set and prints no attributes)
    $out = & $smartctl -j -H -A -i -n standby -d $type "/dev/pd$num" 2>$null
    if (-not $out) { return $null }
    ($out -join "`n") | ConvertFrom-Json
}

$state = @{}
if (Test-Path $stateFile) {
    try { (Get-Content $stateFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } } catch { }
}

# drive number -> letters (C:, D: ...), so the dashboard says "M:" and not "disk 1"
$letters = @{}
foreach ($p in Get-Partition -ErrorAction SilentlyContinue) {
    if ($p.DriveLetter -and [char]::IsLetter($p.DriveLetter)) {
        $letters[[int]$p.DiskNumber] = (@($letters[[int]$p.DiskNumber]) + "$($p.DriveLetter):" | Where-Object { $_ }) -join ','
    }
}

$lines = @('# HELP hw_smart SMART health per drive (hw_pc_smart.ps1 / nuc_smart_prom.py)', '# TYPE hw_smart gauge',
           '# HELP hw_smart_age_seconds seconds since this drive was last read', '# TYPE hw_smart_age_seconds gauge')
$rows = @()
foreach ($d in Get-PhysicalDisk | Sort-Object { [int]$_.DeviceId }) {
    $num = [int]$d.DeviceId
    $isNvme = "$($d.BusType)" -eq 'NVMe'
    $kind = if ($isNvme) { 'nvme' } elseif ("$($d.MediaType)" -eq 'HDD') { 'hdd' } else { 'ssd' }
    $serial = ("$($d.SerialNumber)".Trim()) -replace '[^0-9A-Za-z_.-]', ''
    $key = "$serial|$num"
    $model = "$($d.FriendlyName)" -replace '"', ''
    $drive = if ($letters[$num]) { $letters[$num] } else { "disk$num" }
    $prev = $state[$key]
    $due = ($kind -ne 'hdd') -or ($now.Minute % 10 -eq 0) -or ($null -eq $prev) -or $Fixture
    $fields = $null; $readAt = $null
    if ($due) {
        try {
            $j = Read-Drive $num $isNvme
            if ($null -ne $j) {
                $fields = Get-Fields $j
                if ($fields.Count -lt 2) { $fields = $null; Write-Err "pd$num ($drive): no SMART fields (asleep or unsupported)" }
            }
        } catch { Write-Err "pd$num ($drive): $($_.Exception.Message)" }
        if ($null -ne $fields) {
            $readAt = [int][double]::Parse((Get-Date -UFormat %s))
            $state[$key] = [pscustomobject]@{ at = $readAt; fields = [pscustomobject]$fields }
            $row = [ordered]@{ time = $now.ToString('yyyy-MM-dd HH:mm:ss'); drive = $drive; serial = $serial; model = $model; kind = $kind }
            foreach ($k in 'passed', 'temp_c', 'power_on_hours', 'reallocated', 'pending', 'uncorrectable', 'reported_uncorrect',
                           'crc_errors', 'load_unload', 'start_stop', 'media_errors', 'percentage_used', 'available_spare',
                           'critical_warning', 'unsafe_shutdowns') { $row[$k] = if ($fields.Contains($k)) { $fields[$k] } else { '' } }
            $rows += [pscustomobject]$row
        }
    }
    if ($null -eq $fields -and $null -ne $state[$key]) {   # between HDD reads (or a failed read): last good values
        $fields = [ordered]@{}; $state[$key].fields.PSObject.Properties | ForEach-Object { $fields[$_.Name] = $_.Value }
        $readAt = [int]$state[$key].at
    }
    if ($null -eq $fields) { continue }
    $lab = 'machine="supernova",drive="{0}",serial="{1}",model="{2}",kind="{3}"' -f $drive, $serial, $model, $kind
    foreach ($k in $fields.Keys) { $lines += 'hw_smart{{{0},field="{1}"}} {2}' -f $lab, $k, $fields[$k] }
    $lines += 'hw_smart_age_seconds{{{0}}} {1}' -f $lab, ([int][double]::Parse((Get-Date -UFormat %s)) - $readAt)
}

$state | ConvertTo-Json -Depth 5 | Set-Content -Path $stateFile -Encoding UTF8
try {
    $tmp = Join-Path $prom 'hw_smart.prom.tmp'
    [IO.File]::WriteAllText($tmp, ($lines -join "`n") + "`n")
    Move-Item $tmp (Join-Path $prom 'hw_smart.prom') -Force -ErrorAction Stop   # whole-file swap
} catch { Write-Err "prom write: $($_.Exception.Message)" }

if ($rows.Count) {
    $file = Join-Path $dir ('smart-{0:yyyy-MM-dd}.csv' -f $now)
    if (Test-Path $file) { $rows | Export-Csv $file -Append -NoTypeInformation -Force } else { $rows | Export-Csv $file -NoTypeInformation }
}
if ($now.Hour -eq 0 -and $now.Minute -eq 5) {
    Get-ChildItem $dir -Filter 'smart-*.csv' | Where-Object LastWriteTime -lt $now.AddDays(-30) | Remove-Item -Force
}
