# Build a Windows console Mimir.exe (double-click opens a terminal + TUI).
#
# Usage (from repo root):
#   powershell -File scripts/build_mimir_exe.ps1
#
# Output: dist/mimir.exe
# Close any running mimir.exe first so the file is not locked.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$icon = "clients\tui\assets\mimir.ico"
if (-not (Test-Path $icon)) {
  throw "Missing icon: $icon"
}

Write-Host "Ensuring PyInstaller is available…"
uv sync --group exe --inexact
uv pip install pyinstaller pillow

Write-Host "Building dist/mimir.exe…"
uv run python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name mimir `
  --icon $icon `
  --collect-all textual `
  --collect-all rich `
  --paths . `
  scripts/mimir_exe_entry.py

if (-not (Test-Path "dist\mimir.exe")) {
  throw "Build failed: dist\mimir.exe missing"
}

$exe = Get-Item "dist\mimir.exe"
Write-Host ""
Write-Host "Done: $($exe.FullName) ($([math]::Round($exe.Length/1MB,1)) MB)"
Write-Host "Window title: Mimir  ·  icon: $icon"
