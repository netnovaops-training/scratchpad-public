<#
photos_to_nas.ps1 - copy every M:\Pictures file that has NO copy on the NAS into the NAS Pictures share,
then check each copy's SHA-256 against the one measured on 11-17. Deletes nothing.

Why: Jason 11-17: one copy of the photos, on the NAS (shared Pictures folder = the main version). The 11-17
check (D:\backups\photo-verify\report.csv) found 45,515 files (274.5 GB) that exist ONLY on M: (T0992).
How to run:   powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\photos_to_nas.ps1
              Safe to rerun: files already copied with the right size are checked, not copied again.
Writes:       P:\From-M-Pictures\<path under M:\Pictures>   (P: = \\192.168.1.68\Pictures)
Log:          D:\backups\photo-verify\to-nas.log (progress every 500 files), to-nas-result.csv (path, status)
Next step (separate, needs Jason's OK on the plan): dedupe on the NAS, then clear M:.
OUTCOME 2026-11-17: written and started; see the log.
RETIRED 2026-11-24: the library moved to the personal space (\\192.168.1.68\home\Photos\Library, P: now maps
there) and the PC fill-in is done by D:\scratchpad\tools\photos_syno\pc_fill.py. A 2026-11-24 03:12 run started by
mistake; this guard stops any rerun from writing into the old Pictures share.
#>
Write-Host 'photos_to_nas.ps1 is retired (2026-11-24): use D:\scratchpad\tools\photos_syno\pc_fill.py'; exit 1
$ErrorActionPreference = 'Continue'
$report = 'D:\backups\photo-verify\report.csv'
$dstRoot = '\\192.168.1.68\Pictures\From-M-Pictures'
$log = 'D:\backups\photo-verify\to-nas.log'
$out = 'D:\backups\photo-verify\to-nas-result.csv'
function Say($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Add-Content -Path $log -Encoding utf8 }

$rows = Import-Csv $report | Where-Object { $_.nas_copy -eq 'NO COPY' -and $_.m_path -notlike 'M:PicturesSynologyDrive*' }  # SynologyDrive = Drive placeholders, real files already on the NAS (11-17)
Say "start: $($rows.Count) files with no NAS copy"
"m_path,status" | Set-Content $out -Encoding utf8
$n = 0; $ok = 0; $bad = 0; $gone = 0
foreach ($r in $rows) {
    $n++
    $src = $r.m_path
    $rel = $src.Substring('M:\Pictures\'.Length)
    $dst = Join-Path $dstRoot $rel
    $status = ''
    if (-not (Test-Path -LiteralPath $src)) { $status = 'source missing'; $gone++ }
    else {
        try {
            $need = $true
            if (Test-Path -LiteralPath $dst) { $need = ((Get-Item -LiteralPath $dst).Length -ne [int64]$r.size) }
            if ($need) {
                New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
                Copy-Item -LiteralPath $src -Destination $dst -Force
            }
            $h = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower()
            if ($h -eq $r.sha256) { $status = 'ok'; $ok++ } else { $status = 'HASH MISMATCH'; $bad++ }
        } catch { $status = "ERROR $($_.Exception.Message -replace ',',';')"; $bad++ }
    }
    '"' + $src + '",' + $status | Add-Content -Path $out -Encoding utf8
    if ($n % 500 -eq 0) { Say "$n/$($rows.Count): ok $ok, bad $bad, source missing $gone" }
}
Say "done: $n files; ok $ok, bad $bad, source missing $gone"
