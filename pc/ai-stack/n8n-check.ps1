<#
    Why is n8n losing its data?

    n8n keeps its login, its workflows and its encryption key in ONE SQLite
    file inside the n8n_data volume. If that file isn't where n8n thinks it
    is, or it can't be written, you get exactly the symptoms worth checking
    here: the owner-account screen returns after every refresh, workflows
    don't save, and the key "doesn't take".

    This script only LOOKS. It changes nothing and deletes nothing.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green;  if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;    if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "  n8n persistence check" -ForegroundColor Cyan
Write-Host "  ---------------------" -ForegroundColor DarkGray
Write-Host "  Looks only. Nothing is changed or deleted." -ForegroundColor DarkGray

# --- 1. is it even running -----------------------------------------------
Head "1. Container"
$state = (& docker inspect -f "{{.State.Status}}" n8n 2>$null)
if (-not $state) { Bad "no container named n8n" "start it: docker compose up -d n8n"; Read-Host "  Press Enter to close"; exit 1 }
if ($state -eq "running") { Ok "running" } else { Bad "state is '$state'" "docker compose logs n8n" }

$started = (& docker inspect -f "{{.State.StartedAt}}" n8n 2>$null)
$restarts = (& docker inspect -f "{{.RestartCount}}" n8n 2>$null)
Ok "started $started" "restart count: $restarts"
if ([int]$restarts -gt 3) { Warn "it has restarted $restarts times" "a crash loop would explain data appearing to reset" }

# --- 2. the volume --------------------------------------------------------
Head "2. Where the data should live"
$mount = (& docker inspect -f '{{range .Mounts}}{{if eq .Destination "/home/node/.n8n"}}{{.Type}}|{{.Name}}{{.Source}}{{end}}{{end}}' n8n 2>$null)
if (-not $mount) {
    Bad "NOTHING is mounted at /home/node/.n8n" "this is the bug - all data lives inside the container and dies with it"
} else {
    $parts = $mount -split '\|'
    if ($parts[0] -eq "volume") { Ok "named volume mounted" $parts[1] }
    else { Warn "a bind mount, not a named volume" $parts[1] }
}

# --- 2b. WHERE DOES N8N ACTUALLY THINK ITS DATA GOES ----------------------
# The mount can be perfect and still be ignored. n8n uses $HOME/.n8n unless
# N8N_USER_FOLDER says otherwise -- so running the container as root sends it
# to /root/.n8n while the volume sits unused at /home/node/.n8n. That exact
# mismatch is what silently destroyed the database on every recreate.
Head "2b. Where n8n actually writes (not where you mounted)"
$home_ = (& docker exec n8n sh -c 'echo -n "$HOME"' 2>$null)
$uf    = (& docker exec n8n sh -c 'echo -n "$N8N_USER_FOLDER"' 2>$null)
$effective = if ($uf) { "$uf/.n8n" } else { "$home_/.n8n" }

Ok "HOME inside container" $(if($home_){$home_}else{"(unset)"})
if ($uf) { Ok "N8N_USER_FOLDER is set" $uf } else { Warn "N8N_USER_FOLDER not set" "n8n will fall back to HOME" }
Write-Host "         -> n8n will use: $effective" -ForegroundColor DarkGray

if ($effective -ne "/home/node/.n8n") {
    Bad "MISMATCH: the volume is at /home/node/.n8n but n8n uses $effective" `
        "everything written there dies when the container is recreated"
} else {
    Ok "this matches the mounted volume"
}

# Look for a stray database anywhere it shouldn't be.
$stray = (& docker exec n8n sh -c "ls -la /root/.n8n 2>/dev/null | head -20" 2>$null) -join "`n"
if ($stray -match "database\.sqlite") {
    Bad "found a database at /root/.n8n - this is the WRONG place" `
        "it is not on a volume, so it is lost on every recreate"
    Write-Host $stray -ForegroundColor DarkGray
}

# --- 3. the database file -------------------------------------------------
Head "3. The database file itself"
$ls = (& docker exec n8n ls -la /home/node/.n8n 2>&1) -join "`n"
if ($LASTEXITCODE -ne 0) {
    Bad "couldn't list /home/node/.n8n" $ls
} else {
    Write-Host $ls -ForegroundColor DarkGray
    if ($ls -match "database\.sqlite") { Ok "database.sqlite exists" }
    else { Bad "NO database.sqlite" "n8n has never successfully written its database" }

    # n8n 2.0 refuses a settings file that others can read.
    if ($ls -match "-rw-------.*config") { Ok "config permissions are 0600" "what n8n 2.0 requires" }
    elseif ($ls -match "config") { Warn "config is NOT 0600" "n8n 2.0 rejects a wider-than-0600 settings file" }
}

$size = (& docker exec n8n sh -c "stat -c %s /home/node/.n8n/database.sqlite 2>/dev/null || echo 0" 2>$null)
if ($size -and [int]$size -gt 0) { Ok "database is $([math]::Round([int]$size/1KB,1)) KB" }
else { Bad "database is missing or empty" "nothing is being saved" }

# --- 4. can it actually write there --------------------------------------
Head "4. Write test"
$w = (& docker exec n8n sh -c "touch /home/node/.n8n/.writetest 2>&1 && rm -f /home/node/.n8n/.writetest && echo WRITABLE" 2>&1) -join " "
if ($w -match "WRITABLE") { Ok "the folder is writable" }
else { Bad "CANNOT write to /home/node/.n8n" $w }

$whoami = (& docker exec n8n id 2>$null)
Ok "container user" $whoami

# --- 5. what n8n itself says ---------------------------------------------
Head "5. n8n's own complaints"
$logs = (& docker logs n8n --tail 200 2>&1) -join "`n"
$hits = $logs -split "`n" | Where-Object {
    $_ -match "(?i)(permission|EACCES|readonly|read-only|encryption|settings file|database|sqlite|migration|error|failed)"
} | Select-Object -Last 25

if ($hits) { $hits | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
else { Ok "nothing alarming in the last 200 lines" }

# --- 6. the encryption key -----------------------------------------------
Head "6. Encryption key"
# Compare .env's key against the one n8n actually has, WITHOUT printing either.
$envKey = ""
if (Test-Path ".env") {
    $m = Select-String -Path ".env" -Pattern '^N8N_ENCRYPTION_KEY=(.+)$' | Select-Object -First 1
    if ($m) { $envKey = $m.Matches[0].Groups[1].Value.Trim() }
}
if (-not $envKey) { Bad "N8N_ENCRYPTION_KEY is blank in .env" "run env-sync.cmd" }
else {
    Ok "key present in .env" "$($envKey.Length) characters"
    $inContainer = (& docker exec n8n sh -c 'echo -n "$N8N_ENCRYPTION_KEY"' 2>$null)
    if ($inContainer -eq $envKey) { Ok "the container has the same key" }
    else { Bad "container key DIFFERS from .env" "recreate it: docker compose up -d --force-recreate n8n" }
}

Head "What to do"
Write-Host @"
  If 'NO database.sqlite' or 'CANNOT write'  -> that is the whole problem.
  If the config file is not 0600            -> n8n 2.0 is rejecting it.
                                               N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true
                                               (already set in docker-compose.yml) fixes it
                                               on the next recreate.

  Locked out but the database IS fine? Reset the owner account without
  losing a single workflow:

      docker exec -it n8n n8n user-management:reset

  That clears the login only. Workflows and credentials stay.
"@ -ForegroundColor Gray

Write-Host ""
Read-Host "  Press Enter to close"
