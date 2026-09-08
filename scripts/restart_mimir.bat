@echo off
REM Full restart of Mimir brain + desktop GUI (Windows).
REM Prefer: powershell -File scripts\restart_mimir.ps1
REM Flags: -BrainOnly | -NoGui | -WithTui | -SkipExeBuild
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_mimir.ps1" %*
