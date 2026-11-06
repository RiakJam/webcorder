$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw 'Install FFmpeg and add it to PATH before starting Webcorder.'
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    uv venv --python 3.13 .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $python -c 'import mediapipe, zmq, PIL'
if ($LASTEXITCODE -ne 0) {
    uv pip install --python $python -r requirements-web.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
}

$url = 'http://127.0.0.1:8765/'
function Test-Webcorder {
    try {
        $health = Invoke-RestMethod -Uri "${url}api/health" -TimeoutSec 1
        return $health.app -eq 'webcorder'
    } catch {
        return $false
    }
}

if (-not (Test-Webcorder)) {
    $state = Join-Path $env:LOCALAPPDATA 'Webcorder'
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $log = Join-Path $state 'server.log'
    $errors = Join-Path $state 'server-error.log'
    Start-Process -FilePath $python -ArgumentList 'server.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errors | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Milliseconds 200
        if (Test-Webcorder) { $ready = $true; break }
    }
    if (-not $ready) { throw "Webcorder did not start. See $errors" }
}
Start-Process $url
