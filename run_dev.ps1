# Start backend + frontend for local development (Windows)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host "Starting Multi-Agent Orchestration System..."

# Backend
Set-Location "$Root\backend"
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..."
    python -m venv .venv
}
& ".\.venv\Scripts\Activate.ps1"
pip install -q -r requirements.txt
$backend = Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000" -PassThru -WorkingDirectory "$Root\backend"

# Frontend
Set-Location "$Root\frontend"
if (-not (Test-Path "node_modules")) {
    npm install
}
$frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev" -PassThru -WorkingDirectory "$Root\frontend"

Write-Host "Backend PID: $($backend.Id)"
Write-Host "Frontend PID: $($frontend.Id)"
Write-Host "Dashboard: http://localhost:5173"
Write-Host "API Docs: http://localhost:8000/docs"
Write-Host "Press Ctrl+C in this window to stop (or close terminal and kill processes)."

try {
    Wait-Process -Id $backend.Id, $frontend.Id
} finally {
    Stop-Process -Id $backend.Id, $frontend.Id -Force -ErrorAction SilentlyContinue
}
