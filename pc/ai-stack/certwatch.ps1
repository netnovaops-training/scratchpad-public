<#
    Watch the certificates. Prove renewal is happening instead of trusting it.

    WHAT THIS IS FOR
    ================
    Caddy renews certificates on its own, at about 30 days remaining, forever.
    That is genuinely automatic and there is nothing to schedule.

    This does not renew anything. It CHECKS -- so that if the automatic renewal
    ever stops working, you find out with a month of headroom instead of finding
    out from a customer on day 91.

    The failure it exists to catch is silence: a certificate that quietly stops
    renewing looks exactly like one that is renewing fine, right up until it
    doesn't. Thirty days of warning is the difference between an afternoon and
    an emergency.

    HOW IT CHECKS
    -------------
    Opens a TLS connection to 127.0.0.1:443 and sets the SNI hostname by hand,
    then reads the certificate that comes back. That is the same thing a visitor
    would receive, so there is no gap between "what the config says" and "what is
    being served". Going to loopback rather than the public name means it also
    works from inside your house, where many routers refuse to loop a connection
    back to your own public IP.

        certwatch.cmd              check now, show everything
        certwatch.cmd -Quiet       only speak up if something is wrong
        certwatch.cmd -Install     run it every Monday at 9am, automatically
        certwatch.cmd -Uninstall   stop doing that
#>

param(
    [switch]$Quiet,
    [switch]$Install,
    [switch]$Uninstall,
    [int]$WarnDays = 21
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$DOMAINS  = @("example.org", "teewithmsdee.example.org", "peeweelegacy.ddns.net")
$TASKNAME = "ai-stack certificate watch"

function Ok   { param($m,$d="") if(-not $Quiet){Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray}} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) if(-not $Quiet){Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan} }
function Say  { param($m) if(-not $Quiet){Write-Host "         $m" -ForegroundColor DarkGray} }

# -----------------------------------------------------------------------------
#  Install / uninstall the scheduled task
# -----------------------------------------------------------------------------
if ($Install -or $Uninstall) {
    $admin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Write-Host ""
        Bad "this needs Administrator" "right-click certwatch.cmd -> Run as administrator"
        Write-Host ""; Read-Host "  Press Enter to close"; exit 1
    }

    Unregister-ScheduledTask -TaskName $TASKNAME -Confirm:$false -ErrorAction SilentlyContinue

    if ($Uninstall) {
        Write-Host ""; Ok "weekly check removed"
        Write-Host ""; Read-Host "  Press Enter to close"; exit 0
    }

    # Weekly, not daily. A certificate problem does not develop in hours, and a
    # daily task is a daily chance to be ignored.
    $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\certwatch.ps1`" -Quiet" `
        -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
    $set     = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    Register-ScheduledTask -TaskName $TASKNAME -Action $action -Trigger $trigger `
        -Settings $set -Description "Checks the TLS certificates ai-stack serves and warns before one expires." `
        -RunLevel Limited | Out-Null

    Write-Host ""
    Ok "installed" "runs every Monday 9am, and catches up if the PC was off"
    Say "It only speaks up when something is wrong."
    Say "Remove it with:  certwatch.cmd -Uninstall"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 0
}

# -----------------------------------------------------------------------------
#  Read the certificate a given site is serving
# -----------------------------------------------------------------------------
function Get-ServedCert {
    param([string]$Domain)
    $client = $null; $ssl = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", 443, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(8000)) { throw "no answer on 127.0.0.1:443" }
        $client.EndConnect($iar)
        $accept = [System.Net.Security.RemoteCertificateValidationCallback] {
            param($sender, $certificate, $chain, $errors) return $true
        }
        $ssl = New-Object System.Net.Security.SslStream($client.GetStream(), $false, $accept)
        $ssl.AuthenticateAsClient($Domain, $null,
            [System.Security.Authentication.SslProtocols]::Tls12, $false)
        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 `
            -ArgumentList (,$ssl.RemoteCertificate.GetRawCertData())
    } finally {
        if ($ssl) { $ssl.Dispose() }
        if ($client) { $client.Close() }
    }
}

# -----------------------------------------------------------------------------
#  Telegram, for when nobody is looking at the screen
# -----------------------------------------------------------------------------
function Send-Telegram {
    param([string]$Text)
    if (-not (Test-Path ".env")) { return }
    $token = ""; $ids = ""
    foreach ($line in Get-Content ".env") {
        if ($line -match '^TELEGRAM_BOT_TOKEN=(.+)$')        { $token = $Matches[1].Trim() }
        if ($line -match '^TELEGRAM_ALLOWED_USER_IDS=(.+)$')  { $ids   = $Matches[1].Trim() }
    }
    if (-not $token -or -not $ids) { return }
    foreach ($id in ($ids -split ",")) {
        $id = $id.Trim(); if (-not $id) { continue }
        try {
            Invoke-RestMethod -Method Post -TimeoutSec 20 `
                -Uri "https://api.telegram.org/bot$token/sendMessage" `
                -Body @{ chat_id = $id; text = $Text; disable_web_page_preview = "true" } | Out-Null
        } catch {
            # A failed notification must never be the thing that breaks the
            # check. The screen output above is still the real result.
            Warn "couldn't send the Telegram alert" $_.Exception.Message
        }
    }
}

# -----------------------------------------------------------------------------
#  The check
# -----------------------------------------------------------------------------
if (-not $Quiet) {
    Write-Host ""
    Write-Host "  Certificate watch" -ForegroundColor Yellow
    Write-Host "  -----------------" -ForegroundColor DarkGray
}

Head "What each site is serving"

$problems = @()
$soonest  = 99999

foreach ($d in $DOMAINS) {
    $c = $null
    try { $c = Get-ServedCert -Domain $d } catch {
        $problems += "$d : not serving TLS at all ($($_.Exception.Message))"
        Bad "$d" $_.Exception.Message
        continue
    }

    $days   = [int]([math]::Floor(($c.NotAfter - (Get-Date)).TotalDays))
    $issuer = $c.Issuer
    $when   = $c.NotAfter.ToString("d MMM yyyy")

    if ($issuer -match "STAGING") {
        $problems += "$d : STAGING certificate - browsers will warn. Run websites.cmd -Real"
        Bad "$d - staging certificate" "expires $when ($days days)"
    }
    elseif ($issuer -match "Caddy Local Authority") {
        $problems += "$d : self-signed fallback - Let's Encrypt could not reach this machine"
        Bad "$d - self-signed fallback" "ACME is failing; logs.cmd caddy"
    }
    elseif ($days -lt 0) {
        $problems += "$d : EXPIRED $($days * -1) days ago"
        Bad "$d - EXPIRED" "expired $when"
    }
    elseif ($days -lt $WarnDays) {
        $problems += "$d : only $days days left (expires $when) - renewal may have stopped"
        Warn "$d - $days days left" "expires $when; Caddy should have renewed by now"
    }
    else {
        Ok "$d" "$days days left, expires $when"
    }
    if ($days -lt $soonest) { $soonest = $days }
}

# -----------------------------------------------------------------------------
#  Verdict
# -----------------------------------------------------------------------------
if ($problems.Count -eq 0) {
    Head "Verdict"
    Ok "all three certificates are healthy"
    Say "Soonest expiry is $soonest days away. Caddy renews at about 30 days"
    Say "remaining, so the number above should never fall below roughly 30."
    Say ""
    Say "Run this any time, or install the weekly check:  certwatch.cmd -Install"
    if (-not $Quiet) { Write-Host ""; Read-Host "  Press Enter to close" }
    exit 0
}

# Something is wrong. Say so on screen AND on the phone, because the whole point
# of the scheduled run is that nobody is watching the screen.
Head "Needs attention"
foreach ($p in $problems) { Write-Host "  - $p" -ForegroundColor Yellow }

$msg = "ai-stack certificate warning`n`n" + ($problems -join "`n") +
       "`n`nChecked $(Get-Date -Format 'ddd d MMM HH:mm'). Run certs.cmd on the PC for detail."
Send-Telegram $msg

if (-not $Quiet) {
    Write-Host ""
    Say "Detail:  certs.cmd"
    Say "Caddy's own account of it:  logs.cmd caddy"
    Write-Host ""; Read-Host "  Press Enter to close"
}
exit 1
