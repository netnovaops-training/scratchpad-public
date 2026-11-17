<#
  sort-files.ps1 -- Jason's yes/no list, 2026-11-04. MOVES ONLY; nothing is deleted.
  Jason: "just put [it] into a folder, I'll manually sort it and delete it in the folder itself".

  Downloads -> Downloads\_To-delete, _Archive, _Keep, _Decide (by type; see Bucket below).
              Anything changed in the last 2 days stays where it is (may be in use).
  www       -> www\_To-delete-2026-11-04 : superseded notes, old resume/digest/handover files,
              empty folders, the old site in Jason\ (Garden backup + index.php; Jason\android is live)
              www\_archive\2026-11-04     : the Brandon audit files (audit called off)
              Kept on purpose: HANDOFF.md and CLAUDE.md (CLAUDE.md says read HANDOFF.md first),
              COMPLETED-WORK-SUMMARY.md, homelab-inventory.md, hardware-inventory, the firewall scripts.
  Every move is written to pc-cleanup\sort-files.log as "from -> to", so any of it can be put back.
#>
$ErrorActionPreference = 'Continue'
$log = 'C:\Jasons New Site\www\pc-cleanup\sort-files.log'
function Move-Logged($src, $destDir) {
  if (-not (Test-Path -LiteralPath $src)) { return }
  New-Item -ItemType Directory -Force $destDir | Out-Null
  $dest = Join-Path $destDir (Split-Path $src -Leaf)
  if (Test-Path -LiteralPath $dest) { $dest = "$dest.dup-$(Get-Random)" }
  try { Move-Item -LiteralPath $src -Destination $dest -ErrorAction Stop; Add-Content $log "$src -> $dest" -Encoding utf8 }
  catch { Add-Content $log "SKIPPED $src : $($_.Exception.Message)" -Encoding utf8 }
}
Add-Content $log "=== sort-files $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding utf8

# ---------------- Downloads
$dl = "$env:USERPROFILE\Downloads"
$B = @{ Trash = "$dl\_To-delete"; Archive = "$dl\_Archive"; Keep = "$dl\_Keep"; Decide = "$dl\_Decide" }
$named = @{   # the big ones from CLEANUP-REVIEW.md, decided by name
  'ChatWithRTX_Installer_9_24' = 'Trash'; 'ChatWithRTX_Installer_9_24.zip' = 'Trash'; 'MediCat.USB.v21.12.7z' = 'Archive'
  'OneDrive_1_1-26-2026.zip' = 'Trash'; 'OneDrive_1_1-26-2026' = 'Decide'; 'Website Backup-20260828T011005Z-1-001.zip' = 'Archive'
  'cinebench' = 'Trash'; 'Cinebench2024_win_x86_64.zip' = 'Trash'; 'AI mode' = 'Decide'; 'PI' = 'Decide'; 'le potato' = 'Decide'
  'City Skylines' = 'Decide'; 'Redshift' = 'Decide'; 'resource' = 'Decide'
}
function Bucket($item) {
  if ($named.ContainsKey($item.Name)) { return $named[$item.Name] }
  if ($item.PSIsContainer) { return 'Decide' }
  switch -Regex ($item.Extension.ToLower()) {
    '^\.(tmp|crdownload|part|partial)$'                  { return 'Trash' }
    '^\.(exe|msi|msix|appx|appxbundle)$'                  { return 'Trash' }    # installers: re-downloadable
    '^\.(iso|img|bz2|xz|vhd|vhdx)$'                       { return 'Archive' }  # install media / images
    '^\.(zip|7z|rar|tar|gz)$'                             { return 'Decide' }   # could be anything
    '^\.(pdf|docx?|xlsx?|pptx?|csv|txt|md|epub|mobi)$'    { return 'Keep' }
    '^\.(jpe?g|png|gif|webp|heic|svg|mp3|wav|flac|m4a|mp4|mov|mkv|avi)$' { return 'Keep' }
    default                                               { return 'Decide' }
  }
}
$cut = (Get-Date).AddDays(-2)
Get-ChildItem -LiteralPath $dl -Force | Where-Object { $_.Name -notmatch '^_(To-delete|Archive|Keep|Decide)$' -and $_.Name -ne 'desktop.ini' } |
  Where-Object { $_.LastWriteTime -lt $cut } | ForEach-Object { Move-Logged $_.FullName $B[(Bucket $_)] }

# ---------------- www
$w = 'C:\Jasons New Site\www'
$del = "$w\_To-delete-2026-11-04"; $arc = "$w\_archive\2026-11-04"
foreach ($f in 'messagejay-handover.md', 'previous-chat-digest.md', 'RESUME-2026-10-28.md', 'README-DEPLOY.md', 'index.php',
               'ai-stack\RESUME-HERE.md', 'ai-stack\documents\ai-stack-notes\RESUME-HERE.md',
               'ai-stack\documents\website-handoff\RESUME-2026-10-28.md',
               'Jason\Garden', 'Jason\index.php') {   # Jason\android STAYS: landing mounts it for /jason/android (APKs)
  Move-Logged (Join-Path $w $f) $del
}
foreach ($f in 'brandon-security-audit.md', 'Brandon - White-box Security Test Scope.pdf') { Move-Logged (Join-Path $w $f) $arc }
# empty top-level folders (no files anywhere inside)
Get-ChildItem -LiteralPath $w -Directory -Force | Where-Object { $_.Name -notmatch '^_' -and $_.Name -ne '.claude' } |
  Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Select-Object -First 1) } |
  ForEach-Object { Move-Logged $_.FullName "$del\empty-folders" }

# ---------------- summary
foreach ($d in $B.Values + $del + $arc) {
  if (Test-Path $d) {
    $m = Get-ChildItem -LiteralPath $d -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    "{0,-60} {1,6} files {2,8:N1} GB" -f $d, $m.Count, ($m.Sum / 1GB)
  }
}
"Every move is listed in $log"
