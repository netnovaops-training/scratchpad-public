<#
    Open the Windows firewall for the web server -- and only for the web server.

    Double-click firewall.cmd. It asks for admin, because changing firewall
    rules requires it.

    -------------------------------------------------------------------------
    WHAT THIS OPENS, AND WHY IT IS ONLY TWO PORTS

    Everything in this stack publishes to 127.0.0.1 -- Ollama, the router,
    Open WebUI, the phone service, the booking tools. Ports bound to loopback
    are not reachable from your network or the internet no matter what the
    firewall says, so they need no rule and get none.

    Caddy is the single exception. It publishes 80 and 443 on all interfaces
    because that is what a web server is. So that is what this opens:

        TCP  80    Let's Encrypt's domain check, and the http -> https redirect
        TCP 443    the sites themselves
        UDP 443    HTTP/3 -- optional, faster on phones

    Nothing else. If you ever see a rule from this script for another port,
    something has changed that you should look at.
    -------------------------------------------------------------------------

    Undo everything:   firewall.cmd -Remove
    Check only:        firewall.cmd -CheckOnly
#>

param(
    [switch]$Remove,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Continue"

$pass = 0; $fail = 0; $warn = 0
function Ok   { param($m,$d="") $script:pass++; Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") $script:fail++; Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") $script:warn++; Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

# --- admin -------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin -and -not $CheckOnly) {
    Write-Host ""
    Write-Host "  Firewall rules need administrator. Re-launching..." -ForegroundColor Yellow
    $argList = @("-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`"")
    if ($Remove)    { $argList += "-Remove" }
    if ($CheckOnly) { $argList += "-CheckOnly" }
    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    } catch {
        Write-Host "  You declined the prompt. Nothing was changed." -ForegroundColor Red
        Read-Host "  Press Enter to close"
    }
    exit
}

Write-Host ""
Write-Host "  Firewall -- the two ports the web server needs" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------" -ForegroundColor DarkGray

$rules = @(
    @{ Name = "AI Stack - Caddy HTTP (80/tcp)";   Port = 80;  Proto = "TCP"
       Desc = "Let's Encrypt domain validation and the http->https redirect. Required." },
    @{ Name = "AI Stack - Caddy HTTPS (443/tcp)"; Port = 443; Proto = "TCP"
       Desc = "The websites themselves." },
    @{ Name = "AI Stack - Caddy HTTP/3 (443/udp)";Port = 443; Proto = "UDP"
       Desc = "HTTP/3. Optional; noticeably faster on phones." }
)

# --- remove ------------------------------------------------------------------
if ($Remove) {
    Head "Removing the rules"
    foreach ($r in $rules) {
        $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
        if ($existing) {
            Remove-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
            Ok "removed  $($r.Name)"
        } else {
            Warn "not present  $($r.Name)"
        }
    }
    Write-Host ""
    Write-Host "  Done. The sites will stop being reachable from outside." -ForegroundColor Yellow
    Write-Host "  Everything on 127.0.0.1 is unaffected." -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit
}

# --- the rules ---------------------------------------------------------------
Head "Inbound rules"
foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue

    if ($CheckOnly) {
        if ($existing -and $existing.Enabled -eq "True") { Ok "$($r.Proto) $($r.Port) is allowed" $r.Desc }
        elseif ($existing) { Warn "$($r.Proto) $($r.Port) rule exists but is DISABLED" "re-run without -CheckOnly" }
        else { Warn "$($r.Proto) $($r.Port) has no rule" $r.Desc }
        continue
    }

    try {
        if ($existing) {
            # Don't delete and recreate -- just make sure it says what it should.
            Set-NetFirewallRule -DisplayName $r.Name -Enabled True -Action Allow `
                -Direction Inbound -Profile Any -ErrorAction Stop
            Ok "updated  $($r.Proto) $($r.Port)" $r.Desc
        } else {
            New-NetFirewallRule -DisplayName $r.Name `
                -Description $r.Desc `
                -Direction Inbound -Action Allow -Enabled True `
                -Protocol $r.Proto -LocalPort $r.Port `
                -Profile Any -Group "AI Stack" -ErrorAction Stop | Out-Null
            Ok "created  $($r.Proto) $($r.Port)" $r.Desc
        }
    } catch {
        Bad "could not set the rule for $($r.Proto) $($r.Port)" $_.Exception.Message
    }
}

# --- who else wants these ports ----------------------------------------------
# A rule that allows traffic to a port another program already holds does
# nothing useful, and this is by far the most common reason Caddy won't start
# on Windows. IIS and the Windows "World Wide Web Publishing Service" both grab
# port 80 on install and neither announces it.
Head "Is anything already holding 80 or 443?"
foreach ($p in 80, 443) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Ok "port $p is free"
        continue
    }
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { "pid $($c.OwningProcess)" }
        if ($name -match "docker|com\.docker|vpnkit|wslrelay") {
            Ok "port $p is held by Docker" "$name - that's Caddy, this is correct"
        } else {
            Bad "port $p is held by '$name'" "Caddy cannot start until this releases it. IIS? Skype? Another web server?"
        }
    }
}

$w3svc = Get-Service -Name W3SVC -ErrorAction SilentlyContinue
if ($w3svc -and $w3svc.Status -eq "Running") {
    Bad "IIS (W3SVC) is running" "it owns port 80. Stop it:  Stop-Service W3SVC ; Set-Service W3SVC -StartupType Disabled"
}

# --- Docker ------------------------------------------------------------------
Head "Docker"
$dockerProc = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerProc) { Ok "Docker Desktop is running" }
else { Warn "Docker Desktop doesn't appear to be running" "start it, then run websites.cmd" }

# Docker Desktop adds its own rules for published ports. They coexist with
# these; ours are here so the behaviour is explicit and survives a Docker reset.
$dockerRules = Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like "*Docker Desktop*" -and $_.Direction -eq "Inbound" }
if ($dockerRules) { Ok "Docker Desktop has its own inbound rules too" "$($dockerRules.Count) of them - harmless" }

# --- the part the firewall cannot fix ----------------------------------------
Head "Your router, and your ISP"
Write-Host "  The firewall is only the second door. Traffic from the internet"  -ForegroundColor DarkGray
Write-Host "  has to get through your ROUTER first, and Windows cannot open"    -ForegroundColor DarkGray
Write-Host "  that for you."                                                    -ForegroundColor DarkGray
Write-Host ""

$lan = (Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" } |
        Select-Object -First 1)
if ($lan) {
    $ip = $lan.IPv4Address.IPAddress
    $gw = $lan.IPv4DefaultGateway.NextHop
    Ok "this PC is $ip on your network" "forward ports 80 and 443 to that address"
    Write-Host "         router admin page: http://$gw" -ForegroundColor DarkGray
    Warn "give this PC a STATIC or reserved IP in the router" "if $ip changes, the forward silently points at nothing"
} else {
    Warn "couldn't work out this PC's LAN address"
}

# Compare the public IP to what the domains resolve to. When these disagree,
# every certificate fails and nothing loads -- and the cause is invisible
# without checking exactly this.
try {
    $public = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 8).ip
    Ok "your public IP is $public"

    foreach ($h in "example.org","teewithmsdee.example.org","peeweelegacy.ddns.net") {
        try {
            $res = (Resolve-DnsName -Name $h -Type A -ErrorAction Stop |
                    Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
            if ($res -eq $public) { Ok "$h -> $res" "matches" }
            else { Bad "$h -> $res" "but you are $public. Install No-IP's DUC client, or certificates will fail." }
        } catch {
            Bad "$h does not resolve" "check the record at noip.com"
        }
    }
} catch {
    Warn "couldn't look up your public IP" "no internet, or ipify is down"
}

Write-Host ""
Write-Host "  Test from OUTSIDE, which is the only test that counts:" -ForegroundColor Cyan
Write-Host "    on your phone, with WIFI OFF, open  http://example.org" -ForegroundColor DarkGray
Write-Host "    (from inside your house it can fail for unrelated reasons - see WEBSITES.md)" -ForegroundColor DarkGray

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host "  $pass ok" -NoNewline -ForegroundColor Green
Write-Host "   $warn warnings" -NoNewline -ForegroundColor Yellow
Write-Host "   $fail problems" -ForegroundColor Red
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close"
