# EVP — AI Blueprint-Based Automatic Video Editor

An external AI (Claude, ChatGPT…) analyses your video and writes a **JSON editing blueprint**. EVP validates it, executes every instruction automatically at exact timestamps (keying, background replacement, colour grading, enhancement, text, audio) and leaves the result as a fully editable, non-destructive project: timeline, properties panel, undo/redo, preview, export.

> **Status: working core, not a finished commercial product.** Read the [capability matrix](docs/ARCHITECTURE.md#6-capability-matrix-honest-status) — AI background removal (Robust Video Matting, GPL-3.0, personal use), face tone correction and 2D face relighting (YuNet) work; AI upscaling is **not** implemented and is rejected with an explicit error; the Electron shell is written but has not been launched in this environment; nothing has been benchmarked on a GPU or on long 4K footage.

## What works (and is tested)
* Blueprint v1.0 schema, strict validation with 30+ error/warning codes, dependency + overlap resolution, frame-exact timing → [docs/BLUEPRINT.md](docs/BLUEPRINT.md)
* **AI background removal without a green screen** (Robust Video Matting via ONNX Runtime, CPU or GPU), with hair detail, despeckle and halo removal
* **Face & skin-tone correction and face relighting** with automatic face tracking (YuNet), skin-only colour changes, flicker-free auto exposure
* FFmpeg pipeline: chroma key + despill + edge erode/feather, background replacement (image/video/colour, fit/zoom/offset/blur), full colour grading incl. curves/LGG/LUT, denoise/sharpen/deshake, text, audio gain → [docs/OPERATIONS.md](docs/OPERATIONS.md)
* Jobs with progress, ETA, pause/resume/cancel/retry, GPU→CPU fallback, atomic output, original never touched
* Projects: folders, autosave + crash recovery, versions, relink, recent
* React UI: asset/blueprint panels, multi-track timeline (drag/resize/split/duplicate/disable/delete), generated properties panel, before/after, undo/redo, export dialog
* **101 backend tests** (pixel-level render assertions, security, recovery, API) + **12 frontend logic tests**

## Install
```bash
# prerequisites: Python 3.10+, FFmpeg + ffprobe on PATH (>=5), Node 18+
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
cd electron && npm install && npm start      # desktop app
# or headless:
python -m evp.cli run --video my.mp4 --blueprint my.blueprint.json --out out.mp4   # run from backend/
```
Optional: NVIDIA/Intel/Apple hardware encoders are used automatically when a real test encode succeeds. Installers: `cd electron && npm run dist` (untested).

## Docs
[User guide](docs/USER_GUIDE.md) · [Blueprint format](docs/BLUEPRINT.md) · [Operation reference](docs/OPERATIONS.md) · [Architecture & plan](docs/ARCHITECTURE.md) · [Developer guide](docs/DEVELOPER.md)
