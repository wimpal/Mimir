# Register Windows Task Scheduler tasks for T-016 auto-start (Ollama + Mimir brain).
#
# Run once from an elevated or normal user PowerShell (tasks run as current user):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_login_tasks.ps1
#
# Optional:
#   -SkipOllama          # when Ollama is already a Windows service
#   -Uninstall           # remove \Heim\ tasks

[CmdletBinding()]
param(
  [switch]$SkipOllama,
  [switch]$Uninstall,
  [int]$BrainDelaySec = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TaskPath = "\Heim\"
$OllamaTaskName = "Heim Ollama"
$BrainTaskName = "Heim Mimir brain"
$LoginScript = Join-Path $RepoRoot "scripts\start_brain_at_login.ps1"
$OllamaScript = Join-Path $RepoRoot "scripts\start_ollama_at_login.ps1"

function Remove-HeimTask {
  param([string]$Name)
  $full = "$TaskPath$Name"
  $existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $Name -ErrorAction SilentlyContinue
  if ($existing) {
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $Name -Confirm:$false
    Write-Host "Removed task: $full"
  }
}

if ($Uninstall) {
  Remove-HeimTask -Name $OllamaTaskName
  Remove-HeimTask -Name $BrainTaskName
  Write-Host "Done. Heim login tasks removed."
  exit 0
}

if (-not (Test-Path $LoginScript)) {
  throw "Missing $LoginScript - run from the Mimir repo."
}

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$psExe = (Get-Command powershell.exe).Source

if (-not $SkipOllama) {
  if (-not (Test-Path $OllamaScript)) {
    throw "Missing $OllamaScript - run from the Mimir repo."
  }
  Write-Host "Registering $OllamaTaskName -> $OllamaScript (hidden, logged)"
  $ollamaArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$OllamaScript`""
  $ollamaAction = New-ScheduledTaskAction -Execute $psExe -Argument $ollamaArgs -WorkingDirectory $RepoRoot
  Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $OllamaTaskName `
    -Action $ollamaAction `
    -Trigger $logonTrigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null
} else {
  Write-Host "Skipping Ollama task (-SkipOllama)."
  Remove-HeimTask -Name $OllamaTaskName
}

$brainDelayTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$brainDelayTrigger.Delay = "PT${BrainDelaySec}S"

$brainArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LoginScript`""
$brainAction = New-ScheduledTaskAction -Execute $psExe -Argument $brainArgs -WorkingDirectory $RepoRoot

Write-Host "Registering $BrainTaskName -> $LoginScript (delay ${BrainDelaySec}s, hidden, logged)"
Register-ScheduledTask `
  -TaskPath $TaskPath `
  -TaskName $BrainTaskName `
  -Action $brainAction `
  -Trigger $brainDelayTrigger `
  -Principal $principal `
  -Settings $settings `
  -Force | Out-Null

Write-Host ""
Write-Host "Done. Tasks installed under Task Scheduler -> Task Scheduler Library -> Heim"
Write-Host "One-time LAN prep (M5): powershell -File scripts/install_brain_firewall.ps1"
Write-Host "Test brain task: Start-ScheduledTask -TaskPath '\Heim\' -TaskName '$BrainTaskName'"
Write-Host "Uninstall: powershell -File scripts/install_login_tasks.ps1 -Uninstall"
