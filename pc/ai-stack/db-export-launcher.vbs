' Starts the export with no console window. See db-export.ps1 for why this
' file exists rather than -WindowStyle Hidden.
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Jasons New Site\www\ai-stack\db-export.ps1""", 0, False
