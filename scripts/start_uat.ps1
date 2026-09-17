[CmdletBinding()]
param(
  [int]$BackendPort = 18002,
  [int]$FrontendPort = 18082,
  [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

if ($BackendPort -eq $FrontendPort) {
  throw "BackendPort and FrontendPort must be different."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $Root "docker-compose.yml"
$ProjectName = "phase-o2-manual-uat"
$ContainerName = "$ProjectName-advisor"
$GitSha = (& git -C $Root rev-parse HEAD).Trim()

$env:ADVISOR_CONTAINER_NAME = $ContainerName
$env:BACKEND_PORT = "$BackendPort"
$env:FRONTEND_PORT = "$FrontendPort"
$env:PUBLIC_APP_URL = "http://127.0.0.1:$FrontendPort"
$env:ADVISOR_CORS_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort,http://127.0.0.1:$BackendPort,http://localhost:$BackendPort"
$env:APP_ENV = "development"
$env:ACCEPTANCE_MODE = "false"
$env:APP_GIT_SHA = $GitSha
$env:APP_BUILD_TIME = (Get-Date).ToUniversalTime().ToString("o")

# Load repo .env into empty process vars for compose interpolation. Never print values.
$DotEnvPath = Join-Path $Root ".env"
if (Test-Path $DotEnvPath) {
  foreach ($line in Get-Content $DotEnvPath) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $parts = $line -split '=', 2
    if ($parts.Count -lt 2) { continue }
    $name = $parts[0].Trim()
    $value = $parts[1]
    if (-not $name) { continue }
    $existing = [Environment]::GetEnvironmentVariable($name, "Process")
    if ([string]::IsNullOrWhiteSpace($existing)) {
      Set-Item -Path "Env:$name" -Value ($value.Trim().Trim('"').Trim("'"))
    }
  }
}

# UAT defaults for the existing RealtimeMonitor. Do not override explicit user values.
if ([string]::IsNullOrWhiteSpace($env:REALTIME_MONITOR_ENABLED)) {
  $env:REALTIME_MONITOR_ENABLED = "true"
}
if ([string]::IsNullOrWhiteSpace($env:MONITOR_INTERVAL_SECONDS)) {
  $env:MONITOR_INTERVAL_SECONDS = "60"
}
if ([string]::IsNullOrWhiteSpace($env:MARKET_SCORE_INTERVAL_MINUTES)) {
  $env:MARKET_SCORE_INTERVAL_MINUTES = "5"
}
# All-A needs SecurityMaster. UAT opt-in only; production compose default stays false.
if ([string]::IsNullOrWhiteSpace($env:SECURITY_MASTER_SYNC_ENABLED)) {
  $env:SECURITY_MASTER_SYNC_ENABLED = "true"
}

$composeArgs = @(
  "--project-name", $ProjectName,
  "--file", $ComposeFile,
  "up", "-d", "--remove-orphans"
)
if (-not $NoBuild) {
  $composeArgs += "--build"
}

Push-Location $Root
try {
  & docker compose @composeArgs
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
} finally {
  Pop-Location
}

Write-Host "Manual UAT URL: http://127.0.0.1:$FrontendPort"
Write-Host "Backend health: http://127.0.0.1:$BackendPort/healthz/live"
Write-Host "Compose project: $ProjectName"
Write-Host "Container: $ContainerName"
Write-Host "Runtime DB: Docker volume ${ProjectName}_advisor-data:/app/data/advisor.db"
Write-Host "Acceptance mode: OFF"
