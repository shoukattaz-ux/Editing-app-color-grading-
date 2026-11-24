# Run the latest code from source (no installer): pull, install, build the interface, start the desktop window.
# Needs: git, Python 3.12 (python on PATH), Node.js 20+, FFmpeg + ffprobe on PATH.
param([string]$Branch = "ccr-1fd2c60d-4i1i2q", [switch]$NoPull)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($tool in "git", "python", "node", "npm", "ffmpeg", "ffprobe") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "'$tool' was not found on PATH. Install it first (see docs/DESKTOP.md)." }
}
if (-not $NoPull) {
  git fetch origin $Branch
  git checkout $Branch
  git pull origin $Branch
}
# Windows: the DirectML build of onnxruntime (runs the AI on your graphics card) replaces the CPU-only one; they cannot coexist
if (python -m pip show onnxruntime 2>$null) { python -m pip uninstall -y onnxruntime onnxruntime-directml }
python -m pip install -q -r backend/requirements.txt
Push-Location frontend; if (-not (Test-Path node_modules)) { npm ci }; npm run build; Pop-Location
Push-Location electron; if (-not (Test-Path node_modules)) { npm ci }; npm start; Pop-Location
