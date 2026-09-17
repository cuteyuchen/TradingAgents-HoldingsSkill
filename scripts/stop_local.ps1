[CmdletBinding()]
param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Stopping local TradingAgents processes on ports $BackendPort / $FrontendPort..."

function Stop-ByPort {
  param([int]$Port)

  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $connections) {
    Write-Host "Port ${Port}: no listener"
    return
  }

  $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $pids) {
    if ($procId -le 0) { continue }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    Write-Host "Port $Port -> stopping PID $procId ($($proc.ProcessName))"
    try {
      Stop-Process -Id $procId -Force
    } catch {
      Write-Warning "Failed to stop PID ${procId}: $($_.Exception.Message)"
    }
  }
}

Stop-ByPort -Port $BackendPort
Stop-ByPort -Port $FrontendPort

Write-Host "Done."
