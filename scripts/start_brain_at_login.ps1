# Idempotent brain startup for Windows Task Scheduler at logon (T-016).
#
# Waits for Ollama, skips if /health is already OK, else starts uvicorn via
# brain_launcher.ensure_brain_running (same path as the TUI backup launcher).
#
# Do NOT use restart_mimir.ps1 -BrainOnly here - that kills the port listener first.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_brain_at_login.ps1
#   powershell -File scripts/start_brain_at_login.ps1 -Url http://127.0.0.1:8000

[CmdletBinding()]
param(
  [string]$Url = $(if ($env:MIMIR_BRAIN_URL) { $env:MIMIR_BRAIN_URL } else { "http://127.0.0.1:8000" }),
  [string]$OllamaUrl = "http://127.0.0.1:11434",
  [int]$OllamaWaitSec = 90,
  [int]$ReadyTimeoutSec = 60
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Write-LoginLog {
  param([string]$Message)
  $logDir = Join-Path $RepoRoot "data\logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir "brain_login.log"
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content -Path $logPath -Value "[$stamp] [login task] $Message"
}

function Resolve-UvPath {
  $cmd = Get-Command uv -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) {
    return $cmd.Source
  }
  $candidates = @(
    (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
    (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
  )
  foreach ($path in $candidates) {
    if ($path -and (Test-Path $path)) {
      return $path
    }
  }
  return $null
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

function Wait-OllamaReady {
  param([string]$BaseUrl, [int]$TimeoutSec)
  $tags = ($BaseUrl.TrimEnd("/")) + "/api/tags"
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  Write-LoginLog "Waiting for Ollama at $tags (up to ${TimeoutSec}s)..."
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $tags -UseBasicParsing -TimeoutSec 2
      if ($resp.StatusCode -eq 200) {
        Write-LoginLog "Ollama ready."
        return
      }
    } catch {
      # keep polling
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Ollama did not become ready within ${TimeoutSec}s at $tags"
}

Write-LoginLog "login task start"
Write-LoginLog "Repo: $RepoRoot Brain URL: $Url"

$uv = Resolve-UvPath
if (-not $uv) {
  Write-LoginLog "ERROR: uv not found on PATH or common install locations."
  throw "Could not find uv. Install uv or add it to the user PATH, then re-login."
}
Write-LoginLog "Using uv: $uv"

Wait-OllamaReady -BaseUrl $OllamaUrl -TimeoutSec $OllamaWaitSec

if (Test-BrainHealth $Url) {
  Write-LoginLog "Brain already healthy at $Url - skipping start."
  Write-LoginLog "login task done (already running)"
  exit 0
}

Write-LoginLog "Starting brain via ensure_brain_running..."

$logDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$cliLog = Join-Path $logDir "brain_login_cli.log"
$cliErrLog = Join-Path $logDir "brain_login_cli.err.log"

$proc = Start-Process -FilePath $uv `
  -ArgumentList @(
    "run", "python", "scripts/ensure_brain_cli.py",
    "--url", $Url,
    "--ready-timeout", "$ReadyTimeoutSec"
  ) `
  -WorkingDirectory $RepoRoot `
  -Wait -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $cliLog `
  -RedirectStandardError $cliErrLog

if (Test-Path $cliLog) {
  Get-Content -Path $cliLog -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Trim()) { Write-LoginLog "ensure_brain_cli: $_" }
  }
}
if (Test-Path $cliErrLog) {
  Get-Content -Path $cliErrLog -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Trim()) { Write-LoginLog "ensure_brain_cli stderr: $_" }
  }
}

if ($proc.ExitCode -ne 0) {
  Write-LoginLog "ERROR: ensure_brain_running exited $($proc.ExitCode). See data/logs/brain_launch.log"
  exit $proc.ExitCode
}

if (-not (Test-BrainHealth $Url)) {
  Write-LoginLog "ERROR: brain still not healthy after start."
  exit 1
}

Write-LoginLog "Brain ready at $Url."
Write-LoginLog "login task done"
exit 0
