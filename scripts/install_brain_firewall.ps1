# Inbound Windows Firewall rules for Mimir brain LAN + Tailscale access (T-022, T-015).
#
# Requires **Administrator** PowerShell (creating firewall rules needs elevation).
#
#   1. Start menu -> Windows PowerShell -> Run as administrator
#   2. cd D:\Dev\Projects\Mimir
#   3. powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_brain_firewall.ps1
#
# Optional:
#   -Tailscale   also allow inbound from Tailscale CGNAT (100.64.0.0/10) for M7 remote access
#   -Uninstall    remove installed rule(s)
#   -Port 8000    override port (must match config runtime.port)

[CmdletBinding()]
param(
  [switch]$Uninstall,
  [switch]$Tailscale,
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$LanRuleName = "Heim Mimir brain (TCP $Port, Private)"
$TailscaleRuleName = "Heim Mimir brain (TCP $Port, Tailscale)"

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
  Write-Host "  3. powershell -File scripts/install_brain_firewall.ps1 [-Tailscale]"
  exit 1
}

function Remove-FirewallRuleByName {
  param([string]$Name)
  $existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
  if ($existing) {
    Remove-NetFirewallRule -DisplayName $Name
    Write-Host "Removed firewall rule: $Name"
  } else {
    Write-Host "No firewall rule named '$Name' to remove."
  }
}

function Install-LanRule {
  $existing = Get-NetFirewallRule -DisplayName $LanRuleName -ErrorAction SilentlyContinue
  if ($existing) {
    Write-Host "Firewall rule already exists: $LanRuleName"
    return
  }
  Write-Host "Creating firewall rule: $LanRuleName"
  Write-Host "  Profile: Private | RemoteAddress: LocalSubnet | LocalPort: $Port"
  New-NetFirewallRule `
    -DisplayName $LanRuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private `
    -RemoteAddress LocalSubnet | Out-Null
}

function Install-TailscaleRule {
  $existing = Get-NetFirewallRule -DisplayName $TailscaleRuleName -ErrorAction SilentlyContinue
  if ($existing) {
    Write-Host "Firewall rule already exists: $TailscaleRuleName"
    return
  }
  Write-Host "Creating firewall rule: $TailscaleRuleName"
  Write-Host "  RemoteAddress: 100.64.0.0/10 (Tailscale CGNAT) | LocalPort: $Port"
  New-NetFirewallRule `
    -DisplayName $TailscaleRuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress 100.64.0.0/10 | Out-Null
}

if ($Uninstall) {
  Remove-FirewallRuleByName -Name $LanRuleName
  Remove-FirewallRuleByName -Name $TailscaleRuleName
  exit 0
}

Install-LanRule
if ($Tailscale) {
  Install-TailscaleRule
}

Write-Host "Done. Verify home Wi-Fi is set to Private (Settings -> Network)."
if ($Tailscale) {
  Write-Host "Tailscale: smoke-test GET /health from phone on tailnet (mobile data)."
} else {
  Write-Host "M7 remote access: re-run with -Tailscale after joining PC to tailnet."
}
Write-Host "Uninstall: powershell -File scripts/install_brain_firewall.ps1 -Uninstall"
