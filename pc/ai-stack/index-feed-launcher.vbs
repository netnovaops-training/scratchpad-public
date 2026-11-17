' Starts the feed with no console window. See index-feed.ps1 for why.
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Jasons New Site\www\ai-stack\index-feed.ps1""", 0, False
