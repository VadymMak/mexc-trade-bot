# run-prod.ps1
# Start backend in PRODUCTION mode (no reload, single worker)
param(
  [string]$HostIp = "0.0.0.0",
  [int]$Port = 8000,
  [string]$EnvFile = ".env.production"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MEXC Trading Bot - PRODUCTION MODE" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if .env.production exists
if (-not (Test-Path $EnvFile)) {
  Write-Host "ERROR: $EnvFile not found!" -ForegroundColor Red
  Write-Host "Please create $EnvFile from .env.production template" -ForegroundColor Yellow
  exit 1
}

# Load environment variables
Write-Host "Loading environment from: $EnvFile" -ForegroundColor Green
Get-Content $EnvFile | ForEach-Object {
  if ($_ -match '^([^#][^=]+)=(.*)$') {
    $name = $matches[1].Trim()
    $value = $matches[2].Trim()
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    Write-Host "  ✓ $name" -ForegroundColor DarkGray
  }
}

Write-Host ""
Write-Host "Starting Uvicorn on http://${HostIp}:${Port}" -ForegroundColor Cyan
Write-Host "OpenAPI:  http://${HostIp}:${Port}/docs"
Write-Host "Health:   http://${HostIp}:${Port}/api/healthz"
Write-Host "Config:   http://${HostIp}:${Port}/api/config/provider"
Write-Host ""
Write-Host "⚠️  PRODUCTION MODE - No hot reload!" -ForegroundColor Yellow
Write-Host ""

# Production Uvicorn args
$UvicornArgs = @(
  '--host', $HostIp,
  '--port', "$Port",
  '--workers', '1',
  '--log-level', 'info',
  '--access-log',
  '--no-use-colors',
  'app.main:app'
)

uvicorn @UvicornArgs