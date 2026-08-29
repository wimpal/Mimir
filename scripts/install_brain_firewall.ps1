# Inbound Windows Firewall rule for Mimir brain LAN access (T-022).
#
# Requires **Administrator** PowerShell (creating firewall rules needs elevation).
#
#   1. Start menu -> Windows PowerShell -> Run as administrator
#   2. cd D:\Dev\Projects\Mimir
#   3. powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_brain_firewall.ps1
#
# Optional:
#   -Uninstall   remove the rule
#   -Port 8000   override port (must match config runtime.port)

[CmdletBinding()]
param(
  [switch]$Uninstall,
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$RuleName = "Heim Mimir brain (TCP $Port, Private)"

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
  Write-Host "ERROR: Administrator PowerShell is required to create firewall rules." -ForegroundColor Red
  Write-Host ""
  Write-Host "  1. Start menu -> Windows PowerShell -> Run as administrator"
  Write-Host "  2. cd $PSScriptRoot\.."
  Write-Host "  3. powershell -File scripts/install_brain_firewall.ps1"
  exit 1
}

function Remove-BrainFirewallRule {
  $existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
  if ($existing) {
    Remove-NetFirewallRule -DisplayName $RuleName
    Write-Host "Removed firewall rule: $RuleName"
  } else {
    Write-Host "No firewall rule named '$RuleName' to remove."
  }
}

if ($Uninstall) {
  Remove-BrainFirewallRule
  exit 0
}

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
  Write-Host "Firewall rule already exists: $RuleName"
  exit 0
}

Write-Host "Creating firewall rule: $RuleName"
Write-Host "  Profile: Private | RemoteAddress: LocalSubnet | LocalPort: $Port"

New-NetFirewallRule `
  -DisplayName $RuleName `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort $Port `
  -Profile Private `
  -RemoteAddress LocalSubnet | Out-Null

Write-Host "Done. Verify home Wi-Fi is set to Private (Settings -> Network)."
Write-Host "Uninstall: powershell -File scripts/install_brain_firewall.ps1 -Uninstall"
