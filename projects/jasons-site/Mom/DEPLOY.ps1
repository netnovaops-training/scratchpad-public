<#
================================================================================
  Tech Help - deploy everything, verify it, then tidy up.

      .\DEPLOY.ps1                 do the whole deployment, then verify
      .\DEPLOY.ps1 -Verify         just re-check, change nothing
      .\DEPLOY.ps1 -Cleanup        show what tidying WOULD remove
      .\DEPLOY.ps1 -Cleanup -Yes   actually tidy up

  THE ORDER MATTERS

      1. Run it.  It sets up the desktop, pushes to the Pi, rebuilds, and
         verifies. It does NOT delete anything.
      2. Use the site. Sign in as her, ask something, get an answer.
      3. Only then run -Cleanup. Nothing is deleted until you say so, because
         "it deployed" and "it works" are different claims and only you can
         make the second one.

  WHAT CLEANUP ACTUALLY DOES

      Windows   moves superseded scripts into _archive\<date>\ -- MOVED, never
                deleted, so anything you miss is one drag away from coming back.
                Diagnostics go into tools\ rather than the archive: they are
                exactly what you will want at 2am when something breaks.
      Pi        removes the upload staging folder, old Caddyfile backups and
                superseded scripts.

      It will not touch .env, the knowledge folder, the database volume, or
      anything the running system needs. Those are on an explicit keep list and
      the script refuses to move a file that is not on one of its lists at all.
================================================================================
#>
[CmdletBinding()]
param(
  [string]$PiHost    = "Robin.local",
  [string]$PiUser    = "jason",
  [int]   $PiPort    = 1143,
  [string]$AppDir    = "/var/www/Mom",
  [string]$DesktopIP = "192.168.1.205",
  [string]$Mac       = "",

  [string]$Reason = "hermes3:8b",
  [string]$Vision = "qwen3-vl:2b",
  [string]$Embed  = "embeddinggemma:300m",
  [int]   $NumCtx = 16384,

  [string]$AdminUser = "person4@example.com",
  [string]$MomUser   = "person3@example.com",
  [string]$AdminTZ   = "America/New_York",
  [string]$MomTZ     = "America/Los_Angeles",

  [switch]$Verify,
  [switch]$Cleanup,
  [switch]$Yes,
  [switch]$SkipDesktop,
  [switch]$SkipAccounts
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$Here   = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$Target = "$PiUser@$PiHost"
$script:Problems = @()

function Line(){ Write-Host ("  " + ("-" * 62)) -ForegroundColor DarkGray }
function Phase($n,$t){ Write-Host ""; Write-Host "  $n  $t" -ForegroundColor White; Line }
function Ok($t)  { Write-Host "  [ok]   $t" -ForegroundColor Green }
function Note($t){ Write-Host "         $t" -ForegroundColor Gray }
function Warn($t){ Write-Host "  [warn] $t" -ForegroundColor Yellow }
function Bad($t) { Write-Host "  [FAIL] $t" -ForegroundColor Red; $script:Problems += $t }
function Todo($t){ Write-Host "  [YOU]  $t" -ForegroundColor Magenta }

# Native tools write to stderr for ordinary progress. Treating that as an error
# is what made an earlier script explode on a perfectly healthy machine.
function Native { param([string]$Exe,[string[]]$Arguments)
  $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
  [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") } }

function Pi { param([string]$Cmd,[switch]$Interactive)
  if ($Interactive) { & ssh -t -p $PiPort $Target $Cmd; return }
  Native ssh @('-p',"$PiPort",$Target,$Cmd) }

function Web { param([string]$Url,[int]$T=5)
  try { Invoke-RestMethod -Uri $Url -TimeoutSec $T | Out-Null; $true } catch { $false } }

Write-Host ""
Write-Host "  ==============================================================" -ForegroundColor White
Write-Host "   Tech Help" -ForegroundColor White
Write-Host "  ==============================================================" -ForegroundColor White
Note "desktop $DesktopIP        pi ${Target}:${AppDir}"


# ============================================================== CLEAN UP =====
if ($Cleanup) {
  Phase "TIDY" "What would be moved or removed"

  # Superseded by DEPLOY.ps1 or by later versions of themselves. MOVED to
  # _archive, never deleted.
  $archive = @(
    'Setup-TechHelp.ps1','setup-models.ps1','setup-wol.ps1','setup-hermes.ps1',
    'setup-hermes-OBSOLETE.ps1','Deploy-Mom.ps1','create-accounts.sh',
    'install.sh','rename-pi.sh','jasons-site.zip','jasons-site_1.zip'
  )
  # Still useful, just not at the top level. These are what you reach for when
  # something breaks, so they are kept -- filed, not archived.
  $tools = @(
    'doctor.sh','recover.sh','fix-tls.sh','fix-mac.sh','fix-login.sh',
    'fix-caddy.sh','test-chain.sh','test-wake-on-login.sh','setup-portainer.sh',
    'install-nightly.sh','docker-compose.peppermint.yml','test_mom.py','test_growth.py'
  )
  # Never touched, whatever else happens.
  $keep = @(
    'DEPLOY.ps1','Start-TechHelp.ps1','Install-Startup.ps1','desktop-listener.py',
    'START TECH HELP.cmd','CHECK TECH HELP.cmd','INSTALL STARTUP.cmd',
    'DEPLOY EVERYTHING.cmd','CLEAN UP.cmd','VERIFY.cmd',
    'TechHelp.exe','techhelp.ico','app','knowledge','Dockerfile',
    'docker-compose.yml','docker-entrypoint.sh','requirements.txt','manage.py',
    'deploy.sh','.env','.env.example','index.php','systemd','tools','_archive'
  )

  $stamp   = Get-Date -Format 'yyyy-MM-dd'
  $archDir = Join-Path $Here "_archive\$stamp"
  $toolDir = Join-Path $Here 'tools'

  $toArchive = @(); $toFile = @(); $unknown = @()
  foreach ($item in Get-ChildItem -Path $Here -Force |
                    Where-Object { $_.Name -notlike '.*' -or $_.Name -eq '.env.example' }) {
    if ($keep -contains $item.Name)        { continue }
    elseif ($archive -contains $item.Name) { $toArchive += $item }
    elseif ($tools   -contains $item.Name) { $toFile    += $item }
    else                                   { $unknown   += $item }
  }

  if ($toArchive) {
    Write-Host ""; Note "MOVE to _archive\$stamp\ (superseded):"
    $toArchive | ForEach-Object { Write-Host "           $($_.Name)" -ForegroundColor DarkYellow }
  }
  if ($toFile) {
    Write-Host ""; Note "MOVE to tools\ (diagnostics, kept):"
    $toFile | ForEach-Object { Write-Host "           $($_.Name)" -ForegroundColor DarkCyan }
  }
  if ($unknown) {
    Write-Host ""
    Warn "Not on any list, so LEFT ALONE:"
    $unknown | ForEach-Object { Write-Host "           $($_.Name)" -ForegroundColor Gray }
    Note "The script only moves things it recognises. Anything you added"
    Note "yourself stays exactly where it is."
  }

  Write-Host ""; Note "On the Pi, REMOVE:"
  $piJunk = @("~/momhelp-upload", "/etc/caddy/Caddyfile.bak-*", "/root/caddy-backup-*",
              "$AppDir/create-accounts.sh", "$AppDir/install.sh", "$AppDir/rename-pi.sh",
              "$AppDir/caddy-mom.conf", "/tmp/Caddyfile.*")
  $piJunk | ForEach-Object { Write-Host "           $_" -ForegroundColor DarkYellow }
  Note ""
  Note "NOT touched on the Pi: .env, knowledge/, app/, the momhelp-data volume,"
  Note "and every ticket, answer and account inside it."

  if (-not $Yes) {
    Write-Host ""
    Warn "Nothing has been changed. This was a preview."
    Note "When you are satisfied the site works:   .\DEPLOY.ps1 -Cleanup -Yes"
    Write-Host ""
    return
  }

  Phase "TIDY" "Doing it"
  New-Item -ItemType Directory -Force -Path $archDir | Out-Null
  New-Item -ItemType Directory -Force -Path $toolDir | Out-Null
  foreach ($f in $toArchive) {
    Move-Item -LiteralPath $f.FullName -Destination $archDir -Force -ErrorAction SilentlyContinue
    Ok "archived $($f.Name)"
  }
  foreach ($f in $toFile) {
    Move-Item -LiteralPath $f.FullName -Destination $toolDir -Force -ErrorAction SilentlyContinue
    Ok "filed $($f.Name) under tools\"
  }

  # The Pi keeps its own copies of the diagnostics; only genuine leftovers go.
  $cmd = @(
    "rm -rf ~/momhelp-upload",
    "sudo rm -f /etc/caddy/Caddyfile.bak-* 2>/dev/null",
    "sudo rm -rf /root/caddy-backup-* 2>/dev/null",
    "sudo rm -f $AppDir/create-accounts.sh $AppDir/install.sh $AppDir/rename-pi.sh $AppDir/caddy-mom.conf 2>/dev/null",
    "rm -f /tmp/Caddyfile.* 2>/dev/null",
    "echo CLEANED"
  ) -join '; '
  $r = Pi $cmd
  if ($r.Text -match 'CLEANED') { Ok "Pi tidied" } else { Warn "Pi cleanup: $($r.Text)" }

  Write-Host ""
  Ok "Done. Archived files are in _archive\$stamp\ -- move them back any time."
  Write-Host ""
  return
}


# =============================================================== VERIFY ONLY ==
function Invoke-Verify {
  Phase "CHECK" "Does the whole thing work"
  $problemsBefore = $script:Problems.Count

  if (Web 'http://127.0.0.1:11434/api/tags') {
    Ok "Ollama answering locally"
    # Test the address the PI actually uses, not whichever adapter Windows
    # happens to list first. VirtualBox, WSL, Hyper-V and VPNs all add
    # interfaces, and "reachable at 192.168.1.1" -- a VirtualBox host-only
    # adapter -- proves nothing about whether the Pi can get through. It
    # reported success while the Pi was still failing.
    $ip = $DesktopIP
    $mine = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty IPAddress)
    if ($mine -notcontains $ip) {
      Warn "this machine does not hold $ip"
      Note "it has: $($mine -join ', ')"
      Note "If the Pi is pointed at $ip and this PC is not on it, nothing"
      Note "connects. Re-run with  -DesktopIP <the right one>."
    }
    if (Web "http://${ip}:11434/api/tags") {
      Ok "Ollama reachable at ${ip}:11434 -- the address the Pi uses"
    } else {
      Bad "Ollama is NOT reachable at ${ip}:11434"
      Note "It answers 127.0.0.1 but not its LAN address, so it is bound to"
      Note "localhost. Quit Ollama from the system tray -- not just the window"
      Note "-- then run:  .\START TECH HELP.cmd"
    }
  } else { Bad "Ollama is not running" }

  if (Web 'http://127.0.0.1:9000/health') { Ok "Whisper answering" } else { Warn "Whisper not answering" }

  $r = Pi "sudo bash $AppDir/test-chain.sh 2>&1 || sudo bash $AppDir/tools/test-chain.sh 2>&1"
  if ($r.Text) {
    ($r.Text -split "`n") | Where-Object { $_ -match 'ok|FAIL|BROKEN|NOT YET|YES --' } |
      ForEach-Object { Note $_.Trim() }
    if ($r.Text -match 'NOT YET') {
      foreach ($l in ($r.Text -split "`n")) {
        if ($l -match '^\s*-\s+(.+)$') { Bad "on the Pi: $($Matches[1].Trim())" } }
      if ($r.Text -match 'MOM_DESKTOP_MAC is not set') {
        Note ""
        Note "That is one setting, and it is YOUR desktop's MAC, not your"
        Note "mother's -- hers is never used. Push it with:"
        Note "  .\DEPLOY.ps1 -SkipDesktop -SkipAccounts"
      }
    }
    elseif ($r.Text -match 'YES --') { Ok "the Pi reports the full chain working" }
  }

  try {
    $resp = Invoke-WebRequest -Uri 'https://example.org/Mom/' -TimeoutSec 20 `
              -MaximumRedirection 0 -UseBasicParsing -ErrorAction Stop
    Ok "her address answers from the internet (HTTP $([int]$resp.StatusCode))"
  } catch {
    if ($_.Exception.Response) { Ok "her address answers (HTTP $([int]$_.Exception.Response.StatusCode))" }
    else { Bad "her address did not answer" }
  }
  return ($script:Problems.Count -eq $problemsBefore)
}

if ($Verify) {
  Invoke-Verify | Out-Null
  Write-Host ""
  if ($script:Problems.Count -eq 0) { Ok "Nothing failed." } else {
    Write-Host "  $($script:Problems.Count) problem(s):" -ForegroundColor Red
    $script:Problems | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red } }
  Write-Host ""
  return
}


# ================================================================== DEPLOY ====
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# --- 1. the desktop -----------------------------------------------------------
if (-not $SkipDesktop) {
  Phase "1/6" "This computer"

  $nic = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
         Where-Object { $_.Status -eq 'Up' -and $_.MediaType -ne 'Native 802.11' } |
         Select-Object -First 1
  if ($nic) {
    if (-not $Mac) { $Mac = $nic.MacAddress }
    Ok "wired adapter: $($nic.InterfaceDescription)"
    Note "MAC $Mac"
  } else { Warn "no wired adapter up -- Wake-on-LAN needs one" }

  if ($isAdmin -and $nic) {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
    if ((Get-ItemProperty -Path $key -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled -ne 0) {
      Set-ItemProperty -Path $key -Name HiberbootEnabled -Value 0 -Type DWord -ErrorAction SilentlyContinue
      Ok "Fast Startup turned off (it disarms the network card)"
    } else { Ok "Fast Startup already off" }
    foreach ($d in (Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceEnable -ErrorAction SilentlyContinue)) {
      if ($d.InstanceName -match [regex]::Escape($nic.PnPDeviceID)) {
        Set-CimInstance -InputObject $d -Property @{ Enable = $true } -ErrorAction SilentlyContinue } }
    foreach ($d in (Get-CimInstance -Namespace root\wmi -ClassName MSNdis_DeviceWakeOnMagicPacketOnly -ErrorAction SilentlyContinue)) {
      if ($d.InstanceName -match [regex]::Escape($nic.PnPDeviceID)) {
        Set-CimInstance -InputObject $d -Property @{ EnableWakeOnMagicPacketOnly = $true } -ErrorAction SilentlyContinue } }
    Native powercfg @('/deviceenablewake', $nic.InterfaceDescription) | Out-Null
    foreach ($k in 'Energy Efficient Ethernet','Green Ethernet','Advanced EEE','Gigabit Lite','Power Saving Mode') {
      if (Get-NetAdapterAdvancedProperty -Name $nic.Name -DisplayName $k -ErrorAction SilentlyContinue) {
        Set-NetAdapterAdvancedProperty -Name $nic.Name -DisplayName $k -DisplayValue 'Disabled' -ErrorAction SilentlyContinue } }
    Ok "wake settings applied"
    Native powercfg @('/hibernate','on') | Out-Null
  } elseif ($nic) {
    Warn "not Administrator -- wake settings skipped"
    Note "Right-click PowerShell, Run as Administrator, and run this again."
  }

  Todo "In the BIOS: ErP / EuP Ready DISABLED, Wake on LAN ENABLED."
  Note "ErP cuts standby power to the network card. Nothing else matters if it is on."
  Note "Use  shutdown /h  (hibernate), never Shut Down."

  Phase "2/6" "Models"
  $envVars = [ordered]@{
    'OLLAMA_HOST'='0.0.0.0:11434'; 'OLLAMA_MAX_LOADED_MODELS'='3'
    'OLLAMA_NUM_PARALLEL'='1'; 'OLLAMA_FLASH_ATTENTION'='1'
    'OLLAMA_KV_CACHE_TYPE'='q8_0'; 'OLLAMA_CONTEXT_LENGTH'="$NumCtx"
    'OLLAMA_KEEP_ALIVE'='-1' }
  foreach ($k in $envVars.Keys) {
    [Environment]::SetEnvironmentVariable($k, $envVars[$k], 'User')
    Set-Item -Path "Env:$k" -Value $envVars[$k] }
  Ok "Ollama settings written"

  if (Get-Command ollama -ErrorAction SilentlyContinue) {
    # What is already here? Re-downloading 7.2 GB is the difference between this
    # phase taking two seconds and twenty minutes.
    $have = (Native ollama @('list')).Text
    foreach ($m in @($Reason,$Vision,$Embed)) {
      $short = [regex]::Escape($m.Split(':')[0])
      if ($have -match [regex]::Escape($m) -or $have -match "(?m)^$short\s") {
        Ok "$m already installed"; continue }
      Note ""
      Note "downloading $m -- the slow part; its progress bar is below"
      # NOT through Native. `ollama pull` draws a live progress bar, and
      # capturing its output makes a twenty-minute download look like a frozen
      # script -- which is exactly what happened.
      & ollama pull $m
      if ($LASTEXITCODE -eq 0) { Ok "$m ready" } else { Bad "could not pull $m" }
    }
  } else { Bad "ollama is not installed" }
}

# --- 3. push to the Pi --------------------------------------------------------
Phase "3/6" "The Pi"
$probe = Test-NetConnection -ComputerName $PiHost -Port $PiPort -WarningAction SilentlyContinue
if (-not $probe.TcpTestSucceeded) {
  Bad "cannot reach $PiHost on $PiPort"
} else {
  Ok "$PiHost answering"
  $stage = "~/momhelp-upload"
  Pi "rm -rf $stage; mkdir -p $stage" | Out-Null
  $items = @('app','knowledge','Dockerfile','docker-compose.yml','docker-compose.peppermint.yml',
             'docker-entrypoint.sh','requirements.txt','manage.py','test_mom.py','test_growth.py',
             'deploy.sh','doctor.sh','install-nightly.sh','test-chain.sh','test-wake-on-login.sh',
             'fix-login.sh','fix-mac.sh','fix-tls.sh','recover.sh','setup-portainer.sh','.env.example')
  $sent = 0
  foreach ($i in $items) {
    $p = Join-Path $Here $i
    if (-not (Test-Path $p)) { $p = Join-Path (Join-Path $Here 'tools') $i }
    if (-not (Test-Path $p)) { continue }
    & scp -P $PiPort -q -r $p "${Target}:${stage}/" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $sent++ } }
  Ok "$sent item(s) uploaded"
  Pi "find $stage -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; sed -i 's/\r`$//' $stage/*.sh 2>/dev/null; chmod +x $stage/*.sh" | Out-Null

  Note ""
  Note "The Pi will ask for your password. Type it and press Enter."
  Pi "sudo -v" -Interactive
  Note "Building. First run takes several minutes; later ones are cached."
  Pi "cd $stage; sudo APP_DIR=$AppDir ROOT_PATH=/Mom bash deploy.sh" -Interactive
  Pi "cd $AppDir; sudo docker compose up -d --build" -Interactive

  Phase "4/6" "Settings on the Pi"
  $bcast = ($DesktopIP -replace '\.\d+$','.255')
  $sets = @(
    @{K='MOM_GPU_HOST';V=$DesktopIP}, @{K='MOM_DESKTOP_IP';V=$DesktopIP},
    @{K='MOM_DESKTOP_BROADCAST';V=$bcast}, @{K='MOM_ROOT_PATH';V='/Mom'},
    @{K='MOM_MODEL_PROFILE';V='resident'}, @{K='MOM_MODEL_REASON';V=$Reason},
    @{K='MOM_MODEL_VISION';V=$Vision}, @{K='MOM_MODEL_EMBED';V=$Embed},
    @{K='MOM_OLLAMA_MODEL';V=$Reason}, @{K='MOM_OLLAMA_NUM_CTX';V="$NumCtx"},
    @{K='MOM_DEFAULT_TZ';V=$AdminTZ}, @{K='MOM_LEARNED_DIR';V='/app/knowledge/learned'},
    @{K='MOM_AUTO_WAKE';V='1'})
  if ($Mac) { $sets += @{K='MOM_DESKTOP_MAC';V=$Mac} }

  # Delete-then-append rather than replace-in-place: replacement silently does
  # nothing when the key is absent, which is exactly how the MAC ended up blank.
  $lines = @('set -e', "F=$AppDir/.env")
  foreach ($s in $sets) {
    $lines += "sed -i '/^[[:space:]]*$($s.K)[[:space:]]*=/d' `$F"
    $lines += "printf '%s=%s\n' '$($s.K)' '$($s.V)' >> `$F" }
  ($lines -join "`n") | & ssh -p $PiPort $Target "sudo sh -s" 2>&1 | Out-Null
  Pi "cd $AppDir; sudo docker compose restart" | Out-Null

  $r = Pi "sudo grep -E '^MOM_(DESKTOP_MAC|DESKTOP_BROADCAST|ROOT_PATH|MODEL_PROFILE)=' $AppDir/.env"
  ($r.Text -split "`n") | Where-Object { $_ } | ForEach-Object { Note $_ }
  if ($r.Text -match 'MOM_DESKTOP_MAC=.+') { Ok "settings verified" }
  else { Bad "MOM_DESKTOP_MAC did not take" }

  if (-not $SkipAccounts) {
    Phase "5/6" "Accounts"
    foreach ($a in @(@{U=$AdminUser;N='Jason';TZ=$AdminTZ;A=' --admin'},
                     @{U=$MomUser;N='Mom';TZ=$MomTZ;A=''})) {
      $r = Pi "sudo docker exec momhelp python manage.py adduser $($a.U) --name '$($a.N)'$($a.A) --generate"
      foreach ($l in ($r.Text -split "`n")) {
        if ($l -match '^Password:\s*(.+)$') {
          Write-Host "         PASSWORD  $($a.U)  ->  $($Matches[1].Trim())" -ForegroundColor Yellow } }
      Pi "sudo docker exec momhelp python manage.py timezone $($a.U) $($a.TZ)" | Out-Null
      Pi "sudo docker exec momhelp python manage.py nochange $($a.U)" | Out-Null
    }
    $r = Pi "sudo docker exec momhelp python manage.py users"
    ($r.Text -split "`n") | Where-Object { $_ } | ForEach-Object { Note $_ }
    Note ""
    Note "Write those passwords down. They are shown once and stored as hashes."
  }
}

# --- 6. the clickable thing ---------------------------------------------------
Phase "6/6" "Desktop launcher"
$exe = Join-Path $Here 'TechHelp.exe'
$ico = Join-Path $Here 'techhelp.ico'
$src = Join-Path $Here 'Start-TechHelp.ps1'
$built = $false

if (Test-Path $src) {
  if (-not (Get-Module -ListAvailable -Name ps2exe)) {
    Note "installing ps2exe (once, from the PowerShell Gallery)"
    try { Install-Module ps2exe -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop } catch { }
  }
  if (Get-Module -ListAvailable -Name ps2exe) {
    try {
      Import-Module ps2exe -ErrorAction Stop
      $args = @{ inputFile = $src; outputFile = $exe; title = 'Tech Help'
                 description = 'Starts Ollama, Whisper and the ticket listener' }
      if (Test-Path $ico) { $args['iconFile'] = $ico }
      Invoke-PS2EXE @args -ErrorAction Stop | Out-Null
      if (Test-Path $exe) { Ok "built TechHelp.exe with the power icon"; $built = $true }
    } catch { Warn "ps2exe failed: $($_.Exception.Message)" }
  }
}

# A shortcut either way: it points at the .exe if one was built, the .cmd if
# not, and it is what actually lands on the Desktop with the icon.
try {
  $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tech Help.lnk'
  $sh  = New-Object -ComObject WScript.Shell
  $s   = $sh.CreateShortcut($lnk)
  $s.TargetPath       = if ($built) { $exe } else { Join-Path $Here 'START TECH HELP.cmd' }
  $s.WorkingDirectory = $Here
  $s.Description      = 'Start Ollama, Whisper and the ticket listener'
  if (Test-Path $ico) { $s.IconLocation = "$ico,0" }
  $s.Save()
  Ok "Desktop shortcut created: Tech Help"
  if (-not $built) { Note "(points at the .cmd -- ps2exe was unavailable, works the same)" }
} catch { Warn "could not create the Desktop shortcut" }

if (Test-Path (Join-Path $Here 'Install-Startup.ps1')) {
  if ($isAdmin) {
    & (Join-Path $Here 'Install-Startup.ps1') | Out-Null
    Ok "startup task installed -- services start with Windows"
  } else {
    Note "For automatic start at boot, run as Administrator:"
    Note "  .\INSTALL STARTUP.cmd"
  }
}

Invoke-Verify | Out-Null

# --- the end ------------------------------------------------------------------
Write-Host ""
Write-Host "  ==============================================================" -ForegroundColor White
if ($script:Problems.Count -eq 0) {
  Write-Host "   Deployed. Nothing failed." -ForegroundColor Green
} else {
  Write-Host "   Deployed, with $($script:Problems.Count) problem(s):" -ForegroundColor Yellow
  $script:Problems | ForEach-Object { Write-Host "     - $_" -ForegroundColor Yellow }
}
Write-Host "  ==============================================================" -ForegroundColor White
Write-Host ""
Write-Host "   Her address    https://example.org/Mom/"
Write-Host "   Your console   https://example.org/Mom/admin"
Write-Host ""
Write-Host "   NOW GO AND TRY IT." -ForegroundColor White
Write-Host "   Sign in as her, ask something, make sure you get an answer."
Write-Host ""
Write-Host "   Then, and only then:"
Write-Host "     .\DEPLOY.ps1 -Cleanup        see what tidying would remove"
Write-Host "     .\DEPLOY.ps1 -Cleanup -Yes   do it"
Write-Host ""
