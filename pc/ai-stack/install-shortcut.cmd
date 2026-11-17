@echo off
REM Put a Hermes shortcut with its icon on your Desktop and in the Start menu.
REM
REM   install-shortcut.cmd            create them
REM   install-shortcut.cmd -Plain     use the standard console, not Windows Terminal
REM   install-shortcut.cmd -Remove    take them away
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-shortcut.ps1" %*
