# Build a Windows Mimir desktop GUI exe (Tauri 2) for taskbar pinning.
#
# Usage (from repo root):
#   powershell -File scripts/build_mimir_desktop_exe.ps1
#
# Output: dist/mimir-desktop.exe  (stable path -- pin this, not target/release)
# Close any running mimir-desktop.exe first so the file is not locked.
# Icons come from clients/desktop/src-tauri/icons/ (regenerate with:
#   cd clients/desktop; npm run tauri -- icon ..\tui\assets\mimir-icon.png)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$desktopDir = "clients\desktop"
if (-not (Test-Path (Join-Path $desktopDir "package.json"))) {
  throw "Missing desktop client at $desktopDir"
}

$iconIco = Join-Path $desktopDir "src-tauri\icons\icon.ico"
if (-not (Test-Path $iconIco)) {
  throw "Missing icon: $iconIco - run: cd clients\desktop; npm run tauri -- icon ..\tui\assets\mimir-icon.png"
}

Write-Host "Building Tauri release (clients/desktop)..."
Push-Location $desktopDir
try {
  npm run tauri build
  if ($LASTEXITCODE -ne 0) {
    throw "tauri build failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}

$built = @(
  (Join-Path $desktopDir "src-tauri\target\release\mimir-desktop.exe"),
  (Join-Path $desktopDir "src-tauri\target\release\Mimir.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $built) {
  throw "Build succeeded but no release exe found under src-tauri\target\release\"
}

New-Item -ItemType Directory -Force -Path "dist" | Out-Null
$out = "dist\mimir-desktop.exe"
Copy-Item -Path $built -Destination $out -Force

$exe = Get-Item $out
Write-Host ""
Write-Host ("Done: {0} ({1} MB)" -f $exe.FullName, [math]::Round($exe.Length/1MB, 1))
Write-Host "Pin this file to the taskbar. Icon: $iconIco"
