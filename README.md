# EVP — AI Blueprint-Based Automatic Video Editor

An external AI (Claude, ChatGPT…) analyses your video and writes a **JSON editing blueprint**. EVP validates it, executes every instruction automatically at exact timestamps (keying, background replacement, colour grading, enhancement, text, audio) and leaves the result as a fully editable, non-destructive project: timeline, properties panel, undo/redo, preview, export.

> **Status: working core, not a finished commercial product.** Read the [capability matrix](docs/ARCHITECTURE.md#6-capability-matrix-honest-status) — AI background removal (Robust Video Matting, GPL-3.0, personal use), face tone correction, 2D face relighting (YuNet) and AI upscaling (Real-ESRGAN, CPU or GPU) work; the GPU path is untested here (no GPU in the build environment); the Electron shell is written but has not been launched in this environment; nothing has been benchmarked on a GPU or on long 4K footage.

## What works (and is tested)
* Blueprint v1.0 schema, strict validation with 30+ error/warning codes, dependency + overlap resolution, frame-exact timing → [docs/BLUEPRINT.md](docs/BLUEPRINT.md)
* **AI background removal without a green screen** (Robust Video Matting via ONNX Runtime, CPU or GPU), with hair detail, despeckle and halo removal
* **Face & skin-tone correction and face relighting** with automatic face tracking (YuNet), skin-only colour changes, flicker-free auto exposure
* **AI upscaling** (Real-ESRGAN: general, x4plus, x2plus, anime) on CPU or GPU via ONNX Runtime, with temporal stabilisation, colour fidelity, face protection, grain preservation and a 1:1 before/after magnifier
* **ProRes 422 export** (Proxy/LT/422/HQ) for finishing in other software
* FFmpeg pipeline: chroma key + despill + edge erode/feather, background replacement (image/video/colour, fit/zoom/offset/blur), full colour grading incl. curves/LGG/LUT, denoise/sharpen/deshake, text, audio gain → [docs/OPERATIONS.md](docs/OPERATIONS.md)
* Jobs with progress, ETA, pause/resume/cancel/retry, GPU→CPU fallback, atomic output, original never touched
* Projects: folders, autosave + crash recovery, versions, relink, recent
* **Colour wheels** (true lift/gamma/gain) and **scopes** (waveform, RGB parade, vectorscope with skin line, histogram) — scope maths verified against independent NumPy/FFmpeg implementations; previews show the real pixel values
* **Keyframe animation** of effect parameters (graph editor, easings, Ken-Burns zoom/pan, fade titles, gain automation…) — every animated frame verified pixel-identical to a static render at the interpolated value
* **Cutting & trimming the footage** (cut, ripple-delete, trim by dragging, reorder, duplicate) with frame-exact video and audio; effects ripple with the edits
* React UI: asset/blueprint panels, multi-track timeline (drag/resize/split/duplicate/disable/delete), generated properties panel, before/after, undo/redo, export dialog
* **232 backend tests** (pixel-level render assertions, security, recovery, API) + **70 frontend logic tests** + an end-to-end browser check (`npm run e2e`)

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
