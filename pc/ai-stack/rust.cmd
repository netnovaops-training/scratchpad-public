@echo off
REM Install Rust on this PC, so you can edit the sites with a two-second
REM rebuild instead of a Docker one.
REM
REM NOT required to run anything - rust\Dockerfile carries its own compiler.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rust-setup.ps1" %*
