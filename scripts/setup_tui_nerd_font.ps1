# One-time Windows Terminal setup for TUI Nerd Font icons (T-026 mic button).
#
# Usage (from repo root):
#   powershell -File scripts/setup_tui_nerd_font.ps1
#   powershell -File scripts/setup_tui_nerd_font.ps1 -SkipInstall
#
# Installs JetBrainsMono Nerd Font (winget) if missing, prints WT settings,
# then runs probe_tui_icons.py.

param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# The installed font's Windows family name (not its filename).
$FontFace = "JetBrainsMono NFM"
$WingetId = "DEVCOM.JetBrainsMonoNerdFont"

function Test-NerdFontInstalled {
    $fontsDir = Join-Path $env:WINDIR "Fonts"
    $found = Get-ChildItem -Path $fontsDir -Filter "*JetBrains*Mono*Nerd*" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    return [bool]$found
}

if (-not $SkipInstall) {
    if (Test-NerdFontInstalled) {
        Write-Host "JetBrainsMono Nerd Font already present in $env:WINDIR\Fonts"
    }
    else {
        Write-Host "Installing $WingetId via winget..."
        winget install --id $WingetId --accept-package-agreements --accept-source-agreements
    }
}
else {
    Write-Host "Skipping font install (-SkipInstall)."
}

Write-Host ""
Write-Host "Windows Terminal: set profile font (Settings, Appearance, Font face):"
Write-Host "  $FontFace"
Write-Host ""
Write-Host "Or in settings.json under your PowerShell profile:"
Write-Host ('  "font": { "face": "' + $FontFace + '" }')
Write-Host ""
Write-Host "Restart the terminal, then run:"
Write-Host "  uv run python scripts/probe_tui_icons.py"
Write-Host ""
Write-Host "ASCII fallback (no Nerd Font): set MIMIR_TUI_ICON_MODE=text"
Write-Host ""

uv run python scripts/probe_tui_icons.py
