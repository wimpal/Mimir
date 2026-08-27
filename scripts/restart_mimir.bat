@echo off
REM Full restart of Mimir brain + TUI (Windows).
REM Prefer: powershell -File scripts\restart_mimir.ps1
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_mimir.ps1" %*
