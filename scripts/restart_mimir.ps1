# Full restart of Mimir brain + TUI (Windows).
#
# Usage (from repo root or anywhere):
#   powershell -File scripts/restart_mimir.ps1
#   powershell -File scripts/restart_mimir.ps1 -BrainOnly
#   powershell -File scripts/restart_mimir.ps1 -Url http://127.0.0.1:8000
#   powershell -File scripts/restart_mimir.ps1 -UseExe
#
# Stops listeners on the brain port, stops running TUI (uv run mimir / mimir.exe),
# starts a fresh brain, waits for /health, then opens the TUI in a new window.

[CmdletBinding()]
param(
  [string]$Url = $(if ($env:MIMIR_BRAIN_URL) { $env:MIMIR_BRAIN_URL } else { "http://127.0.0.1:8000" }),
  [switch]$BrainOnly,
  [switch]$NoTui,
  [switch]$UseExe,
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
  param([string]$BrainUrl)
  $port = Get-BrainPort $BrainUrl
  $hostName = "127.0.0.1"
  try {
    $uri = [Uri]$BrainUrl
    if ($uri.Host) { $hostName = $uri.Host }
  } catch { }

  $logDir = Join-Path $RepoRoot "data\logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir "brain_launch.log"
  Add-Content -Path $logPath -Value "`n--- restart_mimir launching brain ${hostName}:${port} ---"

  Write-Host "Starting brain on ${hostName}:${port}..."
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "Could not find uv on PATH. Install uv or start the brain manually."
  }
  # Append stdout/stderr to the same launch log the TUI launcher uses.
  $inner = "uv run uvicorn brain.main:app --host $hostName --port $port >> `"$logPath`" 2>&1"
  Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/c", $inner) `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden |
    Out-Null
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

# --- main ---
$port = Get-BrainPort $Url
Write-Host "Repo: $RepoRoot"
Write-Host "Brain URL: $Url (port $port)"

Write-Host "`n[1/4] Stopping brain on :${port}..."
Stop-ListenersOnPort -Port $port
Start-Sleep -Milliseconds 400

if (-not $BrainOnly -and -not $NoTui) {
  Write-Host "[2/4] Stopping TUI..."
  Stop-MimirTui
  Start-Sleep -Milliseconds 300
} else {
  Write-Host "[2/4] Skipping TUI stop (-BrainOnly/-NoTui)."
}

Write-Host "[3/4] Starting brain..."
Start-Brain -BrainUrl $Url
Wait-BrainReady -BrainUrl $Url -TimeoutSec $ReadyTimeoutSec

if ($BrainOnly -or $NoTui) {
  Write-Host "[4/4] Skipping TUI start."
  Write-Host "Done (brain only)."
  exit 0
}

Write-Host "[4/4] Starting TUI in a new window..."
$exePath = Join-Path $RepoRoot "dist\mimir.exe"
if ($UseExe -and (Test-Path $exePath)) {
  Start-Process -FilePath $exePath -WorkingDirectory $RepoRoot
} else {
  # Dev default: uv run (picks up source). Use -UseExe for dist\mimir.exe.
  Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/k", "uv run mimir --url $Url") `
    -WorkingDirectory $RepoRoot
}

Write-Host "Done."
