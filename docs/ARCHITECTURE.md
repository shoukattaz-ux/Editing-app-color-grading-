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
| Relighting / face correction / upscaling | **Not bundled** | Need further models (face landmarks, relighting, Real-ESRGAN). Declared *unsupported* with a clear error. See §6. |

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
| Face / skin-tone correction, face relighting | ❌ need face-landmark + segmentation models; rejected clearly |
| Denoise (hqdn3d), sharpen (unsharp), basic stabilisation (`deshake`, single pass) | ✅ (classical; limited by source) |
| AI super-resolution | ❌ model needed |
| Text overlay (static; `expansion=none`, text passed via file) | ✅ |
| Audio gain / fades | ✅ |
| Timeline: tracks, move/resize/split/duplicate/delete/disable, zoom, scrub, snapping to frames | ✅ for **operations**. Multi-clip video editing (cutting/rearranging the *source footage*) is ❌ not implemented: one source video per project |
| Undo/redo (coalescing), autosave, crash recovery, versions, relink | ✅ |
| Export MP4/MOV, H.264/H.265, 720p–4K, fps, CRF/bitrate, audio bitrate, progress/ETA/pause/resume/cancel/retry/log | ✅. Hardware encoders are auto-probed with a real test encode and fall back to CPU. **Not tested on a GPU** (none available). |
| Electron shell, installer | ⚠️ written, syntax-checked, **never launched** (no display/Electron binary here); installer not built |

## 7. Roadmap
Phase 1–3 (shell, project mgmt, blueprint engine, FFmpeg, timeline, keying, replacement) and the classical parts of phase 4–6 are done. Done since: AI background removal (RVM). Next: (a) face detection (OpenCV YuNet) → face/skin-tone correction, then relighting; (b) keyframes; (c) multi-clip source timeline; (d) colour-wheel widgets and scopes; (e) packaged installers & CI on macOS/Windows; (f) performance benchmarks on real GPUs.
