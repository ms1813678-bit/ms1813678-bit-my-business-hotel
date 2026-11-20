$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Run scripts\windows\setup.ps1 first." }
if (-not (Test-Path ".env")) { throw ".env is missing. Run setup first." }
Write-Host "Starting locally at http://127.0.0.1:8000"
Write-Host "Keep this window open. Press Ctrl+C to stop."
Start-Process "http://127.0.0.1:8000"
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
