# Build the Windows installer on a Windows PC.
# Needs: Python 3.12 (python on PATH) and Node.js 20+. Output: release\EVP Editor Setup *.exe (+ a portable .exe)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install -r backend/requirements.txt pyinstaller
Push-Location frontend; npm ci; npm run build; Pop-Location
python scripts/fetch_ffmpeg.py
python scripts/build_engine.py
Push-Location electron; npm ci; npx electron-builder --win --publish never; Pop-Location
Write-Host "Done. Installer is in the release folder."
