<#
.SYNOPSIS
  Starts everything the "hybrid" deployment needs locally: MinIO (object
  storage, replaces R2), a Cloudflare Tunnel (makes MinIO reachable from the
  browser/Render, without needing R2's credit-card requirement), and the
  book2audio local render worker daemon.

  Run this only while you actually want to render books. Ctrl+C stops
  everything cleanly -- nothing is left running in the background.

.NOTES
  One-time setup already done: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
  R2_BUCKET / WORKER_MODE=local are in .env. minio.exe / cloudflared.exe
  live in .local-tools/ (gitignored, downloaded once).

  The tunnel URL changes every time this script runs (free "quick tunnel",
  no Cloudflare domain on file). After it prints the new URL, update
  R2_ENDPOINT_URL on Render (Dashboard -> epub-to-audio-book -> Environment)
  and it'll redeploy automatically. Uploads/downloads won't work against the
  old URL until you do.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root ".local-tools"
$dataDir = Join-Path $tools "minio-data"
$minioLog = Join-Path $tools "minio.log"
$minioErrLog = Join-Path $tools "minio.err.log"
$tunnelLog = Join-Path $tools "cloudflared.log"
$tunnelErrLog = Join-Path $tools "cloudflared.err.log"

if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir -Force | Out-Null }

# --- load .env into this process's environment ---
Get-Content (Join-Path $root ".env") | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?([^"]*)"?\s*$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}

foreach ($required in @("DATABASE_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")) {
    if (-not [Environment]::GetEnvironmentVariable($required)) {
        Write-Error "$required is not set in .env -- run the one-time setup first."
        exit 1
    }
}

$minioProc = $null
$tunnelProc = $null
$workerProc = $null

try {
    # --- 1. MinIO ---
    Write-Host "Starting MinIO..." -ForegroundColor Cyan
    $env:MINIO_ROOT_USER = $env:R2_ACCESS_KEY_ID
    $env:MINIO_ROOT_PASSWORD = $env:R2_SECRET_ACCESS_KEY
    $minioProc = Start-Process -FilePath (Join-Path $tools "minio.exe") `
        -ArgumentList "server", "`"$dataDir`"", "--address", ":9000", "--console-address", ":9001" `
        -RedirectStandardOutput $minioLog -RedirectStandardError $minioErrLog `
        -PassThru -WindowStyle Hidden

    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:9000/minio/health/live" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $healthy = $true; break }
        } catch {}
    }
    if (-not $healthy) {
        Write-Error "MinIO didn't come up in time -- check $minioLog"
        exit 1
    }
    Write-Host "MinIO is up (http://127.0.0.1:9000)." -ForegroundColor Green

    # --- 2. bucket (idempotent, safe to call every run) ---
    Write-Host "Ensuring bucket '$env:R2_BUCKET' exists..." -ForegroundColor Cyan
    Push-Location $root
    uv run python -c @"
import os, boto3
from botocore.exceptions import ClientError
c = boto3.client('s3', endpoint_url='http://127.0.0.1:9000',
                 aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                 aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])
try:
    c.head_bucket(Bucket=os.environ['R2_BUCKET'])
    print('bucket already exists')
except ClientError:
    c.create_bucket(Bucket=os.environ['R2_BUCKET'])
    print('bucket created')
"@
    Pop-Location

    # --- 3. Cloudflare Tunnel ---
    Write-Host "Starting Cloudflare Tunnel..." -ForegroundColor Cyan
    $tunnelProc = Start-Process -FilePath (Join-Path $tools "cloudflared.exe") `
        -ArgumentList "tunnel", "--url", "http://127.0.0.1:9000" `
        -RedirectStandardOutput $tunnelLog -RedirectStandardError $tunnelErrLog `
        -PassThru -WindowStyle Hidden

    $tunnelUrl = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        foreach ($logFile in @($tunnelErrLog, $tunnelLog)) {
            if (Test-Path $logFile) {
                $match = Select-String -Path $logFile -Pattern "https://[a-zA-Z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
                if ($match) { $tunnelUrl = $match.Matches[0].Value; break }
            }
        }
        if ($tunnelUrl) { break }
    }
    if (-not $tunnelUrl) {
        Write-Error "Tunnel didn't come up in time -- check $tunnelLog"
        exit 1
    }
    Write-Host ""
    Write-Host "=====================================================================" -ForegroundColor Yellow
    Write-Host " Tunnel URL: $tunnelUrl" -ForegroundColor Yellow
    Write-Host " This changed since last run. Update R2_ENDPOINT_URL on Render to" -ForegroundColor Yellow
    Write-Host " this value (Dashboard -> epub-to-audio-book -> Environment) or" -ForegroundColor Yellow
    Write-Host " uploads/downloads will fail." -ForegroundColor Yellow
    Write-Host "=====================================================================" -ForegroundColor Yellow
    Write-Host ""

    # --- 4. local worker daemon ---
    $env:R2_ENDPOINT_URL = $tunnelUrl
    Write-Host "Starting local worker daemon (Ctrl+C to stop everything)..." -ForegroundColor Cyan
    Push-Location $root
    $workerProc = Start-Process -FilePath "uv" -ArgumentList "run", "python", "-m", "book2audio.worker.local_daemon" `
        -NoNewWindow -PassThru
    Pop-Location

    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Cyan
    foreach ($p in @($workerProc, $tunnelProc, $minioProc)) {
        if ($p -and -not $p.HasExited) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    Write-Host "Stopped. Nothing is running in the background." -ForegroundColor Green
}
