$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv'
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.venv/Scripts/python.exe' -m backend.manage migrate
if ($LASTEXITCODE -ne 0) { throw 'Workspace initialization failed.' }
Write-Host 'Start the frontend in another terminal: cd frontend; npm.cmd ci; npm.cmd run dev'
Write-Host 'First use: configure your API key and run python -m backend.manage bootstrap.'
& '.venv/Scripts/python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
