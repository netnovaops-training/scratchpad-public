' backlog_feed_push.vbs - runs push-feed-changed.sh (Git Bash) with no window, every 30 min.
' Started by the Windows task "Backlog feed push" (made 2026-11-17 by the to-do autopilot, T0970 step 2).
' Uses Git's bash by full path on purpose: plain "bash" on this PC is WSL (WindowsApps\bash.exe), which
' does not have the PC's ssh key for the NUC. The script copy lives in tools\backlog\push-feed-changed.sh;
' the live one is in D:\Garden for all\_local-data\pipeline\dash-rust\ (that is the one this runs).
CreateObject("WScript.Shell").Run """C:\Program Files\Git\bin\bash.exe"" -lc ""sh '/d/Garden for all/_local-data/pipeline/dash-rust/push-feed-changed.sh'""", 0, False
