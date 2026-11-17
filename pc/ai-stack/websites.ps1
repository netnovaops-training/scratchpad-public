<#
    Bring the websites up, in an order that can't waste certificates.

    WHY THIS IS STAGED RATHER THAN ONE `up -d`
    ==========================================
    Let's Encrypt allows FIVE certificates per domain per week. Not five
    successes -- five attempts at the rate limit. A misconfiguration that fails
    four times leaves you locked out of your own domain until the window rolls,
    and there is no appeal and no override.

    So this refuses to ask for a real certificate until everything a
    certificate depends on has been proven:

        1. settings           ACME_EMAIL, session keys
        2. DNS                does the name point at THIS house?
        3. build              images compile
        4. the apps           each one answers on its own port, BEFORE Caddy
        5. certificates       CHECKED, not requested. See below.

    CERTIFICATES ARE LEFT ALONE UNLESS YOU ASK
    ==========================================
    This script used to run the full certificate dance on every run: swap the
    Caddyfile to Let's Encrypt's staging endpoint, rehearse, swap back, and ask
    for real certificates again. That is the right thing the FIRST time and the
    wrong thing every time after, because the rehearsal replaces your working
    certificate with an untrusted one and getting the real one back spends one
    of five weekly attempts -- on a certificate you already had.

    So the normal run now only READS the certificate being served and reports
    what it found. Rebuilding a website is not a reason to re-issue a
    certificate, and the two jobs have no business being welded together.

    Ask for the certificate work explicitly when you actually need it -- a new
    domain, a move to a new machine, an expiry that did not auto-renew:

        websites.cmd              rebuild and restart. Certificates untouched.
        websites.cmd -Certs       the staged run: rehearse on staging, then real
        websites.cmd -Real        skip the rehearsal, go straight to real certs
        websites.cmd -Stop        stop the websites, leave the AI stack running

    -Certs is the one to use. Staging exercises exactly the same path -- DNS,
    port 80, the challenge, the parsing -- and costs nothing. If staging works,
    real certs work.
#>

param(
    [switch]$Real,
    [switch]$Certs,
    [switch]$Stop,
    [switch]$SkipDns
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$fail = 0
function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") $script:fail++; Write-Host "  [FAIL] $m" -ForegroundColor Red; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say  { param($m) Write-Host "         $m" -ForegroundColor DarkGray }

$compose = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.web.yml")
# ONE hostname. The sites moved under paths -- /dee, /mama, /casey, /legacy -- so
# teewithmsdee.example.org and peeweelegacy.ddns.net are no longer served and no
# longer checked. Leaving them here would fail the DNS step and stop the run for
# names nothing depends on.
$DOMAINS = @("example.org")

Write-Host ""
Write-Host "  The websites" -ForegroundColor Cyan
Write-Host "  ------------" -ForegroundColor DarkGray

# --- stop --------------------------------------------------------------------
if ($Stop) {
    & docker @($compose + @("--profile", "web", "stop"))
    Ok "websites stopped" "the AI stack and the phone line are untouched"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 0
}

docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Bad "Docker Desktop isn't running"; Read-Host "  Enter"; exit 1 }

# --- 1. settings -------------------------------------------------------------
Head "1. Settings"
if (-not (Test-Path ".env")) { Bad ".env missing" "run setup.cmd"; Read-Host "  Enter"; exit 1 }
$envText = Get-Content ".env" -Raw

$acmeEmail = ""
foreach ($k in @("ACME_EMAIL", "ZEE_SECRET_KEY", "MOM_SECRET_KEY", "BOOK_SECRET_KEY")) {
    if ($envText -match "(?m)^$k=(.+)$" -and $Matches[1].Trim()) {
        $val = $Matches[1].Trim()
        if ($k -eq "ACME_EMAIL") { $acmeEmail = $val; Ok $k $val } else { Ok $k "set" }
    } else {
        Bad "$k is empty" "run env-sync.cmd - it generates the keys for you"
    }
}
# The three Rust services that hold a door shut. Each one REFUSES TO START
# without its secret rather than serving an open admin or an unsigned session
# cookie, so a blank here is a container that dies on boot -- checked now, where
# the message can name the script that fixes it, instead of eight steps later
# where it looks like a web server problem.
foreach ($k in @(
    @{ n = "ADMIN_PASSWORD_HASH"; svc = "admin"; fix = "admin-password.ps1" },
    @{ n = "AUTH_SECRET";         svc = "auth";  fix = "auth-setup.ps1"     }
)) {
    if ($envText -match "(?m)^$($k.n)=(.+)$" -and $Matches[1].Trim() -notmatch '^[''"]?$') {
        Ok $k.n "set"
    } else {
        Bad "$($k.n) is empty" "run .\$($k.fix) - the $($k.svc) container will not start without it"
    }
}

# The folders the bind mounts point at. Docker would create them as root-owned
# directories if they were missing, which works but puts your site data
# somewhere you did not choose -- make them here so they are plainly yours.
foreach ($d in @(".\site-data\data", ".\site-data\backups", ".\site-data\auth")) {  # was ..\Admin until 2026-11-12
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Ok "created $d"
    }
}

if ($fail) { Write-Host ""; Read-Host "  Press Enter to close"; exit 1 }

# --- 2. DNS ------------------------------------------------------------------
#
# The single most common reason certificates fail, and it fails in a way that
# blames Caddy. If a name points at an old IP, Let's Encrypt knocks on a
# stranger's door and gets nothing.
Head "2. Does your domain point here?"
if ($SkipDns) {
    Warn "skipped (-SkipDns)"
} else {
    try {
        $public = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 10).ip
        Ok "this house is $public"
        foreach ($d in $DOMAINS) {
            try {
                $ip = (Resolve-DnsName -Name $d -Type A -ErrorAction Stop |
                       Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
                if ($ip -eq $public) { Ok "$d -> $ip" }
                else { Bad "$d -> $ip" "should be $public. Install No-IP's DUC client, or certificates WILL fail." }
            } catch { Bad "$d doesn't resolve" "check the record at noip.com" }
        }
    } catch { Warn "couldn't look up your public IP" "carrying on" }
}
if ($fail) {
    Write-Host ""
    Say "Fix DNS before going further. Asking for a certificate now would"
    Say "burn one of five weekly attempts on a request that cannot succeed."
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

# --- 2b. is anything already holding 80 or 443? ------------------------------
#
# Docker publishes 80 and 443 for Caddy. If something else already has them,
# the container never starts -- "port is already allocated" -- and on Windows
# the usual culprit is IIS, which grabs port 80 the moment it is installed and
# never mentions it.
#
# Checked HERE rather than later because a certificate cannot be issued to a
# web server that couldn't start, and the attempt still counts against your
# five per week.
Head "2b. Are ports 80 and 443 free?"
foreach ($port in 80, 443) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { Ok "port $port is free"; continue }
    foreach ($c in $conns) {
        $proc = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        if (-not $proc) { $proc = "pid $($c.OwningProcess)" }
        if ($proc -match "docker|com\.docker|vpnkit|wslrelay") {
            Ok "port $port is Docker's" "that's Caddy - fine"
        } elseif ($proc -eq "System" -or $proc -match "^Idle$") {
            # PID 4 / System means HTTP.sys is holding it -- that IS IIS, or
            # something else registered with the kernel HTTP stack.
            Bad "port $port is held by the Windows HTTP service (HTTP.sys)" "almost always IIS"
        } else {
            Bad "port $port is held by '$proc'" "Caddy cannot start until it lets go"
        }
    }
}

$w3 = Get-Service -Name W3SVC -ErrorAction SilentlyContinue
if ($w3) {
    if ($w3.Status -eq "Running") {
        Bad "IIS is running (W3SVC)" "it owns port 80"
    } else {
        Ok "IIS is installed but stopped" "make sure it's Disabled, not just stopped"
    }
    if ($w3.StartType -ne "Disabled") {
        Warn "IIS is set to start automatically" "it will take port 80 back on the next reboot"
    }
}

if ($fail) {
    Write-Host ""
    Say "Free the ports first. In an ADMIN PowerShell:"
    Write-Host ""
    Write-Host "      Stop-Service W3SVC -Force" -ForegroundColor White
    Write-Host "      Set-Service  W3SVC -StartupType Disabled" -ForegroundColor White
    Write-Host "      Stop-Service WAS -Force" -ForegroundColor White
    Write-Host "      Set-Service  WAS -StartupType Disabled" -ForegroundColor White
    Write-Host ""
    Say "WAS matters: it's the service that restarts W3SVC, so disabling only"
    Say "W3SVC lets IIS come back on its own."
    Say ""
    Say "This is reversible and needs no reboot. Uninstalling IIS also works"
    Say "and is tidier if you never use it, but it does need a restart."
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

# --- 3. build ----------------------------------------------------------------
Head "3. Building"
Say "first run compiles four Rust binaries and three Python images -- a few minutes"
& docker @($compose + @("--profile", "web", "build"))
if ($LASTEXITCODE -ne 0) { Bad "build failed" "scroll up; the failing step names itself"; Read-Host "  Enter"; exit 1 }
Ok "images built"

# Check the Caddyfile PARSES before anything depends on it. A syntax error
# means Caddy won't start at all, and finding that out after the apps are up
# and a certificate has been requested wastes both.
#
# `caddy adapt`, NOT `caddy validate`. Validate goes further and PROVISIONS
# every module -- which means it opens the log file, and the log directory only
# exists at runtime as a volume mount. In a throwaway container that fails, and
# it reports a Caddyfile error for a Caddyfile that is completely correct.
# Adapt converts Caddyfile to JSON and stops, which is exactly the question
# being asked here: does this file parse?
#
# Real provisioning is checked a few steps later, when Caddy actually starts
# with its volumes attached and the logs are being watched.
$cfg = (Join-Path $PSScriptRoot "web\Caddyfile")
# -e ACME_EMAIL is REQUIRED here. The Caddyfile says `email {$ACME_EMAIL}`,
# which Caddy expands from its own environment at parse time. A bare container
# has no such variable, so it expands to nothing, `email` is left with no
# argument, and the validator rejects a file that is perfectly correct --
# reporting a Caddyfile error that doesn't exist.
$out = docker run --rm -e "ACME_EMAIL=$acmeEmail" -v "${cfg}:/etc/caddy/Caddyfile:ro" caddy:2-alpine `
        caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) {
    Ok "Caddyfile parses" "syntax only - Caddy proves the rest when it starts"
} else {
    Bad "the Caddyfile has an error"
    ($out -split "`n" | Where-Object { $_ -match "error|Error" } | Select-Object -First 5) |
        ForEach-Object { Say $_.Trim() }
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

# Start a set of containers and REPORT IT IF THEY DON'T START.
#
# Every one of these calls used to be `... 2>&1 | Out-Null`, which threw the
# error away and never looked at the exit code. So when Caddy failed to start,
# the script said nothing at all, then spent three minutes reading the logs of
# a container that did not exist, and reported "no clear result" -- which reads
# like a slow certificate rather than a dead web server. That is how you end up
# debugging Let's Encrypt when the actual problem is one line of config.
function Start-Containers {
    param([string[]]$Names, [string]$What)
    $args_ = $compose + @("up", "-d")
    if ($Names) { $args_ += $Names }
    $out = & docker @($args_) 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Bad "$What did not start"
        ($out -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 8) |
            ForEach-Object { Say $_.Trim() }
        return $false
    }
    # Exit 0 is not enough: compose reports success for a container that starts
    # and then immediately dies. Ask what is actually running.
    Start-Sleep -Seconds 3
    foreach ($n in $Names) {
        $st = & docker @($compose + @("ps", "-a", "--format", "{{.Name}} {{.State}}", $n)) 2>&1 | Out-String
        if ($st -notmatch "running") {
            Bad "$n is not running" $st.Trim()
            $lg = & docker @($compose + @("logs", "--tail", "20", $n)) 2>&1 | Out-String
            ($lg -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 8) |
                ForEach-Object { Say $_.Trim() }
            return $false
        }
    }
    return $true
}

# --- 4. the apps, before Caddy ----------------------------------------------
#
# Starting Caddy first means a broken app looks like a broken web server. Prove
# each one answers on its own port while the only thing that can be wrong is
# the app itself.
Head "4. The apps (before the web server)"
if (-not (Start-Containers @("landing","admin","auth","legacy","casey","mom","teabook","mealie","kitchen") "the apps")) {
    Write-Host ""; Say "Fix the above before going further."
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}
Start-Sleep -Seconds 8

# `tool` is per-image and not incidental. These images genuinely differ:
#   the Rust four  Debian slim + wget  (Dockerfile installs wget, has no python)
#   the rest       python:3.12-slim    (Dockerfile installs curl, has python)
# A probe written against one tool marks every container using the other as
# broken -- a healthy app failing a broken test.
#
# admin, auth and legacy all REFUSE TO START rather than serve unprotected, so
# a missing password shows up here as "isn't running" with the reason in its
# own log, not as a mysterious 502 after a certificate has been spent.
$apps = @(
    @{ n = "landing"; p = 8010; what = "the landing page";      tool = "wget"   },
    @{ n = "admin";   p = 8011; what = "the admin (/admin)";    tool = "wget"   },
    @{ n = "auth";    p = 8013; what = "sign-in codes (/auth)"; tool = "wget"   },
    @{ n = "legacy";  p = 8012; what = "photos (/legacy)";      tool = "wget"   },
    @{ n = "casey";     p = 8000; what = "career portal (/casey)";  tool = "python" },
    @{ n = "mom";     p = 8001; what = "Tech Help (/mama)";     tool = "python" },
    @{ n = "teabook"; p = 8002; what = "Tea Miss Dee";          tool = "python" },
    @{ n = "kitchen"; p = 8003; what = "recipes (/kitchen)";    tool = "python" },
    # Mealie is a third-party image with neither wget nor curl, but it is
    # python-based, so the python probe works. It has NO /healthz -- that
    # 404s -- and the probe's second try, "/", answers 200. That fallback is
    # load-bearing here rather than incidental.
    @{ n = "mealie";  p = 9000; what = "recipe library (:9443)"; tool = "python" }
)
foreach ($a in $apps) {
    $up = (docker inspect -f "{{.State.Running}}" $a.n 2>$null | Out-String).Trim()
    if ($up -ne "true") {
        Bad "$($a.n) isn't running" "logs.cmd $($a.n)"
        continue
    }
    # Ask from inside the network. Testing from Windows would only prove port
    # bindings, which isn't the question -- Caddy reaches these internally.
    #
    # Probe with PYTHON, not wget or curl. The three app images are
    # python:3.12-slim, which ships NEITHER by default; Dockerfile.app installs
    # curl only, and the Rust landing image installs wget only. A probe written
    # against one tool reports every container using the other as "up but not
    # answering" -- a healthy app failing a broken test, which is a worse
    # outcome than no test.
    #
    # Every one of these images has a working python or a shell, so ask in the
    # way that can't be missing, and print the real status code.
    if ($a.tool -eq "python") {
        # Built line by line rather than as a here-string on purpose. A
        # here-string whose opening @" picks up a stray character, or whose
        # file has mixed line endings, doesn't fail politely -- PowerShell
        # parses the body as CODE, and the first apostrophe in it becomes an
        # unterminated string. The error then points at a line that looks
        # fine. Not worth the risk for nine lines of Python.
        $probe = @(
            "import sys,urllib.request",
            "last = ''",
            "for p in ('/healthz','/'):",
            "    try:",
            "        print('HTTP', urllib.request.urlopen('http://127.0.0.1:$($a.p)'+p, timeout=6).status)",
            "        sys.exit(0)",
            "    except Exception as e:",
            "        last = e",
            "print('FAILED', last)"
        ) -join "`n"
        $res = & docker @($compose + @("exec", "-T", $a.n, "python", "-c", $probe)) 2>&1 | Out-String
    } else {
        $res = & docker @($compose + @("exec", "-T", $a.n, "sh", "-c",
            "wget -qO- -T 6 http://127.0.0.1:$($a.p)/healthz >/dev/null 2>&1 && echo 'HTTP 200' || echo FAILED")) 2>&1 | Out-String
    }
    if ($res -match "HTTP\s+(\d+)") {
        Ok "$($a.n) answers on $($a.p)" "$($a.what) - HTTP $($Matches[1])"
    } else {
        Bad "$($a.n) isn't answering" "$(($res -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1))"
        Say "logs.cmd $($a.n)"
    }
}
if ($fail) {
    Write-Host ""
    Say "Fix the apps first. A certificate for a site that 502s is still a"
    Say "wasted attempt, and Caddy would be blamed for an app's problem."
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

# --- 5 / 6. certificates -----------------------------------------------------
function Set-Staging([bool]$on) {
    $f = "web\Caddyfile"
    $t = Get-Content $f -Raw
    if ($on) {
        $t = $t -replace '(?m)^\s*#\s*(acme_ca\s+https://acme-staging.*)$', "`t`$1"
    } else {
        $t = $t -replace '(?m)^\s*(acme_ca\s+https://acme-staging.*)$', "`t# `$1"
    }
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot $f), $t,
        (New-Object System.Text.UTF8Encoding $false))
}

# Open a TLS connection to Caddy and read back the certificate it serves for a
# given hostname. This is ground truth, where a log line is only a hint.
#
# Goes to 127.0.0.1 with the SNI name set by hand rather than to the public
# name, because many routers -- Google Wifi included -- will not loop a
# connection back to your own public IP. Testing the public name from this PC
# can fail while the site is perfectly fine for the rest of the world.
function Get-ServedCert {
    param([string]$Domain, [string]$Ip = "127.0.0.1", [int]$Port = 443)
    $client = $null; $ssl = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Ip, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(6000)) { throw "no answer on ${Ip}:${Port}" }
        $client.EndConnect($iar)
        $accept = [System.Net.Security.RemoteCertificateValidationCallback] {
            param($sender, $certificate, $chain, $errors) return $true
        }
        $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $accept)
        # TLS 1.2 named explicitly: PowerShell 5.1 still defaults to a protocol
        # list Caddy refuses, and that looks like "no certificate" when it is
        # really "no agreed handshake".
        $ssl.AuthenticateAsClient($Domain, $null,
            [System.Security.Authentication.SslProtocols]::Tls12, $false)
        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 `
            -ArgumentList (,$ssl.RemoteCertificate.GetRawCertData())
    } finally {
        if ($ssl) { $ssl.Dispose() }
        if ($client) { $client.Close() }
    }
}

function Watch-Caddy([int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    $seen = @{}
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $log = & docker @($compose + @("logs", "--tail", "300", "caddy")) 2>&1 | Out-String
        foreach ($d in $DOMAINS) {
            if (-not $seen[$d] -and $log -match [regex]::Escape($d)) {
                if ($log -match "certificate obtained successfully.*$([regex]::Escape($d))" -or
                    $log -match "$([regex]::Escape($d)).*certificate obtained") {
                    Ok "certificate for $d"; $seen[$d] = $true
                }
            }
        }
        if (($log -split "certificate obtained").Count - 1 -ge $DOMAINS.Count) { return $true }
        if ($log -match "could not get certificate|no such host|timeout|urn:ietf:params:acme:error") {
            return $false
        }
    }
    return $null
}

# Does the certificate work run at all? Only when asked.
$wantCerts = $Certs -or $Real

if (-not $wantCerts) {
    Head "5. Certificates (reading, not requesting)"
    Say "Nothing here asks Let's Encrypt for anything. Rebuilding a site is not"
    Say "a reason to re-issue a certificate you already have."

    # Idempotent, and not merely tidy: if an earlier run was interrupted between
    # the rehearsal and the real request, the Caddyfile is still pointed at the
    # staging endpoint and Caddy would quietly serve an untrusted certificate
    # for as long as nobody looked. Put it back before starting anything.
    Set-Staging $false

    if (-not (Start-Containers @("caddy") "Caddy")) {
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }
    & docker @($compose + @("--profile", "web", "up", "-d")) 2>&1 | Out-Null

    # A bind-mounted Caddyfile that changed does NOT restart the container --
    # compose sees no change to the service and leaves it alone, so an edited
    # route stays invisible until something else happens to recreate it. Ask
    # Caddy to re-read the file. This does not touch certificates.
    #
    # NOT a fix for a changed ADMIN_PATH or any other {$VAR}: those are frozen
    # into the container's environment at creation, and reload cannot see them.
    $rl = & docker @($compose + @("exec", "-T", "caddy",
        "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")) 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Ok "Caddy re-read its config"
    } else {
        Warn "Caddy would not reload its config" (($rl -split "`n" | Where-Object { $_ -match "error" } | Select-Object -First 1))
    }
    Start-Sleep -Seconds 5

    $have = 0
    foreach ($d in $DOMAINS) {
        $c = $null
        try { $c = Get-ServedCert -Domain $d } catch { }
        if ($null -eq $c) {
            Bad "$d - nothing served on 443"
        } elseif ($c.Issuer -match "STAGING") {
            Bad "$d - a STAGING certificate is being served" "browsers will warn about it"
        } elseif ($c.Issuer -match "Caddy Local Authority") {
            Bad "$d - self-signed fallback" "Let's Encrypt has never reached this machine"
        } else {
            $days = [int]([math]::Floor(($c.NotAfter - (Get-Date)).TotalDays))
            Ok "$d - real certificate, untouched" "expires $($c.NotAfter.ToString('d MMM yyyy')) ($days days)"
            # Caddy renews at 30 days left, on its own, without this script. A
            # number below that means the automatic renewal is not working, and
            # that is worth saying out loud while there is still time to fix it.
            if ($days -lt 30) {
                Warn "under 30 days left" "Caddy should have renewed by now - check logs.cmd caddy"
            }
            $have++
        }
    }

    if ($have -lt $DOMAINS.Count) {
        Write-Host ""
        Say "The certificate is not right, and I am not going to ask for a new one"
        Say "on my own -- you get five real attempts per domain per week and a"
        Say "failed one still counts."
        Write-Host ""
        Say "When you want to fix it, rehearse against staging first:"
        Write-Host ""
        Write-Host "      websites.cmd -Certs" -ForegroundColor White
        Write-Host ""
        Say "That costs nothing and proves the whole chain before anything real"
        Say "is spent. Or just look:  certs.cmd"
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }
}

if ($wantCerts -and -not $Real) {
    Head "5. Staging certificates (the free rehearsal)"
    Say "Let's Encrypt's staging endpoint. Same DNS, same port 80, same challenge."
    Say "Browsers will warn about the certificate -- that is the expected result."
    Set-Staging $true
    & docker @($compose + @("rm", "-sf", "caddy")) 2>&1 | Out-Null
    if (-not (Start-Containers @("caddy") "Caddy")) {
        Write-Host ""
        Say "The web server itself did not start, so no certificate could be"
        Say "requested. The lines above are the reason. Nothing was wasted."
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }
    Say "watching Caddy for up to 3 minutes..."
    $res = Watch-Caddy 180

    if ($res -eq $false) {
        Bad "staging failed"
        $log = & docker @($compose + @("logs", "--tail", "40", "caddy")) 2>&1 | Out-String
        ($log -split "`n" | Select-String -Pattern "error|could not|no such host" | Select-Object -First 6) |
            ForEach-Object { Say $_ }
        Write-Host ""
        Say "Almost always ports 80/443 not reaching this PC from OUTSIDE."
        Say "Test: on your phone with wifi OFF, open http://$($DOMAINS[0])"
        Say "Then: firewall.cmd"
        Say ""
        Say "Nothing was wasted -- staging has no meaningful rate limit."
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }
    # Log watching is a hint, not a verdict. Ask the server what it is actually
    # serving -- open a TLS connection and read the certificate back.
    #
    # This block used to print "Ready for real certificates / staging passing
    # means these will too" UNCONDITIONALLY, including after an inconclusive
    # watch. That is how you get talked into spending one of five weekly
    # attempts on a chain nobody had proven. Inconclusive now means STOP.
    if ($null -eq $res) { Warn "no clear result in 3 minutes" "checking the server directly instead" }
    elseif ($res)       { Ok "staging worked" "the whole chain is proven" }

    Write-Host ""
    Say "Reading the certificate each site is actually serving..."
    $verified = 0
    foreach ($d in $DOMAINS) {
        $c = $null
        try { $c = Get-ServedCert -Domain $d } catch { }
        if ($null -eq $c) {
            Bad "$d - nothing served on 443"
        } elseif ($c.Issuer -match "STAGING") {
            Ok "$d - staging certificate present"; $verified++
        } elseif ($c.Issuer -match "Caddy Local Authority") {
            Bad "$d - self-signed fallback" "Let's Encrypt could not reach this machine"
        } else {
            Warn "$d - unexpected issuer" $c.Issuer
        }
    }

    if ($verified -lt $DOMAINS.Count) {
        Write-Host ""
        Bad "the rehearsal did NOT pass"
        Say "Stopping here on purpose. Real certificates would fail the same way,"
        Say "and failed attempts count against your five per domain per week."
        Write-Host ""
        Say "In order of likelihood:"
        Say "  1. The router still forwards 80/443 to the old Pi, not this PC."
        Say "  2. Windows Firewall is closed.   Run:  firewall.cmd"
        Say "  3. Your ISP blocks inbound port 80."
        Write-Host ""
        Say "Test 1 and 2 from your PHONE with wifi OFF:  http://$($DOMAINS[0])"
        Say "The exact ACME error:  logs.cmd caddy"
        Write-Host ""
        Say "Nothing was wasted. Staging has no meaningful rate limit, so you can"
        Say "re-run this as many times as it takes."
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }

    Write-Host ""
    Write-Host "  Ready for real certificates." -ForegroundColor Yellow
    Say "A staging certificate was served, so the challenge reached this machine"
    Say "and passed. Real ones will take the identical path."
    Write-Host ""
    $go = Read-Host "  Get real certificates now? (Y/n)"
    if ($go -eq "n") {
        Say "Stopping here. Run  websites.cmd -Real  when you're ready."
        Write-Host ""; Read-Host "  Press Enter to close"; exit 0
    }
}

if ($wantCerts) {
Head "6. Real certificates"
Set-Staging $false
& docker @($compose + @("rm", "-sf", "caddy")) 2>&1 | Out-Null
if (-not (Start-Containers @("caddy") "Caddy")) {
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}
& docker @($compose + @("--profile", "web", "up", "-d")) 2>&1 | Out-Null
Say "watching..."
$res = Watch-Caddy 180

# Again: verify by asking the server, not by reading logs.
Write-Host ""
Say "Reading the certificate each site is actually serving..."
$good = 0
foreach ($d in $DOMAINS) {
    $c = $null
    try { $c = Get-ServedCert -Domain $d } catch { }
    if ($null -eq $c) {
        Bad "$d - nothing served on 443"
    } elseif ($c.Issuer -match "STAGING") {
        Bad "$d - still the STAGING certificate" "browsers will warn; re-run this stage"
    } elseif ($c.Issuer -match "Caddy Local Authority") {
        Bad "$d - self-signed fallback" "Let's Encrypt could not reach this machine"
    } else {
        $days = [int]([math]::Floor(($c.NotAfter - (Get-Date)).TotalDays))
        Ok "$d - real certificate" "expires $($c.NotAfter.ToString('d MMM yyyy')) ($days days)"
        $good++
    }
}
if ($good -lt $DOMAINS.Count) {
    Write-Host ""
    Bad "not every site has a real certificate yet"
    Say "The exact ACME error:  logs.cmd caddy"
    Say "Then re-check any time with:  certs.cmd"
    Say ""
    Say "Do not loop on this. You get five real attempts per domain per week and"
    Say "failures count. Debug against staging instead:  websites.cmd -Certs"
}
}

# --- done --------------------------------------------------------------------
Head "Live"
foreach ($d in $DOMAINS) { Write-Host "  https://$d" -ForegroundColor Green }
Write-Host ""
Say "Test from your phone with wifi OFF. From inside the house a router can"
Say "fail to loop traffic back to itself, which looks like a broken site and isn't."
Write-Host ""
Say "Renewal is automatic and forever. There is no certbot and no cron job."
Write-Host ""
Read-Host "  Press Enter to close"
