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
#    (The NUC relay forwards internet 48396/45729 here, and the NUC is on the LAN,
#    so LAN scope covers the relay too.)
$lan = "192.168.1.0/24"
$rules = @(
    @{Name="ai-stack web 48396";    Port=48396},
    @{Name="ai-stack web 45729";   Port=45729},
    @{Name="ai-stack legacy 58806-58808"; Port="58806-58808"},   # LAN-only now
    @{Name="ai-stack ollama 27324";     Port=27324},
    @{Name="ai-stack voice 48928";       Port=48928},
    @{Name="ai-stack litellm 44897";     Port=44897}
)
foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) { $existing | Remove-NetFirewallRule }
    New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $r.Port -RemoteAddress $lan | Out-Null
    Write-Host ("allowed  {0}  from LAN" -f $r.Name)
}
# HTTP/3 arrives straight from the router as UDP 45729 (not via the NUC relay),
# so this one cannot be LAN-scoped.
$existing = Get-NetFirewallRule -DisplayName "ai-stack quic 45729" -ErrorAction SilentlyContinue
if ($existing) { $existing | Remove-NetFirewallRule }
New-NetFirewallRule -DisplayName "ai-stack quic 45729" -Direction Inbound -Action Allow `
    -Protocol UDP -LocalPort 45729 | Out-Null
Write-Host "allowed  ai-stack quic 45729 (UDP, any source - router forwards it directly)"

Write-Host ""
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table
Write-Host "Done. Default inbound is Block everywhere; the stack is LAN-scoped."
