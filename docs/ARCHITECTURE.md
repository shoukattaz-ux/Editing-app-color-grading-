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
| Colour accuracy of AI round trips | Decoding for analysis uses an explicit accurate-rounding YUV→RGB conversion and AI intermediates stay RGB; FFmpeg's implicit default conversion was measured to darken by ~0.5 level | Full-range phone video now round-trips within ~0.1 level. |
| Colour fidelity of RGB-domain effects | The graph is 4:4:4 between one lossless chroma upsample (duplication) on entry and one box-average downsample on exit. Each run of RGB filters (lutrgb, curves, channel mixer, exposure, vibrance, LUT, compositing) sits between conversions that **name the clip's matrix** (read from the file's tags; untagged HD = BT.709, SD = BT.601) and use 16-bit RGB inside. Effects with a time range run on a split branch merged back by `overlay`, so frames outside the range are never converted. The output is tagged with the matrix it was encoded with. | FFmpeg's automatic conversions decode with the frame's tag but encode RGB with BT.601, which shifted every RGB effect on HD footage by up to ~28 levels; chroma was also resampled on every frame, even outside an effect's range. Measured now: plain pipeline and frames outside an effect are bit-exact; a neutral effect changes nothing inside its range (mean 0.000); real effects match an independent float calculation within 1 level, including one-pixel colour edges. Full-range (yuvj) sources are squeezed to limited range once on entry, as before. |
| Face tone / relight application | Analysis pass writes a small lossless **control video** (tone / brighten / darken weights); FFmpeg blends gain/colour streams with `maskedmerge` | Keeps face work inside the normal ordered pipeline (after compositing, before the global grade) so preview == export; pixels with zero weight pass through bit-exactly. |
| AI upscaling | **Real-ESRGAN family (BSD-3)**: general x4 v3 and anime-video v3 (SRVGGNetCompact), x4plus and x2plus (RRDBNet; the x2 pixel-unshuffle is mapped to ONNX SpaceToDepth by permuting the first layer's input channels). Official weights converted to ONNX on first use **without PyTorch** (restricted unpickler that can only rebuild tensors; conversion verified against an independent NumPy implementation) | Fast "compact" variant chosen so CPU is usable (~8.5 µs per input pixel on 4 threads, e.g. 0.5 s/frame for 320×180 → 1280×720). GPU via the same ONNX Runtime providers as matting, with CPU fallback if the GPU fails mid-run. Hugging Face ONNX exports were unreachable from the build environment, hence the converter. |

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

### Cuts (edit decision list)
`evp/edl.py` holds the single mapping between timeline frames and source frames. The compiler turns the clips into `fps → split → trim → concat` on one decoder when they are in source order (no buffering: `trim` drops other branches' frames), or one `-ss` input per piece when reordered/duplicated. After `concat` the stream is re-timed onto the exact 1/fps grid (concat changes the time base; a mismatch made overlays drop their last frame). AI passes decode piece by piece with exact seeks, so frame N means the same source frame in the render, the single-frame preview and every analysis pass.

### Keyframes: how animation reaches FFmpeg (chosen by measurement)
FFmpeg offers several ways to change a filter over time; each was tested against a static render of every frame:
| Mechanism | Exact? | Used for |
|---|---|---|
| `sendcmd` runtime commands, one file per linear chain, only options that change | **bit-exact** when the target is in the same single-input chain, downstream of the sendcmd (`eq`, `exposure`, `vibrance`, `colorchannelmixer`, `colorbalance`, `curves`, `chromakey`, `despill`, `drawtext` x/y/size/alpha) | grading, keying, text, background colour, face colour |
| per-frame expressions of `t` (`scale`/`overlay` with `eval=frame`, `volume`) | **bit-exact** for video; ~20 ms steps for audio | background zoom/pan, audio gain |
| analysis-pass per-frame parameters (Python) | exact (same `value_at`) | face tone/relight, upscale strength/grain |
| `sendcmd` into `overlay`/`blend` | ❌ one frame early (multi-input filters look ahead) | not used |
| `gblur` sigma / `hqdn3d` / `blend` opacity at runtime | ❌ inexact | **excluded** — those parameters are not animatable |
| anything placed after a per-frame size change | ❌ FFmpeg re-normalises the size | zoom is therefore the last step before the overlay |
One definition of the maths (`keyframes.value_at`) feeds the command files; `to_expr` generates the equivalent FFmpeg expression and is checked against `value_at` by running FFmpeg's own evaluator; the TypeScript editor mirrors it and is tested on the same shared vectors (`shared/keyframe_vectors.json`).

### Display accuracy: frame PNGs are stripped of colour tags
FFmpeg tags BT.709 frames with `cICP`/`cHRM`/`gAMA` chunks and browsers then colour-convert them: the same pixels measured 0.342 average luma raw but 0.308 as shown (~10 % darker). Live-frame previews, Before/After and the 1:1 loupe were therefore darker than the real pixels and the exported video. `render_frame` now removes those chunks (`evp/pngtools.py`) so every viewer shows the stored values, and the scopes additionally decode with `colorSpaceConversion: "none"`. (Found by checking scope readouts against NumPy.)
Why lift/gamma/gain is a LUT and not `colorbalance`: measured on a ramp, FFmpeg's `colorbalance` "midtones" weighting peaks in the *shadows* (a gamma-style change moved the dark zone by +17.6 and true midtones by 0.4), so wheels built on it would not act where a colourist expects.

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
| Colour wheels (Lift / Gamma / Gain) | ✅ Wheels map an RGB triplet ↔ puck position + master **bijectively** (an existing value appears at the right place), with keyboard, numeric entry, fine adjust, reset. The grade is true per-channel lift/gamma/gain (one exact 1-D LUT per channel, `evp/compiler.py::lgg_filter`), verified against the documented formula to ±3 levels. Static only (arrays are not keyframable) |
| Scopes: waveform, RGB parade, vectorscope (75 % targets, skin line), histogram, clipping readouts; graded/original; live during playback | ✅ BT.709 maths reproduced exactly by an independent NumPy implementation (`shared/scope_vectors.json`) and checked against FFmpeg's YCbCr conversion; the UI readouts equal an independent measurement of the same frame (e2e). **Limits:** measured from 8-bit RGB frames at 360 px (not 10-bit/YUV levels), no legal-range (16–235) mode, no per-region/picker sampling, scopes cannot be undocked |
| Keyframes (animation of numeric parameters; 5 easings; graph editor, stopwatch, prev/next, drag; timeline diamonds; split/trim/ripple-aware) | ✅ for the parameters listed in [BLUEPRINT.md](BLUEPRINT.md#keyframes-animation) (grade, chroma key, background zoom/pan/colour, text position/size/opacity, audio gain, face and upscale strengths). **Not animatable by design:** blur, denoise, sharpen, LUT intensity, colours/enums/arrays — each was measured not to be frame-exact. Verified by an oracle: an animated frame is pixel-identical to a static render at the interpolated value |
| Face & skin-tone correction: auto face exposure toward a target (temporally smoothed), manual face EV, skin-only warmth/tint/saturation measured from the person's own cheek colour (any skin tone) | ✅ No smoothing/reshaping, so identity is untouched. Checked on two real clips: no added flicker (jitter 1.55 → 1.10). Limits: profile/heavily turned or very small faces may not be detected (detection_confidence); neck/hands are not corrected. |
| Face relighting: key light side/height/intensity, fill (lifts darker areas), shadow depth | ✅ **2D image-space approximation** (directional gain over the face ellipse), no 3D geometry: good for gentle corrections, cannot cast new shadows or create a rim light. Background-light matching not implemented. |
| Denoise (hqdn3d), sharpen (unsharp), basic stabilisation (`deshake`, single pass) | ✅ (classical; limited by source) |
| AI super-resolution (x2/x4 and anything in between; tiled for 4K; denoise via Real-ESRGAN DNI; strength blend with Lanczos; models: general compact, x4plus, x2plus, anime; grain preservation) | ✅ CPU tested on a real clip (180×320 → 720×1280, ~0.6 s/frame). Versus the raw network output, the post-process cut the AI's tone shift 0.94 → 0.16 levels, detail flicker 2.50 → 1.98 (the real footage's own noise is 2.15) and over-sharpening 42 → 28 (original 23.5), and removed the plastic look on faces (judged by eye). **GPU path not tested here (no GPU)**; provider selection + fallback are unit-tested with a simulated GPU failure. Reconstructs *plausible* detail. Professional post-process: base/detail split (colour fidelity: low frequencies from the Lanczos upscale), face-aware detail attenuation (YuNet), motion-compensated temporal stabilisation of the detail layer (DIS optical flow on source frames + photometric confidence, so no ghosting). |
| Text overlay (static; `expansion=none`, text passed via file) | ✅ |
| Audio gain / fades | ✅ |
| Timeline: tracks, move/resize/split/duplicate/delete/disable, zoom, scrub, snapping to frames | ✅ for **operations** |
| Cutting the source footage: cut, delete (ripple), trim by dragging/typing, reorder, duplicate; audio follows sample-accurately (4 ms joint fades); effects ripple with edits | ✅ Frame-exact (verified with per-frame-unique test videos, NTSC rate, reordered/duplicated/overlapping clips, audio frequency content and sync, short-audio sources). **Limits:** one source video per project; no speed changes, transitions or multi-track video; reordered/overlapping clips cap at 48 (one decoder each); changing cuts recomputes AI effects in the affected range (AI caches are keyed to the cut list) |
| Undo/redo (coalescing), autosave, crash recovery, versions, relink | ✅ |
| Export MP4/MOV, H.264/H.265, ProRes 422 (Proxy/LT/422/HQ, 10-bit container over an 8-bit pipeline), 720p–4K, fps, CRF/bitrate, audio bitrate, progress/ETA/pause/resume/cancel/retry/log | ✅. Hardware encoders are auto-probed with a real test encode and fall back to CPU. **Not tested on a GPU** (none available). |
| Electron shell, installer | ⚠️ written, syntax-checked, **never launched** (no display/Electron binary here); installer not built |

## 7. Roadmap
Phase 1–3 (shell, project mgmt, blueprint engine, FFmpeg, timeline, keying, replacement) and the classical parts of phase 4–6 are done. Done since: AI background removal (RVM), face tone correction and 2D relighting (YuNet). AI upscaling (Real-ESRGAN) also done. Next: (a) rim light using the AI matte; (b) keyframes; (c) multi-clip source timeline; (d) colour-wheel widgets and scopes; (e) packaged installers & CI on macOS/Windows; (f) performance benchmarks on real GPUs.
