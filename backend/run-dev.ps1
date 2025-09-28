# run-dev.ps1
# Start backend with hot-reload. Prefer 127.0.0.1:8000, fall back to 127.0.0.1:8010 if 8000 is busy.

param(
  [string]$HostIp = "127.0.0.1",
  [int]$PreferredPort = 8000,
  [int]$FallbackPort = 8010
)

function Test-PortInUse {
  param([string]$ip, [int]$port)
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($ip, $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(250)
    if ($ok -and $client.Connected) { $client.Close(); return $true }
    $client.Close()
    return $false
  } catch { return $false }
}

$usePort = $PreferredPort
if (Test-PortInUse -ip $HostIp -port $PreferredPort) {
  Write-Host "Port $PreferredPort is already in use on $HostIp. Falling back to $FallbackPort." -ForegroundColor Yellow
  $usePort = $FallbackPort
} else {
  Write-Host ("Using {0}:{1}" -f $HostIp, $PreferredPort)
}

Write-Host ("Starting Uvicorn on http://{0}:{1}" -f $HostIp, $usePort) -ForegroundColor Cyan
Write-Host ("OpenAPI:  http://{0}:{1}/docs" -f $HostIp, $usePort)
Write-Host ("Health:   http://{0}:{1}/ping" -f $HostIp, $usePort)
Write-Host ("Config:   http://{0}:{1}/api/config/provider" -f $HostIp, $usePort)
Write-Host ""

# Notes:
# --reload-include keeps watcher focused on app code
# --reload-exclude avoids reload storms from DB/log/pycache changes
# --reload-delay helps coalesce multiple FS events on Windows
$UvicornArgs = @(
  '--host', $HostIp,
  '--port', "$usePort",
  '--reload',
  '--reload-dir', 'app',
  '--log-level', 'debug',
  '--access-log',
  'app.main:app'
)

uvicorn @UvicornArgs
