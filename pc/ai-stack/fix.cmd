@echo off
rem fix -- restart Docker on SuperNova from anywhere (SSH or a local prompt). See fix-run.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Jasons New Site\www\ai-stack\fix-run.ps1" %*
