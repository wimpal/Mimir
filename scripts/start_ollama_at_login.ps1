# Idempotent Ollama startup for Windows Task Scheduler at logon (T-016).
#
# Starts ollama serve with no visible console; process output goes to
# data/logs/ollama_serve.log. Operator-facing steps go to ollama_login.log.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File scripts/start_ollama_at_login.ps1

[CmdletBinding()]
param(
  [string]$OllamaUrl = "http://127.0.0.1:11434",
  [int]$ReadyTimeoutSec = 90
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Write-OllamaLoginLog {
  param([string]$Message)
  $logDir = Join-Path $RepoRoot "data\logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $logPath = Join-Path $logDir "ollama_login.log"
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content -Path $logPath -Value "[$stamp] [login task] $Message"
}

function Resolve-OllamaPath {
  $cmd = Get-Command ollama -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) {
    return $cmd.Source
  }
  $local = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $local) {
    return $local
  }
  return $null
}

function Test-OllamaReady {
  param([string]$BaseUrl)
  $tags = ($BaseUrl.TrimEnd("/")) + "/api/tags"
  try {
    $resp = Invoke-WebRequest -Uri $tags -UseBasicParsing -TimeoutSec 2
    return ($resp.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Wait-OllamaReady {
  param([string]$BaseUrl, [int]$TimeoutSec)
  $tags = ($BaseUrl.TrimEnd("/")) + "/api/tags"
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  Write-OllamaLoginLog "Waiting for Ollama at $tags (up to ${TimeoutSec}s)..."
  while ((Get-Date) -lt $deadline) {
    if (Test-OllamaReady $BaseUrl) {
      Write-OllamaLoginLog "Ollama ready."
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Ollama did not become ready within ${TimeoutSec}s at $tags"
}

Write-OllamaLoginLog "login task start"
Write-OllamaLoginLog "Repo: $RepoRoot Ollama URL: $OllamaUrl"

if (Test-OllamaReady $OllamaUrl) {
  Write-OllamaLoginLog "Ollama already running - skipping start."
  Write-OllamaLoginLog "login task done (already running)"
  exit 0
}

$ollamaExe = Resolve-OllamaPath
if (-not $ollamaExe) {
  Write-OllamaLoginLog "ERROR: ollama not found on PATH or %LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  throw "Could not find ollama.exe. Install Ollama, then re-run install_login_tasks.ps1."
}
Write-OllamaLoginLog "Starting Ollama: $ollamaExe serve"

$logDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$serveLog = Join-Path $logDir "ollama_serve.log"
Add-Content -Path $serveLog -Value "`n--- login task launching ollama serve $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ---"

$inner = "`"$ollamaExe`" serve >> `"$serveLog`" 2>&1"
Start-Process -FilePath "cmd.exe" `
  -ArgumentList @("/c", $inner) `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden |
  Out-Null

Wait-OllamaReady -BaseUrl $OllamaUrl -TimeoutSec $ReadyTimeoutSec

Write-OllamaLoginLog "login task done"
exit 0
