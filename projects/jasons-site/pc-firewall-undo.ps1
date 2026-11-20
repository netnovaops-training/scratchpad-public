# =============================================================================
#  UNDO for pc-firewall.ps1 -- removes every rule that script added and puts
#  the firewall profiles back to Windows' own defaults.
#
#  Run FROM AN ADMINISTRATOR PowerShell:
#      powershell -ExecutionPolicy Bypass -File "C:\Jasons New Site\www\pc-firewall-undo.ps1"
#
#  What "undo" means here, precisely:
#    1. Every rule pc-firewall.ps1 created (all named "ai-stack ...") is
#       deleted. Nothing else is touched -- Windows' stock rules were never
#       modified by the apply script, so there is nothing to put back.
#    2. The three profiles return to NotConfigured for the inbound default,
#       which is Windows' out-of-the-box state (and still resolves to Block
#       -- Windows has blocked unsolicited inbound by default since Vista).
#       The firewall itself stays ON; turning it off is not an undo, it is
#       a different and worse decision, so this script will not do it.
#
#  After running this the machine behaves as it did before pc-firewall.ps1:
#  the stack's ports answer according to whatever rules Docker Desktop and
#  the apps themselves maintain, same as always.
# =============================================================================

$removed = 0
Get-NetFirewallRule -DisplayName "ai-stack *" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host ("removing  {0}" -f $_.DisplayName)
    $_ | Remove-NetFirewallRule
    $removed++
}
if ($removed -eq 0) {
    Write-Host "no ai-stack rules found - either the apply script never ran, or this undo already did"
}

Set-NetFirewallProfile -Profile Domain,Private,Public `
    -Enabled True -DefaultInboundAction NotConfigured -DefaultOutboundAction NotConfigured

Write-Host ""
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table
Write-Host ("Done. Removed {0} rule(s); profiles back to Windows defaults; firewall still ON." -f $removed)
