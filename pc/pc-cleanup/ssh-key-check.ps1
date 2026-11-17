# ssh-key-check.ps1 -- read-only. Shows how Windows' SSH server reads administrators_authorized_keys
# (public keys only; nothing secret) so the NUC's restricted key can be fixed. Output: pc-cleanup\ssh-key-check.txt
$out = Join-Path $PSScriptRoot 'ssh-key-check.txt'
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
& {
  "--- lines (key text shortened):"
  Get-Content $ak | ForEach-Object { if ($_ -match '^(.*?)(ssh-\S+)\s+(\S{12})\S*\s*(.*)$') { "options=[$($Matches[1].Trim())] type=$($Matches[2]) key=$($Matches[3])... comment=$($Matches[4])" } else { "UNPARSED: $_" } }
  "--- fingerprints ssh-keygen can read:"
  & 'C:\Windows\System32\OpenSSH\ssh-keygen.exe' -l -f $ak 2>&1
  "--- file permissions:"
  icacls $ak
  "--- sshd config check:"
  & 'C:\Windows\System32\OpenSSH\sshd.exe' -T 2>&1 | Select-String -Pattern '^(authorizedkeysfile|pubkeyauthentication|strictmodes|loglevel)'
} *>&1 | Out-File $out -Encoding utf8
