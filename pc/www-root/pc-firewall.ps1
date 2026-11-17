# =============================================================================
#  SuperNova firewall: confirm default-deny inbound on every profile, then
#  allow only what this machine actually serves -- and only to the LAN.
#
#  Run FROM AN ADMINISTRATOR PowerShell:
#      powershell -ExecutionPolicy Bypass -File "C:\Jasons New Site\www\pc-firewall.ps1"
#
#  What this deliberately does NOT do: delete Windows' own app rules one by
#  one. Default-deny inbound means an app with no allow rule is unreachable
#  already; stripping hundreds of stock rules breaks printers and casting
#  and produces a machine that fights its owner. This sets the posture and
#  adds explicit LAN-scoped allows for the stack's own listeners.
# =============================================================================

# 1. Default deny inbound, allow outbound, firewall ON, all three profiles.
Set-NetFirewallProfile -Profile Domain,Private,Public `
    -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow

# 2. The stack's listeners, reachable from the LAN only.
#    (The NUC relay forwards internet 27782/35573 here, and the NUC is on the LAN,
#    so LAN scope covers the relay too.)
$lan = "192.168.1.0/24"
$rules = @(
    @{Name="ai-stack web 27782";    Port=27782},
    @{Name="ai-stack web 35573";   Port=35573},
    @{Name="ai-stack legacy 50603-50605"; Port="50603-50605"},   # LAN-only now
    @{Name="ai-stack ollama 55827";     Port=55827},
    @{Name="ai-stack voice 58469";       Port=58469},
    @{Name="ai-stack litellm 31380";     Port=31380}
)
foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) { $existing | Remove-NetFirewallRule }
    New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $r.Port -RemoteAddress $lan | Out-Null
    Write-Host ("allowed  {0}  from LAN" -f $r.Name)
}
# HTTP/3 arrives straight from the router as UDP 35573 (not via the NUC relay),
# so this one cannot be LAN-scoped.
$existing = Get-NetFirewallRule -DisplayName "ai-stack quic 35573" -ErrorAction SilentlyContinue
if ($existing) { $existing | Remove-NetFirewallRule }
New-NetFirewallRule -DisplayName "ai-stack quic 35573" -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort 35573 | Out-Null
Write-Host "allowed  ai-stack quic 35573 (UDP, any source - router forwards it directly)"

Write-Host ""
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table
Write-Host "Done. Default inbound is Block everywhere; the stack is LAN-scoped."
