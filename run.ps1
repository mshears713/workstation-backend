# Start the workstation backend.
#
#   .\run.ps1
#
# Use this instead of typing the uvicorn line by hand. It sets the two things
# that are easy to get wrong and expensive to notice later:
#
#   --host 0.0.0.0   the ESP32 cannot reach 127.0.0.1. Binding to loopback
#                    looks fine from this machine and fails from the device.
#
#   --reload         restart on any change under app/ or config/, and on .env.
#                    Without it, a committed backend change can sit unloaded
#                    for hours while firmware is tested against it - which has
#                    happened, and cost a hardware test plus a post-mortem to
#                    work out. GET /health reports the running commit for the
#                    same reason.
#
# .env is included explicitly because uvicorn's reloader only watches Python
# files by default, and adding a credential there is exactly the kind of
# change you expect to take effect immediately.
#
# Safe to leave running while recording: a restart mid-capture fails the
# in-flight chunk, the firmware retries it, and the accumulated .pcm on disk
# survives - so the recording continues rather than being lost.

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtualenv at .venv - expected $python"
}

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1).IPAddress
Write-Host "Backend on http://${lan}:8000  (BACKEND_BASE_URL in main/backend_config.h must match)" -ForegroundColor Cyan
Write-Host "Auto-reloads on changes under app/, config/ and .env. Ctrl+C to stop." -ForegroundColor DarkGray

& $python -m uvicorn app.api.main:app `
    --host 0.0.0.0 `
    --port 8000 `
    --reload `
    --reload-dir app `
    --reload-dir config `
    --reload-include .env
