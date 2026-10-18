<#
.SYNOPSIS
  Start FinAlly in Docker (Windows). Safe to run repeatedly.
.EXAMPLE
  .\scripts\start_windows.ps1            # build image if missing, (re)start container
  .\scripts\start_windows.ps1 -Build     # force an image rebuild
  .\scripts\start_windows.ps1 -NoOpen    # don't open the browser
#>
param(
    [switch]$Build,
    [switch]$NoOpen,
    [int]$Port = 8000
)

# Native commands are checked via $LASTEXITCODE; 'Stop' would turn docker's stderr into errors on PS 5.1
$ErrorActionPreference = 'Continue'

$Image = 'finally'
$Container = 'finally'
$Volume = 'finally-data'
$Url = "http://localhost:$Port"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Docker is not running. Start Docker Desktop and try again.'
    exit 1
}

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Warning '.env not found; created it from .env.example.'
    Write-Warning 'Set OPENROUTER_API_KEY in .env to enable AI chat (or LLM_MOCK=true).'
}

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    Write-Host "Building image '$Image'..."
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { Write-Error 'docker build failed.'; exit 1 }
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing container '$Container'..."
    docker rm -f $Container | Out-Null
}

Write-Host "Starting container '$Container'..."
docker run -d --name $Container -v "${Volume}:/app/db" -p "${Port}:8000" --env-file .env $Image | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error 'docker run failed.'; exit 1 }

Write-Host -NoNewline 'Waiting for the app'
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { Write-Host ' ready.'; break }
    } catch { }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 1
}
Write-Host ''
Write-Host "FinAlly is running at $Url"
Write-Host "Logs: docker logs -f $Container    Stop: .\scripts\stop_windows.ps1"

if (-not $NoOpen) { Start-Process $Url }
