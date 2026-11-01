# Sets up ALPHA on this computer: its own Python environment, then a desktop shortcut.
# Run from anywhere: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Choose a specific Python with: -Python "C:\path\to\python.exe"

param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($version -notin @("3.11", "3.12", "3.13")) {
    throw "ALPHA needs Python 3.11, 3.12 or 3.13, but '$Python' is $version. Use -Python to choose another."
}
$venvPython = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $Python -m venv (Join-Path $project ".venv")
}
& $venvPython -m pip install --quiet -r (Join-Path $project "requirements.txt")
Push-Location $project
try {
    & $venvPython -m alpha.voice.wake_model
    & $venvPython -m alpha.voice.speech_model
    & $venvPython -m alpha.shortcut
} finally {
    Pop-Location
}
