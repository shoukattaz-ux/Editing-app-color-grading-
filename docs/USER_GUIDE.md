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

## Cutting and trimming your footage
The **Video** track shows your footage as clips. The original file is never changed; clips are just ranges of it.
* **Cut**: put the playhead where you want the cut and press **Cut** (or **C**). The clip splits in two; nothing changes in the picture.
* **Delete a clip**: click it, then **Delete clip** (or Del). Everything after it moves earlier (ripple), and so do effects. Effects that were only on the deleted part are removed (you'll be told how many).
* **Trim**: drag a clip's left or right edge, or type exact *in/out* times in the properties panel. Later footage and effects follow.
* **Reorder / repeat**: select a clip and use **◀ Earlier / Later ▶**, or **Duplicate** to repeat it. Effects keep their timeline times when you reorder (they don't follow the footage).
* Audio is cut at exactly the same points, with a tiny fade at each join to avoid clicks. All of it is undoable (Ctrl+Z).
* **Effects are timed on the cut timeline.** If you paste an AI blueprint that has no `clips`, nothing changes; if it has `clips`, its effect times are after the cuts (see [BLUEPRINT.md](BLUEPRINT.md#cutting-clips)).
* *Original* preview plays your cut timeline by jumping between clips (small hiccups at joins are normal there); *Edited* is the real result.
* One video per project for now, and changing a cut recomputes AI effects (matting, face, upscaling) for that range, so make your cuts *before* running heavy AI.

## Resize / move the subject and cast a shadow
On `background_replacement`: `subject_scale`, `subject_x`, `subject_y` and `subject_anchor` (`bottom` keeps the cut-off shoulders on the frame's bottom edge) resize and reposition the keyed person; `shadow`, `shadow_blur`, `shadow_dx`, `shadow_dy` add a soft cast shadow on the background (offset away from the key light). Make the subject **larger** freely; making it **smaller** or moving it sideways shows the footage's own frame edge where the person touches it (shoulders) - the app warns with `SUBJECT_EDGE_VISIBLE`. The shadow is the subject's silhouette blurred and offset, i.e. a shadow on a wall behind the person; it does not model a floor or 3D light. These settings are not animatable.

## Studio Look: make a replaced background look real
A cut-out subject on a new background looks pasted-on because the two were never lit together. Add one `studio_look` operation after the background replacement and the app does what a compositor does by hand:

1. **Ambient match** - the background's colours fall on the subject, in the direction the light is in the scene (a blue neon on the right tints the right side of the face).
2. **Light wrap** - background light spills over the subject's edges, which removes the cut-out outline.
3. **Depth** - the background is softened slightly so the subject stands out.
4. **Finish over everything** - S-curve contrast, shadow/highlight colour (split-tone), soft glow around bright lights, vignette, local contrast, saturation and fine film grain, so subject and background share one look.

Pick a preset (`studio_warm`, `studio_cool`, `cinematic`, `clean_bright`) and override any control, e.g. `{"preset": "studio_cool", "glow": 0.5, "grain": 0}`. Steps 1-3 need a `background_replacement` in the same time range; without one the app says so (`STUDIO_NO_BACKGROUND`) and applies step 4 only. **Auto match (`auto_match`, 0-1):** the app measures your background and your subject and sets every control you did not set yourself, the way a colourist reads a scene: the background's light and shadow colours become the highlight/shadow tint; where the background is brightest decides which way the cast shadow falls; the background's tonal range, darkness, colourfulness, blown highlights and sharpness set contrast, vignette, ambient colour, glow and depth; the subject's highlights versus the background set a small exposure correction (`subject_exposure`) and the brightness just behind the edge sets the light wrap. A **skin guard** then renders the result and, if skin tones moved more than about 5 degrees of hue, eases off the colour-casting controls and re-checks. Values are deliberately conservative; your own values always win, and the measured numbers (including the skin check) are reported with the plan. `{"type":"studio_look","parameters":{"preset":"clean_bright","auto_match":1.0}}` is a complete look for most footage. It reads one frame (the middle of the look's range), so a scene whose lighting changes a lot needs several looks.

**Match a reference picture:** set `reference_asset_id` (an image asset) and `reference_strength`. The app renders the frame, measures how its colour and tone differ from your reference per channel, and adds the correction as RGB curves (capped, so differing content cannot wreck the picture). It matches colour and tone only - not framing, content or lighting direction, so a reference with a very different background colour balance can pull skin tones; use a lower strength then. `samples/studio_creator.blueprint.json` is a worked green-screen-to-studio example tuned against one reference.

Honest limits: it does not re-light the subject in 3D or cast shadows onto the scene - it is a colour and edge integration; for a strong result choose a background whose light direction roughly agrees with your footage, and use `face_relighting` for the face.

## Colour wheels
Select a **Color Grading** effect: the **Colour wheels** section has **Lift** (shadows), **Gamma** (midtones) and **Gain** (highlights). Drag the puck toward a colour to push that tone range toward it (the further from the centre, the stronger); the **Master** slider brightens or darkens the range without changing its colour. **Shift-drag** = fine adjustment, **double-click** (or Delete, or *Reset*) = back to neutral, arrow keys move the puck (Shift = bigger steps). The three R/G/B boxes show and accept exact values, so a setting loaded from a blueprint appears at the right place on the wheel.
What they do (classic lift/gamma/gain, per channel): **Lift** `out = in + lift·(1−in)` moves the black point and tapers off toward white; **Gain** `out = in·(1+gain)` scales and keeps black; **Gamma** `out = in^(2^−gamma)` bends the midtones and keeps black and white. Wheels are static (they cannot be keyframed). Watch the effect on the **Scopes**.

## Scopes
The **Scopes** tab (left rail) measures the frame at the playhead — the **graded** result by default, or the **original** — using the BT.709 standard. While the video plays it follows the playing video.
* **Waveform** — brightness (up) for every picture column. Whites near the top, blacks near the bottom, no flat line pressed against 0 or 100.
* **RGB Parade** — red, green, blue waveforms side by side. In a neutral (grey/white) area the three should line up: a quick white-balance check.
* **Vectorscope** — hue is the angle, saturation the distance from the centre. Neutrals sit in the middle; yellow boxes are the 75 % colour-bar targets (R, Mg, B, Cy, G, Yl); the dashed **skin line** is the reference for faces — real skin gathers in a band of roughly ±20° around it, a little further round for warmer/tanned skin. Pull a face toward the line with the Gain/Gamma wheels or *Face & Skin Tone*.
* **Histogram** — pixel counts per level; spikes at either end mean clipping.
* Readouts: **clipped highlights**, **crushed blacks** and **average luma**. *Intensity* brightens a sparse scope; *Graticule* toggles the scale.
Scopes are measured from the real pixel values of an 8-bit frame (360 px wide), not from what your display makes of them.

## Keyframes: animating effects
Select an effect; every parameter that can be animated has four small buttons: **◀ ◆ ▶ ⏱**.
1. Move the playhead where the animation should start and press the **stopwatch (⏱)**. The parameter now animates and shows a **curve graph**; the first keyframe holds the current value.
2. Move the playhead and change the value (slider or number): that **creates a keyframe** there. Between keyframes the box shows the interpolated value. **◆** adds/removes a keyframe at the playhead; **◀ ▶** jump to the previous/next keyframe. Pressing ⏱ again stops animating and keeps the value at the playhead.
3. In the graph: **drag** a point to change its time and value, **click** empty space to add a keyframe, **double-click** a point (or press Del) to remove it, and pick the **easing** of the selected keyframe (linear, hold, ease in, ease out, ease in-out). The red line is the playhead; dimmed margins are outside the effect but still shape the curve.
4. Keyframe diamonds also show on the effect's bar in the timeline.
* Examples: a slow **zoom/pan on the background** (animate `scale`, `offset_x`), **fade-in titles** (text `opacity`), a moving **lower third** (`x`, `y`), a grade that warms up (`temperature`), **audio ducking** (`gain_db`), a **moving key light** (relight `key_angle`).
* Moving an effect moves its keyframes; dragging only its **left** edge keeps keyframes where they are on the timeline; **Split at playhead** gives both halves the exact original curve; cutting footage out removes the keyframes inside the cut and moves later ones earlier (the value glides across the gap).
* Not everything can be animated — blur, denoise, sharpen, LUT intensity, colours and choices cannot (the validator says why). Previews show the animation exactly as the export will.

## AI background removal (no green screen)
*Backgrounds ▸ AI remove background* adds `background_removal` with `mode: "ai"` over the whole clip; then pick a background (image, video or colour). The first use downloads the 15 MB model to `~/.evp/models`. The matte is computed once per clip/settings (progress shows "AI matting … frame n/N"; about 10 frames/s on a 4-core CPU at 720p) and then reused, so later previews and exports are fast.
* `detail`: **fast** (default) is the most robust on busy or patterned walls; **balanced/high** give finer hair on plain backgrounds but are slower and may pick up wall texture.
* `despeckle` removes background blobs not touching the person; `matte_threshold` removes faint haze; `edge_color_fix` removes the old background's colour from hair edges (halo).
* Works for people only. Fast hand movement (motion blur) can look partly transparent.
* Phone videos recorded in portrait are handled automatically (rotation metadata).

## Face correction and relighting
*Effects ▸ Face*: **Face & Skin Tone Correction** finds and tracks faces automatically, evens out face brightness toward `target_brightness` (smoothly, so it never flickers) and lets you change skin warmth/tint/saturation without touching beard, eyes, hair or the background. **Face Relighting** adds a soft virtual key light from the side you choose (`key_angle` −90 = frame-left … +90 = frame-right), a `fill` that lifts dark areas, and optional `shadow_depth`. Both only change brightness/colour — never skin texture or face shape. Relighting is a 2D effect: keep it gentle. The face detector (230 KB) downloads on first use; analysis runs at ~25 fps on CPU and is cached.
If no face is found the operation does nothing and the export log says so.

## AI upscaling (CPU or GPU)
1. Set the output resolution larger than the source (Output settings or Export, e.g. 1920x1080 or 3840x2160).
2. *Effects ▸ Enhance ▸ AI Resolution Enhancement*. It replaces the normal scaler, so all other effects then work on the sharper frames.
* `model` — pick by footage and hardware (CPU times per frame for a 320×180 source, 4 threads):

  | model | best for | CPU |
  |---|---|---|
  | **general** (default) | real-world footage, any factor; the CPU choice | ~0.5 s |
  | **quality** (Real-ESRGAN x4plus) | finest natural detail for 3–4× jobs; use with a GPU | ~6 s |
  | **quality_x2** (Real-ESRGAN x2plus) | 1.5–2× jobs (e.g. 1080p → 4K, 720p → 1440p) | ~1.4 s |
  | **anime** | cartoons, anime, flat graphics (makes real faces look flat) | ~0.3 s |
* `grain` (0–1) re-adds the source's own fine grain so the result looks camera-made rather than painted.
* `quality`: **fast** (default) halves the network input when the needed factor is small enough (about 4× faster); **best** always uses the full frame.
* `denoise` 0 keeps texture (and noise), 1 smooths strongly; `strength` < 1 blends with a classical upscale for a more natural look.
* `temporal_stability` (default 0.6) keeps AI detail steady from frame to frame (no shimmer) by following the motion; it only blends where the motion estimate is reliable, so moving things don't leave trails.
* `color_fidelity` (on) keeps the original's colour, exposure and contrast exactly — the AI only contributes fine detail, so your grade is not altered.
* `face_detail` (default 0.8) applies less AI detail on faces to avoid plastic skin and redrawn features (identity-changing face-restoration models are deliberately not used).
* The live single-frame preview primes the temporal filter with 2 earlier frames; the export uses the full history, so fine detail can differ very slightly between them.
* **Speed**: CPU ≈ 0.5 s per frame for a 320×180 source, ≈ 2 s for 640×360. The validator warns with an estimate when it will take over a minute. Results are cached, so later previews/exports are fast.
* **GPU**: replace `onnxruntime` with `onnxruntime-gpu` (NVIDIA, needs CUDA 12 + cuDNN 9) or `onnxruntime-directml` (Windows, any DirectX 12 GPU). macOS uses CoreML automatically. The Export dialog shows the AI device in use. If the GPU fails (e.g. out of memory) the job continues on CPU and says so in the log.
* It invents *plausible* detail; it cannot recover what the camera never captured.
* **Check it at pixel level**: Preview ▸ *Before/After* ▸ choose **1:1 zoom / 200% / 400%** — before and after are shown side by side at full output resolution; drag either side to move around (both stay locked together).
* For portrait (phone) video the resolution menus offer portrait sizes (e.g. 1080×1920) and a "2× source" choice.

## ProRes export (for editing in other software)
Export ▸ Codec ▸ **ProRes 422 10-bit** (container switches to .mov, audio becomes uncompressed PCM). Profiles: Proxy, LT, 422, 422 HQ. Note: the processing pipeline is 8-bit, so ProRes gives a high-quality, edit-friendly intermediate file but does not add colour precision beyond 8 bits.

## Shortcuts
C cut at playhead · Space play/pause · ←/→ frame step · Ctrl+Z / Ctrl+Shift+Z undo/redo · Ctrl+S save · Del delete effect or clip · Ctrl+wheel on the timeline zooms.

## Tips & limits
* Greenscreen: raise *similarity* if green remains, raise *blend* for softer edges, use *edge_erode* 1–2 to remove fringe, *spill_strength* for green cast on hair/skin.
* Playback of *Edited* is a proxy; colours/edges can only be judged exactly in the live frame or the final export.
* Pause/resume uses POSIX signals: available on macOS/Linux, not on Windows.
