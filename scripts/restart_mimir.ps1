# Full restart of Mimir brain + desktop GUI (Windows).
#
# Usage (from repo root or anywhere):
#   powershell -File scripts/restart_mimir.ps1
#   powershell -File scripts/restart_mimir.ps1 -BrainOnly
#   powershell -File scripts/restart_mimir.ps1 -Url http://127.0.0.1:8000
#   powershell -File scripts/restart_mimir.ps1 -NoGui
#   powershell -File scripts/restart_mimir.ps1 -WithTui
#   powershell -File scripts/restart_mimir.ps1 -WithTui -SkipExeBuild
#
# Default full restart: stop clients, start brain, open the Tauri desktop GUI
# (clients/desktop — Windows daily driver). Textual TUI is opt-in via -WithTui.
#
# -BrainOnly / -NoTui: brain only (no client window). -NoTui kept for older shortcuts.
# -NoGui: skip desktop GUI (useful with -WithTui for TUI-only).
# -WithTui: also rebuild/launch dist\mimir.exe (or uv run mimir).
#
# For login auto-start (T-016), use start_brain_at_login.ps1 instead of -BrainOnly —
# that script is idempotent and does not kill an existing healthy brain.
#
# -Url is the client health-check URL (default loopback). Uvicorn bind host/port
# come from config/config.yaml runtime.* via ensure_brain_cli (T-022).

[CmdletBinding()]
param(
  [string]$Url = $(if ($env:MIMIR_BRAIN_URL) { $env:MIMIR_BRAIN_URL } else { "http://127.0.0.1:8000" }),
  [switch]$BrainOnly,
  [switch]$NoTui,
  [switch]$NoGui,
  [switch]$WithTui,
  [switch]$SkipExeBuild,
  [int]$ReadyTimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
$env:MIMIR_REPO_ROOT = $RepoRoot

function Get-BrainPort {
  param([string]$BrainUrl)
  try {
    $uri = [Uri]$BrainUrl
    if ($uri.Port -gt 0) { return $uri.Port }
    if ($uri.Scheme -eq "https") { return 443 }
    return 80
  } catch {
    return 8000
  }
}

function Stop-ListenersOnPort {
  param([int]$Port)
  $pids = @()
  try {
    $pids = @(
      Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )
  } catch {
    # Fallback when Get-NetTCPConnection is unavailable
    $lines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    foreach ($line in $lines) {
      if ($line -match "\s(\d+)\s*$") {
        $pids += [int]$Matches[1]
      }
    }
    $pids = $pids | Select-Object -Unique
  }

  foreach ($procId in $pids) {
    if ($procId -le 4) { continue }
    try {
      $proc = Get-Process -Id $procId -ErrorAction Stop
      Write-Host "Stopping brain listener PID ${procId} ($($proc.ProcessName)) on :$Port"
      Stop-Process -Id $procId -Force -ErrorAction Stop
    } catch {
      Write-Host "Could not stop PID ${procId}: $($_.Exception.Message)"
    }
  }
}

function Stop-MimirTui {
  $targets = @()

  Get-Process -Name "mimir" -ErrorAction SilentlyContinue | ForEach-Object {
    $targets += $_
  }

  try {
    $cim = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.CommandLine -and (
          $_.CommandLine -match '(?i)(\brun mimir\b|python\s+-m\s+clients\.tui|clients\.tui\.app)'
        ) -and $_.CommandLine -notmatch '(?i)restart_mimir'
      }
    foreach ($row in $cim) {
      try {
        $targets += Get-Process -Id $row.ProcessId -ErrorAction Stop
      } catch { }
    }
  } catch { }

  $targets = $targets | Sort-Object Id -Unique
  foreach ($proc in $targets) {
    Write-Host "Stopping TUI PID $($proc.Id) ($($proc.ProcessName))"
    try {
      Stop-Process -Id $proc.Id -Force -ErrorAction Stop
    } catch {
      Write-Host "Could not stop TUI PID $($proc.Id): $($_.Exception.Message)"
    }
  }
}

function Stop-MimirDesktop {
  $targets = @()

  Get-Process -Name "mimir-desktop","Mimir" -ErrorAction SilentlyContinue | ForEach-Object {
    $targets += $_
  }

  try {
    $cim = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.CommandLine -and (
          $_.CommandLine -match '(?i)(mimir-desktop\.exe|dist[/\\]mimir-desktop|clients[/\\]desktop)'
        ) -and $_.CommandLine -notmatch '(?i)restart_mimir'
      }
    foreach ($row in $cim) {
      try {
        $targets += Get-Process -Id $row.ProcessId -ErrorAction Stop
      } catch { }
    }
  } catch { }

  $targets = $targets | Sort-Object Id -Unique
  foreach ($proc in $targets) {
    Write-Host "Stopping desktop GUI PID $($proc.Id) ($($proc.ProcessName))"
    try {
      Stop-Process -Id $proc.Id -Force -ErrorAction Stop
    } catch {
      Write-Host "Could not stop desktop GUI PID $($proc.Id): $($_.Exception.Message)"
    }
  }
}

function Test-BrainHealth {
  param([string]$BrainUrl)
  $health = ($BrainUrl.TrimEnd("/")) + "/health"
  try {
    $resp = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
    return ($resp.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Start-Brain {
  param([string]$BrainUrl, [int]$TimeoutSec = 60)
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "Could not find uv on PATH. Install uv or start the brain manually."
  }

  $logDir = Join-Path $RepoRoot "data\logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir "brain_launch.log"
  Add-Content -Path $logPath -Value "`n--- restart_mimir ensure_brain_running url=$BrainUrl ---"

  Write-Host "Starting brain via ensure_brain_cli (bind from config/config.yaml runtime.host)..."
  $cliLog = Join-Path $logDir "brain_restart_cli.log"
  $cliErrLog = Join-Path $logDir "brain_restart_cli.err.log"

  & uv run python scripts/ensure_brain_cli.py --url $BrainUrl --ready-timeout $TimeoutSec `
    1> $cliLog 2> $cliErrLog
  if ($LASTEXITCODE -ne 0) {
    throw "ensure_brain_cli exited $LASTEXITCODE. See data/logs/brain_launch.log"
  }

  if (-not (Test-BrainHealth $BrainUrl)) {
    throw "Brain not healthy at $BrainUrl after start. See data/logs/brain_launch.log"
  }
  Write-Host "Brain ready ($BrainUrl)."
}

function Build-MimirExe {
  $buildScript = Join-Path $PSScriptRoot "build_mimir_exe.ps1"
  if (-not (Test-Path $buildScript)) {
    throw "Missing build script: $buildScript"
  }
  Write-Host "Building dist\mimir.exe (PyInstaller)..."
  & $buildScript
  if ($LASTEXITCODE -ne 0) {
    throw "build_mimir_exe.ps1 failed with exit code $LASTEXITCODE"
  }
}

function Get-DesktopReleaseExe {
  # Prefer stable dist\ path for taskbar pins; fall back to cargo target.
  $candidates = @(
    (Join-Path $RepoRoot "dist\mimir-desktop.exe"),
    (Join-Path $RepoRoot "clients\desktop\src-tauri\target\release\mimir-desktop.exe"),
    (Join-Path $RepoRoot "clients\desktop\src-tauri\target\release\Mimir.exe")
  )
  foreach ($path in $candidates) {
    if (Test-Path $path) { return $path }
  }
  return $null
}

function Start-MimirDesktop {
  $desktopDir = Join-Path $RepoRoot "clients\desktop"
  if (-not (Test-Path (Join-Path $desktopDir "package.json"))) {
    throw "Desktop client missing at clients\desktop (T-045)."
  }

  $exe = Get-DesktopReleaseExe
  if ($exe) {
    Write-Host "Starting desktop GUI: $exe"
    Start-Process -FilePath $exe -WorkingDirectory $desktopDir
    return
  }

  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "No release mimir-desktop.exe and npm not on PATH. Run: powershell -File scripts/build_mimir_desktop_exe.ps1"
  }

  Write-Host "No release desktop exe found; starting npm run tauri dev (builds on first run)..."
  Write-Host "For a pinable taskbar exe: powershell -File scripts/build_mimir_desktop_exe.ps1"
  Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/k", "npm run tauri dev") `
    -WorkingDirectory $desktopDir
}

function Start-MimirTuiWindow {
  param([string]$BrainUrl, [bool]$PreferExe)
  $exePath = Join-Path $RepoRoot "dist\mimir.exe"
  if ($PreferExe -and (Test-Path $exePath)) {
    Write-Host "Starting TUI: $exePath"
    Start-Process -FilePath $exePath -WorkingDirectory $RepoRoot
  } else {
    if ($PreferExe) {
      Write-Host "dist\mimir.exe missing; falling back to uv run mimir."
    }
    Start-Process -FilePath "cmd.exe" `
      -ArgumentList @("/k", "uv run mimir --url $BrainUrl") `
      -WorkingDirectory $RepoRoot
  }
}

# --- main ---
# -NoTui kept as brain-only alias for older shortcuts / docs.
$doBrainOnly = $BrainOnly -or $NoTui
$startGui = (-not $doBrainOnly) -and (-not $NoGui)
$startTui = (-not $doBrainOnly) -and $WithTui
$fullClientRestart = $startGui -or $startTui

$port = Get-BrainPort $Url
Write-Host "Repo: $RepoRoot"
Write-Host "Brain URL: $Url (port $port)"
Write-Host "Clients: GUI=$(if ($startGui) { 'yes' } else { 'no' }) TUI=$(if ($startTui) { 'yes' } else { 'no' })"

$launchTuiExe = $false
$step = 1

Write-Host "`n[$step] Stopping brain on :${port}..."
$step++
Stop-ListenersOnPort -Port $port
Start-Sleep -Milliseconds 400

if ($fullClientRestart) {
  Write-Host "[$step] Stopping desktop GUI / TUI..."
  $step++
  Stop-MimirDesktop
  Stop-MimirTui
  Start-Sleep -Milliseconds 300

  if ($startTui) {
    if (-not $SkipExeBuild) {
      Write-Host "[$step] Rebuilding dist\mimir.exe..."
      $step++
      Build-MimirExe
      $launchTuiExe = $true
    } else {
      Write-Host "[$step] Skipping TUI exe build (-SkipExeBuild)."
      $step++
      $exePath = Join-Path $RepoRoot "dist\mimir.exe"
      $launchTuiExe = Test-Path $exePath
    }
  }
}

Write-Host "[$step] Starting brain..."
$step++
Start-Brain -BrainUrl $Url -TimeoutSec $ReadyTimeoutSec

if ($doBrainOnly) {
  Write-Host "Done (brain only)."
  exit 0
}

if ($startGui) {
  Write-Host "[$step] Starting desktop GUI..."
  $step++
  Start-MimirDesktop
}

if ($startTui) {
  Write-Host "[$step] Starting TUI in a new window..."
  Start-MimirTuiWindow -BrainUrl $Url -PreferExe $launchTuiExe
}

Write-Host "Done."
