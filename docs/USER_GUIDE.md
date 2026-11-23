# User guide

## Install (from source)
See the [README](../README.md#install). You need Python 3.10+, FFmpeg/ffprobe on `PATH` (≥ 5; checked on 6.1), Node 18+.

## Workflow
1. **New project** (name, folder, optionally the video). The video is only *referenced* — it is never modified. Projects are folders ending in `.evproj`.
2. **Media ▸ Import video** (MP4, MOV, MKV, AVI, WebM). Corrupt files are rejected with an explanation.
3. **Assets ▸ Import asset** for any background images/videos and `.cube` LUTs the blueprint uses. Give each the **same id** the blueprint uses (the id is derived from the file name; the blueprint's own `assets` list also works once the files exist under `assets/`).
4. **Blueprint**: paste (or *Import JSON*), **Validate** — errors/warnings are listed; click one to select the operation. **Execute Blueprint** validates again, applies it to the project (one undo step) and renders the preview. Nothing is executed if there are errors.
5. **Edit manually**: click any bar in the timeline. Drag to move, drag the edges to resize, *Split* at the playhead, Duplicate, Disable (checkbox), Reset, Delete (Del key). Every parameter is in the right-hand panel. Undo/redo with Ctrl+Z / Ctrl+Shift+Z.
6. **Preview**: *Original*, *Edited*, *Before/After* wipe. *Edited* plays a 640 px proxy clip; when you edit, the proxy is marked out-of-date and the view switches to an exact 960 px **live frame** at the playhead until you press *Update preview video*. Final export is always full quality.
7. **Export**: resolution, fps, codec, CRF/bitrate, container, audio bitrate. Progress, ETA, Pause/Resume/Cancel/Retry, and the FFmpeg log are shown. Files go to `<project>.evproj/exports/`.
8. **Save** (Ctrl+S) keeps a restorable version (Projects tab). Edits are also autosaved a second after each change; after a crash you'll be offered recovery.

## AI background removal (no green screen)
*Backgrounds ▸ AI remove background* adds `background_removal` with `mode: "ai"` over the whole clip; then pick a background (image, video or colour). The first use downloads the 15 MB model to `~/.evp/models`. The matte is computed once per clip/settings (progress shows "AI matting … frame n/N"; about 10 frames/s on a 4-core CPU at 720p) and then reused, so later previews and exports are fast.
* `detail`: **fast** (default) is the most robust on busy or patterned walls; **balanced/high** give finer hair on plain backgrounds but are slower and may pick up wall texture.
* `despeckle` removes background blobs not touching the person; `matte_threshold` removes faint haze; `edge_color_fix` removes the old background's colour from hair edges (halo).
* Works for people only. Fast hand movement (motion blur) can look partly transparent.
* Phone videos recorded in portrait are handled automatically (rotation metadata).

## Shortcuts
Space play/pause · ←/→ frame step · Ctrl+Z / Ctrl+Shift+Z undo/redo · Ctrl+S save · Del delete operation · Ctrl+wheel on the timeline zooms.

## Tips & limits
* Greenscreen: raise *similarity* if green remains, raise *blend* for softer edges, use *edge_erode* 1–2 to remove fringe, *spill_strength* for green cast on hair/skin.
* Features that still need a model (relighting, face tone, AI upscaling) appear disabled in *Effects* with the reason.
* Playback of *Edited* is a proxy; colours/edges can only be judged exactly in the live frame or the final export.
* Pause/resume uses POSIX signals: available on macOS/Linux, not on Windows.
