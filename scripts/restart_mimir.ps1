# Full restart of Mimir brain + TUI (Windows).
#
# Usage (from repo root or anywhere):
#   powershell -File scripts/restart_mimir.ps1
#   powershell -File scripts/restart_mimir.ps1 -BrainOnly
#   powershell -File scripts/restart_mimir.ps1 -Url http://127.0.0.1:8000
#   powershell -File scripts/restart_mimir.ps1 -SkipExeBuild
#
# Full restart (default) stops TUI, rebuilds dist\mimir.exe, starts brain, opens the exe.
# Use -SkipExeBuild for a faster dev loop (uv run mimir, no PyInstaller).
#
# For login auto-start (T-016), use start_brain_at_login.ps1 instead of -BrainOnly —
# that script is idempotent and does not kill an existing healthy brain.
#
# Stops listeners on the brain port, stops running TUI (uv run mimir / mimir.exe),
# rebuilds mimir.exe (unless -SkipExeBuild / -BrainOnly), starts a fresh brain,
# waits for /health, then opens the TUI in a new window.
#
# -Url is the client health-check URL (default loopback). Uvicorn bind host/port
# come from config/config.yaml runtime.* via ensure_brain_cli (T-022).

[CmdletBinding()]
param(
  [string]$Url = $(if ($env:MIMIR_BRAIN_URL) { $env:MIMIR_BRAIN_URL } else { "http://127.0.0.1:8000" }),
  [switch]$BrainOnly,
  [switch]$NoTui,
  [switch]$SkipExeBuild,
  [int]$ReadyTimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

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

function Wait-BrainReady {
  param([string]$BrainUrl, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (Test-BrainHealth $BrainUrl) {
      Write-Host "Brain ready ($BrainUrl)."
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Brain did not become ready within ${TimeoutSec}s. See data/logs/brain_launch.log"
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

# --- main ---
$port = Get-BrainPort $Url
Write-Host "Repo: $RepoRoot"
Write-Host "Brain URL: $Url (port $port)"

$launchExe = $false
$fullRestart = (-not $BrainOnly) -and (-not $NoTui)

Write-Host "`n[1] Stopping brain on :${port}..."
Stop-ListenersOnPort -Port $port
Start-Sleep -Milliseconds 400

if ($fullRestart) {
  Write-Host "[2] Stopping TUI..."
  Stop-MimirTui
  Start-Sleep -Milliseconds 300

  if (-not $SkipExeBuild) {
    Write-Host "[3] Rebuilding dist\mimir.exe..."
    Build-MimirExe
    $launchExe = $true
  } else {
    Write-Host "[3] Skipping exe build (-SkipExeBuild)."
    $exePath = Join-Path $RepoRoot "dist\mimir.exe"
    $launchExe = Test-Path $exePath
  }
}

Write-Host "$(if ($fullRestart) { '[4]' } else { '[2]' }) Starting brain..."
Start-Brain -BrainUrl $Url -TimeoutSec $ReadyTimeoutSec

if (-not $fullRestart) {
  Write-Host "Done (brain only)."
  exit 0
}

Write-Host "[5] Starting TUI in a new window..."
$exePath = Join-Path $RepoRoot "dist\mimir.exe"
if ($launchExe -and (Test-Path $exePath)) {
  Start-Process -FilePath $exePath -WorkingDirectory $RepoRoot
} else {
  if (-not $SkipExeBuild) {
    Write-Host "dist\mimir.exe missing after build; falling back to uv run mimir."
  }
  Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/k", "uv run mimir --url $Url") `
    -WorkingDirectory $RepoRoot
}

Write-Host "Done."
