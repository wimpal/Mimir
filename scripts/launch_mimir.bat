@echo off
REM Open a new console window and run the Mimir TUI (dev shortcut; needs uv).
REM Prefer dist\mimir.exe once built via scripts\build_mimir_exe.ps1.
cd /d "%~dp0.."
start "Mimir" cmd /k "uv run mimir %*"
