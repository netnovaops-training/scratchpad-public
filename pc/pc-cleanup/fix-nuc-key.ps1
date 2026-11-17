# fix-nuc-key.ps1 -- run as administrator (2026-11-04). install-pc-maint.ps1 appended the NUC's restricted key onto
# the END of the existing Termius key's line (the file had no final newline), so sshd read one key with a long
# comment. This puts the NUC key on its own line. The Termius key is not changed. Backup: ...bak-fix
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
if (-not $isAdmin) { Write-Host 'Run this in an ADMIN terminal (right-click Start > Terminal (Admin)).' -ForegroundColor Red; return }
Copy-Item $ak "$ak.bak-fix" -Force
$t = [IO.File]::ReadAllText($ak)
$n = $t -replace 'supernova-termius(from=)', "supernova-termius`r`n`$1"
if ($n -eq $t) { 'already on separate lines; nothing to fix' } else { [IO.File]::WriteAllText($ak, $n.TrimEnd() + "`r`n", [Text.Encoding]::ASCII); 'fixed' }
"keys in the file now: " + @(Get-Content $ak | Where-Object { $_ -match 'ssh-' }).Count + " (expected 2: Termius + NUC)"
