' pc_health_log.vbs - runs pc_health_log.ps1 with no window, so the every-minute task never flashes a console.
' Started by the Windows task "PC health log" (2026-11-17).
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""D:\scratchpad\tools\pc_health_log.ps1""", 0, False
