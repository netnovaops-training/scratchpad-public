<#
    What certificate is each site actually serving, right now?

    WHY THIS EXISTS
    ===============
    websites.cmd used to decide whether certificates worked by watching Caddy's
    log for a phrase. That was wrong twice over: a log format can change under
    you, and "I didn't see the phrase" is not the same as "it failed" -- but it
    printed like one, and then the next line cheerfully offered to spend real
    certificate attempts anyway.

    This asks the only question that has a real answer: open a TLS connection
    and read the certificate that comes back. No log parsing, no guessing.

    HOW IT ASKS
    -----------
    It connects to 127.0.0.1:443 and sets the SNI hostname by hand. That matters
    for two reasons:

      * It works from inside your house. Many routers -- Google Wifi among them
        -- will not loop a connection back to your own public IP, so browsing to
        your own domain from your desktop can fail while the site is perfectly
        fine for everyone else. Going straight to loopback skips that entirely.

      * SNI is what tells Caddy which of your three sites you are asking for, so
        each one is checked individually rather than whichever answers first.

        certs.cmd
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say  { param($m) Write-Host "         $m" -ForegroundColor DarkGray }

$DOMAINS = @("example.org", "teewithmsdee.example.org", "peeweelegacy.ddns.net")

Write-Host ""
Write-Host "  Certificates" -ForegroundColor Yellow
Write-Host "  ------------" -ForegroundColor DarkGray

# -----------------------------------------------------------------------------
#  Fetch the certificate one site is serving, by name.
# -----------------------------------------------------------------------------
function Get-ServedCert {
    param([string]$Domain, [string]$Ip = "127.0.0.1", [int]$Port = 443)

    $client = $null
    $ssl    = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Ip, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(6000)) {
            throw "nothing answered on ${Ip}:${Port} within 6 seconds"
        }
        $client.EndConnect($iar)

        # Accept ANY certificate. We are inspecting it, not trusting it -- a
        # staging or self-signed cert is a result worth reporting, not an error
        # to swallow.
        $accept = [System.Net.Security.RemoteCertificateValidationCallback] {
            param($sender, $certificate, $chain, $errors) return $true
        }
        $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $accept)

        # TLS 1.2 named explicitly. Windows PowerShell 5.1 still defaults to a
        # protocol list Caddy will refuse, which would look like "no certificate"
        # when the real answer is "we could not agree on a handshake".
        $ssl.AuthenticateAsClient($Domain, $null,
            [System.Security.Authentication.SslProtocols]::Tls12, $false)

        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 `
            -ArgumentList (,$ssl.RemoteCertificate.GetRawCertData())
    }
    finally {
        if ($ssl)    { $ssl.Dispose() }
        if ($client) { $client.Close() }
    }
}

# -----------------------------------------------------------------------------
#  Is Caddy even up?
# -----------------------------------------------------------------------------
Head "The web server"
$ps = & docker compose -f docker-compose.yml -f docker-compose.web.yml ps caddy 2>&1 | Out-String
if ($ps -match "\bUp\b|running") {
    Ok "caddy is running"
} else {
    Bad "caddy is not running" "nothing below can succeed until it is"
    Say "start it:  docker compose -f docker-compose.yml -f docker-compose.web.yml --profile web up -d"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

# -----------------------------------------------------------------------------
#  The certificates themselves
# -----------------------------------------------------------------------------
Head "What each site is serving"

$real = 0; $staging = 0; $internal = 0; $broken = 0

foreach ($d in $DOMAINS) {
    try {
        $c = Get-ServedCert -Domain $d
    } catch {
        $broken++
        Bad "$d" $_.Exception.Message
        continue
    }

    $issuer = $c.Issuer
    $days   = [int]([math]::Floor(($c.NotAfter - (Get-Date)).TotalDays))
    $detail = "expires $($c.NotAfter.ToString('d MMM yyyy')) ($days days) - issuer: $issuer"

    # Order matters: the staging issuer ALSO contains "Let's Encrypt".
    if ($issuer -match "STAGING") {
        $staging++
        Warn "$d - STAGING certificate" $detail
        Say "Browsers will show a warning. This proves the chain works, but it"
        Say "is not a public certificate. Run:  websites.cmd -Real"
    }
    elseif ($issuer -match "Caddy Local Authority|CN=$([regex]::Escape($d))") {
        $internal++
        Bad "$d - self-signed fallback" $detail
        Say "Caddy could not get a public certificate, so it issued its own so"
        Say "the site would still serve. This is the 'it failed' result."
    }
    elseif ($issuer -match "Let's Encrypt|ISRG|\bR1[01]\b|\bE[56]\b") {
        $real++
        if ($days -lt 21) {
            Warn "$d - real certificate, renewing soon" $detail
        } else {
            Ok "$d - real Let's Encrypt certificate" $detail
        }
    }
    else {
        Warn "$d - unrecognised issuer" $detail
    }
}

# -----------------------------------------------------------------------------
#  The verdict, stated plainly
# -----------------------------------------------------------------------------
Head "Verdict"

if ($real -eq $DOMAINS.Count) {
    Ok "all three sites have real, publicly trusted certificates"
    Say "Nothing further to do. Caddy renews these on its own at about 30 days"
    Say "remaining, restarts nothing, and needs no cron job or scheduled task."
    Say ""
    Say "The one thing worth doing: back up the certificates."
    Say "  docker run --rm -v ai-stack_caddy_data:/d -v `"$PSScriptRoot`":/b alpine tar czf /b/caddy-certs-backup.tgz -C /d ."
}
elseif ($staging -gt 0 -and $internal -eq 0 -and $broken -eq 0) {
    Warn "staging certificates are in place - the chain WORKS"
    Say "Port 80 reached this PC from the internet and the challenge passed."
    Say "That is the hard part, and it is done. Get the real ones:"
    Say ""
    Say "    websites.cmd -Real"
}
elseif ($internal -gt 0) {
    Bad "Let's Encrypt could not reach this machine"
    Say "Caddy fell back to self-signed certificates so the sites still serve."
    Say ""
    Say "In order of likelihood:"
    Say "  1. Your router still forwards 80/443 to the old Pi, not 192.168.1.205."
    Say "  2. Windows Firewall is blocking 80/443.   Run:  firewall.cmd"
    Say "  3. Your ISP blocks inbound port 80 on residential lines."
    Say ""
    Say "Test 1 and 2 from your PHONE with wifi OFF:"
    Say "    http://example.org"
    Say "If that does not load, the problem is in front of Caddy, not in it."
    Say ""
    Say "See the exact ACME error:"
    Say "    logs.cmd caddy"
}
else {
    Bad "could not read a certificate from at least one site"
    Say "logs.cmd caddy"
}

Write-Host ""
Say "Rate limit reminder: 5 real certificates per domain per week, and FAILED"
Say "attempts count. If you are debugging, debug against staging - it is free."

Write-Host ""
Read-Host "  Press Enter to close"
