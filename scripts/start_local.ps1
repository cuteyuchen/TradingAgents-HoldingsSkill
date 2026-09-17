[CmdletBinding()]
param(
  [switch]$SkipInstall,
  [switch]$Reload = $true,
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173,
  [string]$BackendHost = "127.0.0.1",
  [switch]$NoFrontend,
  [switch]$NoBackend
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$BackendScript = Join-Path $BackendDir "scripts\start-dev.ps1"

function Resolve-Node {
  if ($env:MIMO_NODE -and (Test-Path $env:MIMO_NODE)) {
    return $env:MIMO_NODE
  }
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  $candidates = @(
    "C:\Program Files\nodejs\node.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe"),
    (Join-Path $env:USERPROFILE "scoop\shims\node.exe")
  )
  foreach ($path in $candidates) {
    if ($path -and (Test-Path $path)) {
      return $path
    }
  }
  throw "Node.js was not found. Install Node 20+ and retry."
}

function Resolve-Npm {
  $node = Resolve-Node
  $npm = Join-Path (Split-Path -Parent $node) "npm.cmd"
  if (Test-Path $npm) {
    return $npm
  }
  $cmd = Get-Command npm -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  throw "npm was not found next to Node."
}

Write-Host ""
Write-Host "TradingAgents-HoldingsSkill local start (no Docker)" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Backend: http://$BackendHost`:$BackendPort"
Write-Host "Frontend: http://localhost:$FrontendPort"
Write-Host "Production packaging remains docker compose / GHCR only."
Write-Host ""

$backendJob = $null
$frontendJob = $null

if (-not $NoBackend) {
  if (-not (Test-Path $BackendScript)) {
    throw "Missing backend start script: $BackendScript"
  }

  $backendArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $BackendScript,
    "-HostName", $BackendHost,
    "-Port", "$BackendPort"
  )
  if ($SkipInstall) {
    $backendArgs += "-SkipInstall"
  }
  if ($Reload) {
    $backendArgs += "-Reload"
  }

  Write-Host "Starting backend (uvicorn, local venv)..." -ForegroundColor Green
  $backendJob = Start-Process -FilePath "powershell.exe" `
    -ArgumentList $backendArgs `
    -WorkingDirectory $BackendDir `
    -PassThru `
    -RedirectStandardOutput (Join-Path $Root "local-backend.out.log") `
    -RedirectStandardError (Join-Path $Root "local-backend.err.log")
  Write-Host "Backend PID $($backendJob.Id); logs: local-backend.out.log / local-backend.err.log"
}

if (-not $NoFrontend) {
  $node = Resolve-Node
  $npm = Resolve-Npm
  $nodeModules = Join-Path $FrontendDir "node_modules"

  if (-not $SkipInstall -and -not (Test-Path $nodeModules)) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Green
    & $npm --prefix $FrontendDir install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
      throw "frontend npm install failed"
    }
  }

  Write-Host "Starting frontend (vite dev, proxies /api -> backend)..." -ForegroundColor Green
  $env:VITE_BACKEND_URL = "http://$BackendHost`:$BackendPort"
  $frontendJob = Start-Process -FilePath $npm `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort") `
    -WorkingDirectory $FrontendDir `
    -PassThru `
    -RedirectStandardOutput (Join-Path $Root "local-frontend.out.log") `
    -RedirectStandardError (Join-Path $Root "local-frontend.err.log")
  Write-Host "Frontend PID $($frontendJob.Id); logs: local-frontend.out.log / local-frontend.err.log"
}

Write-Host ""
Write-Host "Open: http://localhost:$FrontendPort" -ForegroundColor Cyan
Write-Host "API docs: http://$BackendHost`:$BackendPort/docs"
Write-Host "Stop: close this window, or stop PIDs: $(if ($backendJob) { $backendJob.Id }) $(if ($frontendJob) { $frontendJob.Id })"
Write-Host ""
Write-Host "Press Ctrl+C to leave processes running in background."
Write-Host "Use scripts/stop_local.ps1 to stop them later."

# Keep the launcher attached so Ctrl+C is discoverable; child processes stay up if this exits.
if ($backendJob -or $frontendJob) {
  try {
    while ($true) {
      Start-Sleep -Seconds 5
      $alive = @()
      if ($backendJob -and -not $backendJob.HasExited) { $alive += "backend:$($backendJob.Id)" }
      if ($frontendJob -and -not $frontendJob.HasExited) { $alive += "frontend:$($frontendJob.Id)" }
      if (-not $alive.Count) {
        Write-Host "Local processes exited. Check local-*.log files." -ForegroundColor Yellow
        break
      }
    }
  } finally {
    Write-Host "Launcher exiting. Child processes may still be running."
  }
}
