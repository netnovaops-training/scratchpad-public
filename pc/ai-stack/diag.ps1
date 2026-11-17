<#
    Collect everything needed to work out why the sites aren't answering, into
    one file you can paste back.

    It writes  diagnostics.txt  next to this script and prints the same thing.

    NO SECRETS ARE INCLUDED. Where a key matters, it reports only whether it is
    set and how long it is -- never the value. Read the file before you send it
    if you want to be sure; that is a reasonable thing to want.

        diag.cmd
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$out  = Join-Path $PSScriptRoot "diagnostics.txt"
$lines = New-Object System.Collections.ArrayList

function W { param($m = "")
    [void]$lines.Add($m)
    Write-Host $m
}
function Section { param($m)
    W ""
    W "================================================================"
    W "  $m"
    W "================================================================"
}

$compose = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.web.yml")
$DOMAINS = @("example.org", "teewithmsdee.example.org", "peeweelegacy.ddns.net")

W "ai-stack diagnostics"
W "generated $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# -----------------------------------------------------------------------------
Section "1. Is Docker up?"
$dv = & docker version --format "{{.Server.Version}}" 2>&1 | Out-String
W "docker server: $($dv.Trim())"
if ($LASTEXITCODE -ne 0) {
    W "*** Docker Desktop is not running. Nothing below will be meaningful. ***"
}

# -----------------------------------------------------------------------------
Section "2. Containers"
W (& docker @($compose + @("ps", "-a")) 2>&1 | Out-String)

# -----------------------------------------------------------------------------
Section "3. Caddy log (last 120 lines)"
# This is where the real ACME error lives. Everything else is context.
W (& docker @($compose + @("logs", "--tail", "120", "caddy")) 2>&1 | Out-String)

# -----------------------------------------------------------------------------
Section "4. What owns ports 80 and 443 on this PC?"
foreach ($p in 80, 443) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        W "port ${p}: NOTHING LISTENING"
    } else {
        foreach ($c in $conns) {
            $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
            W "port ${p}: $($c.LocalAddress) held by $($proc.ProcessName) (pid $($c.OwningProcess))"
        }
    }
}

# IIS specifically -- it reserves URLs even when the site is stopped.
W ""
W "HTTP.sys URL reservations (IIS lives here):"
$svc = & netsh http show servicestate view=requestq 2>&1 | Out-String
$hits = ($svc -split "`n" | Select-String -Pattern "Request queue name|HTTP://" | Select-Object -First 12)
if ($hits) { $hits | ForEach-Object { W "  $_" } } else { W "  none" }

# -----------------------------------------------------------------------------
Section "5. Windows Firewall"
$rules = Get-NetFirewallRule -Group "AI Stack" -ErrorAction SilentlyContinue
if (-not $rules) {
    W "NO 'AI Stack' firewall rules exist. Run firewall.cmd."
} else {
    foreach ($r in $rules) {
        $pf = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        W "$($r.DisplayName): enabled=$($r.Enabled) action=$($r.Action) port=$($pf.LocalPort)/$($pf.Protocol)"
    }
}

# -----------------------------------------------------------------------------
Section "6. Can this PC talk to Caddy at all?"
# Straight to loopback. This does NOT involve your router, your ISP, DNS or
# Let's Encrypt. If this fails, the problem is entirely local and nothing about
# certificates matters yet.
foreach ($p in 80, 443) {
    try {
        $t = New-Object System.Net.Sockets.TcpClient
        $iar = $t.BeginConnect("127.0.0.1", $p, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(4000)) {
            $t.EndConnect($iar); W "127.0.0.1:${p}  CONNECTED"
        } else { W "127.0.0.1:${p}  no answer in 4s" }
        $t.Close()
    } catch { W "127.0.0.1:${p}  $($_.Exception.Message)" }
}

W ""
W "HTTP on loopback (expect a 308 redirect to https):"
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1/" -MaximumRedirection 0 `
         -UseBasicParsing -TimeoutSec 8 -ErrorAction SilentlyContinue
    W "  status $($r.StatusCode)  location: $($r.Headers.Location)"
} catch {
    $resp = $_.Exception.Response
    if ($resp) { W "  status $([int]$resp.StatusCode)  location: $($resp.Headers['Location'])" }
    else { W "  $($_.Exception.Message)" }
}

# -----------------------------------------------------------------------------
Section "7. What certificate is each site serving?"
foreach ($d in $DOMAINS) {
    $client = $null; $ssl = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", 443, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(6000)) { throw "no answer on 127.0.0.1:443" }
        $client.EndConnect($iar)
        $accept = [System.Net.Security.RemoteCertificateValidationCallback] {
            param($sender, $certificate, $chain, $errors) return $true
        }
        $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $accept)
        $ssl.AuthenticateAsClient($d, $null,
            [System.Security.Authentication.SslProtocols]::Tls12, $false)
        $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 `
             -ArgumentList (,$ssl.RemoteCertificate.GetRawCertData())
        W "$d"
        W "    issuer:  $($c.Issuer)"
        W "    subject: $($c.Subject)"
        W "    expires: $($c.NotAfter)"
    } catch {
        W "$d"
        W "    FAILED: $($_.Exception.Message)"
    } finally {
        if ($ssl) { $ssl.Dispose() }
        if ($client) { $client.Close() }
    }
}

# -----------------------------------------------------------------------------
Section "8. DNS and public IP"
try {
    $public = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 10).ip
    W "this house looks like: $public"
} catch { $public = $null; W "could not determine public IP" }

foreach ($d in $DOMAINS) {
    try {
        $ip = (Resolve-DnsName -Name $d -Type A -ErrorAction Stop |
               Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
        $note = if ($public -and $ip -eq $public) { "matches" } else { "DOES NOT MATCH public IP" }
        W "$d -> $ip   ($note)"
    } catch { W "$d -> does not resolve" }
}

W ""
W "this PC's LAN addresses (the router must forward 80/443 to one of these):"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object { W "  $($_.IPAddress)  ($($_.InterfaceAlias))" }

# -----------------------------------------------------------------------------
Section "9. Settings (names and lengths only, never values)"
if (Test-Path ".env") {
    foreach ($k in @("ACME_EMAIL","PUBLIC_HOST","ZEE_SECRET_KEY","MOM_SECRET_KEY",
                     "BOOK_SECRET_KEY","LITELLM_MASTER_KEY","TWILIO_AUTH_TOKEN")) {
        $line = Get-Content ".env" | Where-Object { $_ -like "$k=*" } | Select-Object -First 1
        if (-not $line) { W "$k : not present" ; continue }
        $v = $line.Substring($k.Length + 1)
        if ($k -eq "ACME_EMAIL" -or $k -eq "PUBLIC_HOST") { W "$k : $v" }
        elseif ([string]::IsNullOrWhiteSpace($v))         { W "$k : EMPTY" }
        else                                              { W "$k : set ($($v.Length) chars)" }
    }
} else { W ".env is MISSING" }

# -----------------------------------------------------------------------------
Section "10. Caddyfile as Caddy actually parses it"
$adapt = & docker run --rm -e ACME_EMAIL=diag@example.com `
    -v "${PSScriptRoot}\web\Caddyfile:/etc/caddy/Caddyfile:ro" `
    caddy:2-alpine caddy adapt --config /etc/caddy/Caddyfile 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) { W "Caddyfile parses cleanly." }
else { W "CADDYFILE PROBLEM:"; W $adapt }

# -----------------------------------------------------------------------------
Set-Content -Path $out -Value ($lines -join "`r`n") -Encoding UTF8
Write-Host ""
Write-Host "  Written to: $out" -ForegroundColor Green
Write-Host "  No secrets are in it - open it and check if you like." -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close"
