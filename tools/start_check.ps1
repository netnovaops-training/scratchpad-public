<#
start_check.ps1 - one PASS/FAIL check of everything a fresh session needs (Proton Pass on all three machines,
SSH, Gitea, GitHub, the NAS, the push schedule, Docker, the site). Prints names and counts only, never a secret.

How to run (PC):  powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\start_check.ps1
How to test:      every line should say PASS; a FAIL line says what to look at. Known FAILs on 2026-11-17:
                  Pi Tailscale (T0936) and the Pi SSD (T0942/T0964) until those items are done.
Reads only. Proton Pass is asked for the vault LIST (names and counts), never an item or a field.

Proton Pass on each machine (the working setup, 2026-11-17):
  PC   pass-cli 2.4.2 at %LOCALAPPDATA%\Programs\ProtonPass\pass-cli.exe. User env PROTON_PASS_KEY_PROVIDER=fs
       (set 11-17; without it pass-cli fails with "hmac check failed"). Interactive login; the PC is the only
       place that can manage tokens (PATs).
  NUC  ~/bin/pass (wrapper: sets fs + PATH, re-logs in via ~/pass-relogin.sh, cron every 10 min). Token
       ~/.secrets/secrets-page/worker.token, expiry in worker.token.expires. Never call bare pass-cli there.
  Pi   ~/bin/pass (same wrapper, added 11-17), ~/pass-relogin.sh on cron every 10 min, log
       ~/.secrets/pass-relogin.log, token expiry in ~/.secrets/secrets-page/worker.token.expires.
  Rules: always --field for one value; never `item view` without it; renew tokens before they expire.
OUTCOME 2026-11-17: written before a fresh /clear; run once, results in the handoff.
#>
# Tailscale first (Jason, 2026-11-17); the LAN address is only a fallback, and the row says which one was used.
# OUTCOME 2026-11-17: NUC over Tailscale PASS; Pi falls back to LAN until T0936 (Pi Tailscale logged out).
$NUC = 'server@100.64.0.11'
$SSHO = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8')
$PI = @('-p', '1143', 'jason@100.64.0.12'); $piVia = 'Tailscale'
ssh @SSHO @PI true 2>$null
if ($LASTEXITCODE -ne 0) { $PI = @('-p', '1143', 'jason@192.168.1.69'); $piVia = 'LAN fallback' }
$fails = 0
function Row($ok, $what, $detail) {
    $s = if ($ok) { 'PASS' } else { 'FAIL'; $script:fails++ }
    '{0}  {1,-34} {2}' -f $s, $what, $detail
}
function Remote($target, $script) {
    # Run a bash script on a remote host. Sent base64-encoded on the command line: Windows PowerShell 5 mangles
    # quotes in native arguments and adds a byte-order mark + CRLF to piped stdin (both seen 11-17, both broke it).
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r", "")))
    ssh @SSHO @target "echo $b64 | base64 -d | bash" 2>$null
}
function Days($date) { if ($date) { [int]((Get-Date $date) - (Get-Date)).TotalDays } else { -1 } }

# --- Proton Pass ---
$env:PROTON_PASS_KEY_PROVIDER = 'fs'
$n = (pass-cli vault list 2>$null | Select-String '\]: ').Count
Row ($n -gt 0) 'PC pass-cli (key provider fs)' "$n vaults; user env = $([Environment]::GetEnvironmentVariable('PROTON_PASS_KEY_PROVIDER','User'))"

$PASSCHK = 'echo "$(~/bin/pass vault list 2>/dev/null | grep -c "\]: ") $(cat ~/.secrets/secrets-page/worker.token.expires 2>/dev/null)"'
$nucOut = Remote @($NUC) $PASSCHK
$v, $exp = "$nucOut" -split ' '
Row ([int]$v -gt 0) 'NUC ~/bin/pass' "$v vaults; token expires $exp ($(Days $exp) days)"
Row ((Days $exp) -gt 14) 'NUC pass token not expiring soon' "renew under 14 days (PC: personal-access-token create)"

$piOut = Remote $PI $PASSCHK
$v, $exp = "$piOut" -split ' '
Row ([int]$v -gt 0) 'Pi ~/bin/pass' "$v vaults; token expires $exp ($(Days $exp) days); via $piVia"
Row ((Days $exp) -gt 14) 'Pi pass token not expiring soon' "renew under 14 days"

# --- Pi health (known open items) ---
$ts = Remote $PI 'tailscale status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get(chr(66)+\"ackendState\"))"'
Row ("$ts" -eq 'Running') 'Pi Tailscale (T0936)' "state: $ts"
$mp = Remote $PI 'mountpoint -q /mnt/downloads && echo yes || echo no'
Row ("$mp" -eq 'yes') 'Pi drive mounted at /mnt/downloads' "mounted: $mp (T0942/T0964)"

# --- Code hosting ---
$g = Remote @($NUC) 'git ls-remote http://127.0.0.1:3000/Jason/nuc-homelab.git HEAD 2>/dev/null | cut -c1-7'
Row ([bool]"$g") 'Gitea (through the NUC)' "nuc-homelab HEAD $g"
gh auth status 2>$null | Out-Null
Row ($LASTEXITCODE -eq 0) 'GitHub CLI signed in' 'gh auth status'
$t = Get-ScheduledTask -TaskName 'Scratchpad daily GitHub push' -EA SilentlyContinue
$last = Get-Content D:\scratchpad\.git\daily-push.log -Tail 1 -EA SilentlyContinue
Row ($t -and $t.State -ne 'Disabled') 'Nightly GitHub push task' "state $($t.State); last: $last"

# --- Storage ---
Row (Test-Path '\\192.168.1.68\Website backup\unified\_manifest.csv') 'NAS unified copy' '\\192.168.1.68\Website backup\unified'
$bl = Remote @($NUC) 'test -d ~/backlog-page/feed && echo yes || echo no'
Row ("$bl" -eq 'yes') 'Backlog data folder (T0970)' "~/backlog-page/feed present: $bl"

# --- PC apps and the site ---
$lan = docker ps --filter name=^landing$ --format '{{.Status}}' 2>$null
Row ("$lan" -match 'healthy') 'PC Docker: landing (root page)' "$lan"
$code = curl.exe -s -o NUL -w '%{http_code}' https://example.org/todo/
Row ($code -in '200', '302', '303') 'Site /todo/ answers' "HTTP $code (303 = sign-in, expected)"

""
if ($fails) { "$fails FAIL line(s). Known open items are named in brackets." } else { "All PASS." }
