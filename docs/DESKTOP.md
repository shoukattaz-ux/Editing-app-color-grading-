# Desktop app (Windows installer)

The desktop app is an Electron window around the local engine. In the **installed** app the engine is a self-contained
executable (Python, FFmpeg and all libraries are bundled): nothing else needs to be installed.

## Build the Windows installer
On a Windows PC with Python 3.12 and Node.js 20+:
```
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```
Output: `release\EVP Editor Setup <version>.exe` (installer: choose folder, desktop and Start-menu shortcuts) and a portable `.exe`.

Without a Windows PC: run the GitHub Actions workflow **Desktop app** (Actions tab > Desktop app > Run workflow). It runs the
tests, freezes the engine, bundles FFmpeg, builds the installer, starts the unpacked app once as a smoke test and uploads the
installer as a build artifact.

Steps, if you want to run them by hand: `npm run build` in `frontend`; `python scripts/fetch_ffmpeg.py`;
`python scripts/build_engine.py`; `npx electron-builder --win` in `electron`.

## What the app does around the editor
* start-up window while the engine starts; remembered window size/position; single instance;
* strict content-security-policy, no navigation, no pop-up windows; the engine listens on 127.0.0.1 with a per-launch secret;
* File > Open logs folder / Open data folder; Help > Problem reports;
* the engine is stopped when the window closes; an unexpected engine stop is reported with the log tail.

## Problem reports to your Cloudflare R2 bucket
Settings (gear icon) > Problem reports: endpoint (`https://<account>.r2.cloudflarestorage.com`), bucket name, Access Key ID and
Secret Access Key (an R2 API token with *Object Read & Write* on that one bucket). When a render fails the app uploads a small
zip (error, log tail, app/OS/FFmpeg versions, your blueprint with file names only, never media) to `reports/YYYY/MM/DD/...`.
Rules: at most 6 automatic reports per hour, the same error only once, failed uploads are queued and retried, "Send test report"
checks the connection, and you can switch automatic sending off.

**Keys are never part of the source code or the installer.** They are stored in `<user data>/evp/report.json` (owner-only
permissions) or come from the environment (`EVP_REPORT_ENDPOINT`, `EVP_REPORT_BUCKET`, `EVP_REPORT_ACCESS_KEY_ID`,
`EVP_REPORT_SECRET_ACCESS_KEY`). Do not paste them into chats or screenshots; if they have been shared, rotate them in the
Cloudflare dashboard (R2 > Manage API tokens). If you ever hand the installer to other people, do not give them your R2 keys:
put a small Cloudflare Worker in front of the bucket that accepts reports and holds the keys itself.

## Updates
The installed app checks GitHub Releases a few seconds after start and from Help > Check for updates. When a newer build exists it
asks before downloading, and again before restarting to install ("Restart and install" or "Install when I quit"); it never
restarts by itself, so an export in progress is never interrupted. Every workflow run publishes `EVP-Editor-Setup-0.1.<run number>.exe`
plus `latest.yml` and a block map (so updates download only the changed parts). Development runs and the portable version are not
updated automatically. The updater only trusts files published to this repository's releases; Windows still shows the
"unknown publisher" warning because builds are not code-signed (a signing certificate removes it).

## Running the latest code from source (VS Code) - no installer needed
Code changes are pushed to GitHub; you pull them and run the app from the source folder:
```
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```
It switches to the working branch, pulls, installs missing dependencies, rebuilds the interface and starts the desktop window.
Needs Python 3.12, Node.js 20+ and FFmpeg on PATH. The installer (`scripts\build-windows.ps1` or the Desktop app workflow) is only
for giving the app to a computer that has none of these; it is never needed for development.
