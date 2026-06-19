[CmdletBinding()]
param(
  [switch]$SkipInstall,
  [switch]$Reload,
  [string]$HostName = "0.0.0.0",
  [int]$Port = 8000,
  [string]$DbPath = ""
)

$ErrorActionPreference = "Stop"

$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $BackendDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$EnvFile = Join-Path $BackendDir ".env.local"

function Get-EnvFileValue {
  param([string]$Name)

  if (-not (Test-Path $EnvFile)) {
    return ""
  }

  $pattern = "^\s*$([regex]::Escape($Name))\s*="
  $line = Get-Content $EnvFile | Where-Object { $_ -match $pattern } | Select-Object -First 1
  if (-not $line) {
    return ""
  }

  return ($line -replace $pattern, "").Trim()
}

function Get-OrCreateToken {
  if ($env:ADVISOR_TOKEN) {
    return $env:ADVISOR_TOKEN
  }

  $savedToken = Get-EnvFileValue -Name "ADVISOR_TOKEN"
  if ($savedToken) {
    return $savedToken
  }

  $bytes = [byte[]]::new(24)
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }

  $token = "adv_" + [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  Set-Content -LiteralPath $EnvFile -Value "ADVISOR_TOKEN=$token" -Encoding ASCII
  return $token
}

function Resolve-DbPath {
  if ($DbPath) {
    if ([System.IO.Path]::IsPathRooted($DbPath)) {
      return $DbPath
    }
    return (Join-Path $BackendDir $DbPath)
  }

  return (Join-Path $BackendDir "data\advisor.db")
}

function Resolve-PythonCommand {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ File = "py"; Args = @("-3") }
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ File = "python"; Args = @() }
  }
  throw "Python was not found. Install Python 3.11+ and retry."
}

function Ensure-Backend {
  if (-not (Test-Path $PythonExe)) {
    $python = Resolve-PythonCommand
    Write-Host "Creating backend virtualenv..."
    & $python.File @($python.Args) -m venv $VenvDir
  }

  if ($SkipInstall) {
    return
  }

  $requirements = Join-Path $BackendDir "requirements.txt"
  $marker = Join-Path $VenvDir ".requirements-installed"
  $needsInstall = -not (Test-Path $marker)
  if (-not $needsInstall) {
    $needsInstall = (Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $marker).LastWriteTimeUtc
  }

  if ($needsInstall) {
    Write-Host "Installing backend dependencies..."
    & $PythonExe -m pip install -r $requirements
    Set-Content -LiteralPath $marker -Value (Get-Date).ToString("o") -Encoding ASCII
  }
}

$token = Get-OrCreateToken
$env:ADVISOR_TOKEN = $token
$env:ADVISOR_HOST = $HostName
$env:ADVISOR_PORT = "$Port"
$env:ADVISOR_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
$resolvedDbPath = Resolve-DbPath
$env:ADVISOR_DB_PATH = $resolvedDbPath
$env:ADVISOR_SQLITE_JOURNAL_MODE = "MEMORY"
New-Item -ItemType Directory -Force (Split-Path -Parent $resolvedDbPath) | Out-Null

Ensure-Backend

Set-Location $BackendDir

Write-Host ""
Write-Host "Starting TradingAgents-HoldingsSkill backend..."
Write-Host "Backend : http://127.0.0.1:$Port"
Write-Host "Docs    : http://127.0.0.1:$Port/docs"
Write-Host "Token   : $token"
Write-Host "DB      : $resolvedDbPath"
Write-Host "Journal : MEMORY"
Write-Host "Reload  : $Reload"
Write-Host "Stop    : Ctrl+C"
Write-Host ""

$uvicornArgs = @("app.main:app", "--host", $HostName, "--port", "$Port")
if ($Reload) {
  $uvicornArgs += "--reload"
}

& $PythonExe -m uvicorn @uvicornArgs
