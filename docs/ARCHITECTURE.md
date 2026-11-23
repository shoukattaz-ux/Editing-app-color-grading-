# Architecture plan

## 1. Findings (environment inspection)
The repository was empty (greenfield). Build environment: Linux, 4 CPU, 15 GB RAM, **no GPU**, Python 3.13, Node 22, **FFmpeg 6.1** with `chromakey`, `despill`, `curves`, `colorbalance`, `lut3d`, `exposure`, `vibrance`, `hqdn3d`, `unsharp`, `deshake`, `drawtext`. No ML models are installed.

## 2. Decisions
| Concern | Choice | Why / licensing |
|---|---|---|
| Media processing & encoding | **FFmpeg** (invoked as a subprocess with an argument *list*, never a shell string) | Mature, frame-accurate, hardware encoders (NVENC/QSV/VideoToolbox). Your FFmpeg build's licence applies (LGPL/GPL). We link nothing; we call the binary. |
| Engine | **Python 3.10+ / FastAPI** local service | Easy to add OpenCV/PyTorch modules later. Bound to `127.0.0.1`, per-launch secret token, CORS open only because the token is required. |
| UI | **React + TypeScript + Vite**, hosted in **Electron** | Electron shell starts the engine and gives native file dialogs. |
| Preview | Same compiled pipeline as export: **single-frame render** (960 px, any time, exact) + **640 px proxy clip** | "Preview == render" by construction; no second implementation to drift. |
| AI background removal | **Robust Video Matting (MobileNetV3) via ONNX Runtime** | Best hair detail of the candidates; temporally stable (recurrent). GPL-3.0 – fine for personal use, re-evaluate before distributing. Runs on CPU (~10 fps at 720×1280 on 4 cores) and uses CUDA/DirectML/CoreML automatically when the matching onnxruntime build is installed. MediaPipe was evaluated and dropped: its current Python package needs system GL/EGL libraries. Weights (15 MB) download on first use, SHA-256 verified. |
| Face detection & tracking | **YuNet (OpenCV Zoo, MIT) via `cv2.FaceDetectorYN`** | 230 KB, ~10 ms/frame on CPU, gives 5 landmarks; no extra runtime. Faces are tracked (IoU matching, temporal smoothing, short hold on missed frames). |
| Face tone / relight application | Analysis pass writes a small lossless **control video** (tone / brighten / darken weights); FFmpeg blends gain/colour streams with `maskedmerge` | Keeps face work inside the normal ordered pipeline (after compositing, before the global grade) so preview == export; pixels with zero weight pass through bit-exactly. |
| AI upscaling | **Real-ESRGAN general x4 v3 (SRVGGNetCompact, BSD-3)**, official weights converted to ONNX on first use **without PyTorch** (restricted unpickler that can only rebuild tensors; conversion verified against an independent NumPy implementation) | Fast "compact" variant chosen so CPU is usable (~8.5 µs per input pixel on 4 threads, e.g. 0.5 s/frame for 320×180 → 1280×720). GPU via the same ONNX Runtime providers as matting, with CPU fallback if the GPU fails mid-run. Hugging Face ONNX exports were unreachable from the build environment, hence the converter. |

## 3. Components
```
Electron main ──spawns──▶ python -m evp.server  (prints {"port","token"} on stdout)
   │ preload (contextIsolation)                         │
   ▼                                                    ▼
React UI  ───HTTP+token──▶  server.py  ─▶  engine.prepare()  ─▶ blueprint.py (parse+validate)
 • model.ts (pure doc ops,                               │          registry.py  (ONLY allowed ops)
   undo/redo, timeline math)                             ▼
 • Timeline / Properties (generated                  planner.py  (bind alpha sources, conflicts,
   from registry schema)                                          dependency-aware ordering)
                                                         ▼
                                                    compiler.py (Plan ▶ one FFmpeg filter graph)
                                                         ▼
                                              engine.Job / JobManager  (progress, pause, resume,
                                                         cancel, retry, GPU→CPU fallback)
project.py: non-destructive project folders, versions, autosave/recovery, relink
security.py: path policy (no traversal / URLs / symlink escape), output-never-source
```

## 4. Operation registry (`backend/evp/registry.py`)
Single source of truth: each operation declares type, title, timeline track, pipeline *stage*, and typed parameters with defaults and limits. The validator, the compiler, `/api/schema`, the UI properties panel, `docs/blueprint.schema.json` and `docs/OPERATIONS.md` are all generated from it. A blueprint cannot invoke anything not in the registry and cannot carry code.

Pipeline stages (execution order, subject to `depends_on`): restore (10) → key (20) → composite (30) → grade (40) → finish (50) → overlay (60) → audio (70).

## 5. Execution pipeline
1. **Parse** JSON (size-limited) → 2. **Validate** structure, version, ids, timestamps (seconds, `mm:ss.ms`, `hh:mm:ss.ms`; snapped to the output frame grid, warned when snapped), parameters (type/range/enum), asset existence/type/path safety, unsupported ops → errors, never silent skips.
3. **Plan**: bind each `background_replacement` to a covering alpha source (explicit `depends_on` or auto), detect overlap conflicts (two replacements overlapping = error; stacked grades/keys = warning), topologically order by `(stage, start, blueprint order)` honouring dependencies, drop disabled ops, reject missing alpha sources.
4. **Compile** to a single `filter_complex` (ranged ops use FFmpeg timeline `enable=` with frame-exact boundaries, or split/overlay for filters without timeline support). 5. **Run** as a job. The original is opened read-only; output is written to `*.part` and atomically renamed; cancelled/failed jobs delete the partial file.

## 6. Capability matrix (honest status)
| Capability | Status |
|---|---|
| Green/blue/custom-colour chroma key, spill suppression, edge erode/feather | ✅ implemented (FFmpeg) |
| Background removal – `chroma` mode | ✅ |
| Background removal – `ai` mode (no green screen): person matting with hair detail, despeckle, edge colour decontamination (model foreground colour in soft edges) | ✅ RVM. Matte cached per project and reused by previews/proxies/exports. Checked by eye on two real phone clips (busy wallpaper + low light). Limits: motion-blurred hands become partly transparent; with `detail` balanced/high, busy patterned walls next to the body can leak in (default `fast` is most robust). Only people are matted (not objects/pets). |
| Separate hair refinement pass for **chroma** keys | ❌ (AI mode already gives hair-level alpha) |
| Background replacement: image / video (looped) / colour, fit, zoom, offset, blur, brightness/saturation matching | ✅ |
| Depth-aware compositing, perspective matching | ❌ not implemented |
| Colour grading: exposure, contrast, brightness, highlights/shadows/whites/blacks, temperature/tint, saturation, vibrance, gamma, lift/gamma/gain, RGB curves, `.cube`/`.3dl` LUT (with intensity), 6 presets | ✅ |
| Colour wheels UI | ❌ (the underlying LGG values are editable as numbers; no wheel widget) |
| Keyframed parameters | ❌ parameters are constant per operation; use split/duplicate to change values over time |
| Face & skin-tone correction: auto face exposure toward a target (temporally smoothed), manual face EV, skin-only warmth/tint/saturation measured from the person's own cheek colour (any skin tone) | ✅ No smoothing/reshaping, so identity is untouched. Checked on two real clips: no added flicker (jitter 1.55 → 1.10). Limits: profile/heavily turned or very small faces may not be detected (detection_confidence); neck/hands are not corrected. |
| Face relighting: key light side/height/intensity, fill (lifts darker areas), shadow depth | ✅ **2D image-space approximation** (directional gain over the face ellipse), no 3D geometry: good for gentle corrections, cannot cast new shadows or create a rim light. Background-light matching not implemented. |
| Denoise (hqdn3d), sharpen (unsharp), basic stabilisation (`deshake`, single pass) | ✅ (classical; limited by source) |
| AI super-resolution (x2/x4 and anything in between; tiled for 4K; denoise via Real-ESRGAN DNI; strength blend with Lanczos) | ✅ CPU tested (180×320 → 720×1280: sharpness 5 → 28 vs 35 for the true original, 0.48 s/frame). **GPU path not tested here (no GPU)**; provider selection + fallback are unit-tested with a simulated GPU failure. Reconstructs *plausible* detail: may smooth skin slightly and redraw fine texture. |
| Text overlay (static; `expansion=none`, text passed via file) | ✅ |
| Audio gain / fades | ✅ |
| Timeline: tracks, move/resize/split/duplicate/delete/disable, zoom, scrub, snapping to frames | ✅ for **operations**. Multi-clip video editing (cutting/rearranging the *source footage*) is ❌ not implemented: one source video per project |
| Undo/redo (coalescing), autosave, crash recovery, versions, relink | ✅ |
| Export MP4/MOV, H.264/H.265, 720p–4K, fps, CRF/bitrate, audio bitrate, progress/ETA/pause/resume/cancel/retry/log | ✅. Hardware encoders are auto-probed with a real test encode and fall back to CPU. **Not tested on a GPU** (none available). |
| Electron shell, installer | ⚠️ written, syntax-checked, **never launched** (no display/Electron binary here); installer not built |

## 7. Roadmap
Phase 1–3 (shell, project mgmt, blueprint engine, FFmpeg, timeline, keying, replacement) and the classical parts of phase 4–6 are done. Done since: AI background removal (RVM), face tone correction and 2D relighting (YuNet). AI upscaling (Real-ESRGAN) also done. Next: (a) rim light using the AI matte; (b) keyframes; (c) multi-clip source timeline; (d) colour-wheel widgets and scopes; (e) packaged installers & CI on macOS/Windows; (f) performance benchmarks on real GPUs.
