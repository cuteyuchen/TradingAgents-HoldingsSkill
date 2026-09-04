[CmdletBinding()]
param(
  [int]$BackendPort = 18002,
  [int]$FrontendPort = 18082
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $Root "docker-compose.yml"
$ProjectName = "phase-o2-manual-uat"
$ContainerName = "$ProjectName-advisor"

$env:ADVISOR_CONTAINER_NAME = $ContainerName
$env:BACKEND_PORT = "$BackendPort"
$env:FRONTEND_PORT = "$FrontendPort"

Push-Location $Root
try {
  & docker compose --project-name $ProjectName --file $ComposeFile stop advisor
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
} finally {
  Pop-Location
}

Write-Host "Stopped $ContainerName. The UAT volume was preserved."
