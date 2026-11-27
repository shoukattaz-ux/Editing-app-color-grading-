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

## Transparent export: remove the screen and put nothing behind
Use one `background_removal` (mode `screen` for a green/blue screen, `ai` for ordinary footage) or `chroma_key`, **no** `background_replacement`, and choose a transparent codec in Export (or `output`):
* `"codec": "prores4444", "format": "mov"` - ProRes 4444 with a 16-bit alpha channel; opens with transparency in Premiere, DaVinci Resolve, Final Cut and After Effects. Large files.
* `"codec": "vp9_alpha", "format": "webm"` - WebM VP9 with alpha; plays with transparency in Chrome, Edge and Firefox, and imports into OBS, CapCut and Resolve. Small files (quality: `output.crf`, lower = better).

The key must cover the whole video (`TRANSPARENT_FULL_RANGE`), exactly one key is allowed (`TRANSPARENT_NEEDS_ONE_KEY`), and a background cannot be combined with transparency (`TRANSPARENT_WITH_BACKGROUND`). Colour grading, text and the other effects still apply to the person. Previews and the proxy show a grey checkerboard where the screen was, because ordinary video cannot show transparency. H.264/H.265 and ordinary ProRes cannot carry transparency; the app refuses to pretend they do.

## Professional green/blue-screen key (`background_removal`, `mode: "screen"`)
The simple `chroma_key` removes pixels close to one colour. Hair, fuzzy wool and motion-blurred edges are *mixtures* of subject and screen, so a threshold either leaves a green halo or eats the fine strands. `mode: "screen"` unmixes them instead: the screen colour is found automatically (green or blue) and followed over time; the screen's brightness is modelled **locally**, so uneven lighting, hot spots and shadows on the screen do not leave haze or holes; each edge pixel's partial transparency is solved from how screen-like it is; and the subject's own colour is solved back out of the mixture, so edges carry no green. Spill is removed from the rest of the subject, isolated specks are dropped, and the matte is smoothed over time **only where nothing moved** (no flicker, no ghost trail). No model, no GPU.

Settings: `similarity` (noise floor of the screen, 0.05-0.2), `blend` (protect subject colours close to the screen), `spill_strength`, `edge_shrink` / `edge_soften` (px) to fine-tune the edge, `temporal_smooth`, `fill_holes` (off by default; turn on if you see pinholes), `auto_screen` (off = use `key_color`). Measured on a synthetic hair test with known ground truth on an unevenly lit screen: edge error 5.5 vs 21 levels for the simple key (the simple key loses most fine strands). For footage without any screen use `mode: "ai"`. The old `hair_edge_refinement` operation is not a separate step: this mode and `ai` already do hair-level edges.

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
C cut at playhead · Space play/pause · ←/→ frame step · Home / End jump to start / end · Ctrl+Z / Ctrl+Shift+Z undo/redo · Ctrl+S save · Del delete effect or clip · Ctrl+wheel on the timeline zooms · Esc closes a dialog or popup (otherwise clears the selection). While a dialog is open, no other shortcut acts on the timeline behind it.

## Right-click menus and deleting things
* **Timeline, effects:** right-click an effect bar for **Duplicate**, **Split at playhead**, **Turn off** (keep it but skip it) / **Turn on**, and **Delete effect**. Selecting it and pressing **Del** also deletes it.
* **Timeline, clips (your footage):** right-click a clip for **Cut at playhead**, **Duplicate clip**, **Move earlier / later** and **Delete clip** (later footage and effects move up). The only clip of a project cannot be deleted: cut it first, then delete the part you do not want.
* **Media panel:** right-click a card for **Use / Edit this video**, **Copy file location** and **Remove**; or click a card and press **Del**. Removing a background or LUT deletes the project's own copy; removing an earlier source video only takes it off the list. Your original files are never touched. The video you are editing now cannot be removed from the list (switch to another video first).
* If a filter (Videos / Pictures / LUTs) or the search hides your files, the panel says so and offers **Show all**.

## Small things that help
* Adding or changing an effect switches the preview to **Edited**, so you see the result at once (Original always shows the untouched source).
* **One background at a time:** adding a background while one is already on the timeline at that time changes that one instead of stacking a second one (two at once would be a conflict). Dropping a background card at a later point on the Background track starts the new background there and ends the previous one.
* The status bar at the bottom says what the project needs ("No effects yet", "3 effects ready · 1 note", "1 problem"); click it to see the details in the Blueprint panel.
* Messages in the corner close by themselves; click one to close it sooner.
* The timeline ruler shows minutes:seconds (0:01.5 = one and a half seconds); the timecode next to the play button shows hours:minutes:seconds:frames.

## Tips & limits
* Greenscreen: raise *similarity* if green remains, raise *blend* for softer edges, use *edge_erode* 1–2 to remove fringe, *spill_strength* for green cast on hair/skin.
* Playback of *Edited* is a proxy; colours/edges can only be judged exactly in the live frame or the final export.
* Pause/resume uses POSIX signals: available on macOS/Linux, not on Windows.

## Auto-save and coming back to exactly where you were
* Every change is written to disk within a second (toolbar: "Auto-saved 14:02"), together with the screen you were on (playhead, open panel, zoom, selection, panel sizes). Closing the window, a crash or a power cut loses nothing; closing the window also waits (max 2 s) for the last write.
* The project is under **Recent projects** on the start screen (newest work first) with a badge "auto-saved" when it holds work you never saved. **Continue where you left off** opens it in one click, in the exact state you left. A banner offers **Save now** (makes a numbered version) or **Back to last saved version**.
* Not restored: the Undo history (it starts fresh) and a half-finished render (renders are restarted from the Export dialog).

## Editing is live on one frame; "Apply to full video" renders the rest
While you edit, only the **current frame** (the one under the playhead) is rendered, with the same engine as the export, so every slider shows its effect within a moment and nothing re-processes the whole clip. A frame you have already seen (scrubbing back, Undo) comes back instantly from a small cache. When you are happy, press **Apply to full video** (toolbar, or the button on the preview) to render the whole timeline as a 640px preview video you can play; **Export** makes the final full-quality file. **Execute Blueprint** also shows the current frame first. The **Auto-apply** switch (shows "Auto-apply: off" by default; it was reset to off for everyone) restores the old behaviour of re-rendering the whole preview video by itself after every change, which is slow for long or heavy clips. If you change anything while the whole video is rendering, that render is stopped at once (it was for the old settings) so the live frame stays fast; press Apply to full video again when you are happy. Reopening a project never starts a render by itself. How fast the live frame is depends on your clip's resolution and the effects used (typically a fraction of a second to a few seconds for 1080p; it is a real render, not an estimate).

### Draft live frame (faster tries)
The **Draft** switch next to Auto-apply (off by default) makes the live frame faster while you try settings: the whole frame, the green-screen key included, is processed at preview size (960 px wide) instead of full size and then shrunk. Pixel-sized settings (edge soften / shrink, background blur ...) are scaled with it so the picture looks the same; only the finest edges (single hairs) are a little less exact than the export. Measured on a 1080p screen key with a picture background and a Studio Look: about 1.1 s instead of about 2 s per frame; on 4K footage the gain is larger. A **DRAFT** label sits on the picture while you look at a draft frame. Key views (matte / edge), the 1:1 zoom, Apply to full video and Export always use full quality. For videos up to 960 px wide the switch does nothing (they already work at that size).

### Faster full renders (Apply to full video and Export)
* **The green-screen key uses every processor core:** the clip is split into one piece per core; each piece is primed on the 6 frames before it, so the joins are invisible (the result equals a one-core key frame for frame). It also only does the colour work where the edge is see-through.
* **The final picture is rendered in parallel time segments** (one FFmpeg each, since one FFmpeg keeps only about 2 cores busy), joined without re-encoding; the sound is rendered once over the whole range and added, so it has no joins. Used automatically for clips of 6 s or more; not for stabilised or denoised clips (those effects look at neighbouring frames) or for see-through exports. If anything goes wrong it falls back to a normal one-piece render by itself.
* Measured on a 4-core computer, 8 s of 1080p with a screen key, a picture background and a Studio Look: Export from scratch 155 s → 72 s. More cores give more.

## GPU indicator, and why a Radeon may look idle
The toolbar chip shows what the graphics card is doing: **GPU active · 41%** (green, pulsing), **GPU ready · idle** (blue), **GPU not used** (amber). **Click it** for a checklist of what runs where, with the exact fix for each problem:
* **AI** (AI keying, hair detail, upscaling) runs on your Radeon through **DirectML** (needs the `onnxruntime-directml` package; `scripts\dev.ps1` installs it and removes the CPU-only `onnxruntime`, which would otherwise take over). The card with the most video memory is chosen (Settings > AI device to change).
* **Video encoding** (export, preview video) uses AMD **AMF** (`h264_amf` / `hevc_amf`) when your FFmpeg build has it and a test encode works; otherwise the panel shows FFmpeg's error and the fix (usually: update the Radeon driver, or use a "full" FFmpeg build).
* **Video decoding** of the source in exports and preview videos uses the card (d3d11va / dxva2) on Windows; if it ever fails, the render retries on the processor and says so in the log.
* **Keying (the green-screen keyer), colour grading and compositing run on the processor** (FFmpeg filters). They cannot be moved to the graphics card without losing features, so while they work the Radeon can look nearly idle: that is expected, not a fault. Only the AI models, encoding and decoding use it.
On Windows the percentage is measured, the same counters as Task Manager (Compute / 3D / Video Encode engines), summed over this app and its video encoder, with the video memory it holds. NVIDIA (NVENC) and Intel (QSV) work the same way for encoding.

## Executing a blueprint: add or replace
Blueprint tab > **When I execute**. **Add to my timeline** (default) merges the pasted blueprint into your project and keeps everything already there: background removal, background layers, effects, cuts, assets and output settings. Clashing ids / asset names are renamed automatically, and a second background over an existing one is left out and reported. With cuts, select a clip first to apply the blueprint **only to that clip** (its times then start at the clip's start). **Replace the whole timeline** is the old behaviour (Undo brings the previous timeline back).

## Media bin (Media tab)
Everything you import stays in the project's Media bin, with thumbnails: your **source videos** (earlier ones remain, so you can switch back with **Use**), and every **background picture / video and LUT** (a copy inside the project's `assets/` folder). Undoing an edit never removes a file from the bin. Reuse any item by double-clicking it, pressing **Use** / **Apply**, or **dragging it onto the timeline**: backgrounds on the Background track, LUTs on the Effects track, source videos on the Video track (a background lands where you drop it). You can also drop files from your computer onto the bin. **Remove** deletes only the project's copy, never your original. The engine edits one source video at a time; multi-video editing is not supported yet.

## Master amount (one slider for the whole effect)
Colour Grading, Studio Look, Background Removal and Chroma Key have a **Master amount** at the top of their Properties, above the **Fine-tune** sliders. It cross-fades the whole effect with the original footage: **0 %** = the original, **100 %** = the effect exactly as set below, **up to 150 %** pushes colour effects further (a key goes 0–100 %: how much of the removal applies; for a transparent export it is the matte strength). It acts inside the effect's own time range, and the sliders below keep their own values, so you can set the look, then dial it back (or up) with one control. For a Studio Look it also scales how strongly the subject is integrated into the new background (colour cast, light wrap, depth). In a blueprint it is the `amount` parameter (not keyframable). The Blueprint tab has a **Strength** slider: every pasted effect that has a Master amount is applied at that strength, so a look that arrives too strong can be dialled back in one move. Drop/contact shadow and light wrap of a background are sliders on the Background Replacement itself.

## Choosing where an export is saved
Export dialog > **save to** > **Choose folder…** opens the folder picker. The choice is remembered for next time; **Project folder** goes back to the project's own `exports` folder. An existing file with the same name is never overwritten (the new one becomes "name (2)"), and the original video is never touched. When it finishes the dialog shows the full path (and, in the desktop app, **Show in folder**).

## Looking at a key properly: the Key view
With a Chroma Key or Background Removal on the timeline, set the preview to **Edited** or **Before/After** and use the **View** menu under the picture: **Matte (alpha)** (subject white, removed area black: holes and soft areas are obvious), **Edge band** (only the partly transparent edge is lit: halos and the thickness of the edge), or **On red / white / grey / checkerboard** (the subject on a solid colour: green spill and fringes show instantly). The zoom menu next to it (1:1, 200%, 400%) shows the normal picture beside the key view so you can inspect the edge pixel by pixel. The view shows the key only (grading and looks are ignored) and always uses the current frame.

## Garbage and keep masks: fixing a key by hand
Effects > Keying > **Garbage / Keep Mask** adds a shape you draw on the picture (set the preview to Edited). **Garbage** = the area is always removed (a light stand, the edge of the screen, anything that is not the person). **Keep** = the area is always kept (a face or shirt the key made see-through). Choose a rectangle, ellipse or polygon: drag the shape to move it, drag the white squares to resize, or for a polygon click the picture to add a corner (double-click a corner to delete it). Quick placements (left / right / top / bottom strip, centre), a soft edge (feather) and **invert** are in the Properties. A mask covers the whole clip by default; shorten it on the timeline for a time range. Masks correct the matte of the keys they overlap, before the background is placed, and they also work for transparent exports. In a blueprint: operation `matte_mask` (mode, shape, x, y, width, height, polygon, feather, invert; coordinates are fractions of the frame).

## Clean plate: the cleanest key you can get
Before or after the shot, record **3–5 seconds of the empty green screen** with the same camera, framing, zoom, focus and lights (no person, nothing touched). In Properties of a Background Removal set to the **screen** key, press **Import clean plate…** and choose that clip (or a still picture of it). The key then knows every wrinkle, fold, hot spot and shadow of the screen pixel by pixel instead of guessing them, so haze and holes next to hair and the face disappear, especially in dark or uneven parts of the screen. Only slow brightness drift (exposure, lights changing) between the plate and the shot is corrected automatically. If the plate does not fit (the camera moved, different framing), the panel says so and those frames use the normal estimate. `clean_plate_strength` (blueprint) mixes between the normal key (0) and the plate key (1). A plate video is averaged over its first ~2 seconds so sensor noise cancels.

## Key doctor: what is wrong with my key?
Select a Chroma Key or Background Removal and press **Key doctor > Check this key**. It samples ten frames across the clip plus a short run of consecutive frames, runs the editor's own screen keyer, and measures the pixels: an **uneven screen** (folds, shadows, dark corners: points you to the clean plate), a **screen that does not fill the frame** (measures how far the room reaches in and offers a ready garbage mask of that size), **see-through spots** inside the subject (raises Solidity), **green spill** on the edge (turns on / raises spill removal), an edge that is **too hard** (cut-out look) or **too wide** (motion blur: pulls it in), **noise** and **edge flicker** (more temporal smoothing), or **no screen at all** (suggests AI mode). Each finding shows the worst frame (**Show frame**: the playhead jumps there, then look at View > Matte) and a **fix** made of real settings; **Apply all fixes** applies every one in a single undo step. A garbage mask you already drew silences its finding. The score (100 = nothing to fix) is only a summary of the findings. It measures the footage, so judge the result with the Key view afterwards; it works best on green/blue-screen footage and cannot judge hair detail or colour taste.

## Camera match: make the subject look like the same camera shot it
A background picture and your footage are almost never shot by the same camera: one is softer, one grainier, one has milky blacks. That mismatch is what makes a keyed subject look pasted on, even with a perfect edge. Select a **Studio Look** (with a Background Replacement behind the subject) and press **Camera match > Match now**. The editor measures both sides on the picture and adjusts:
* **Sharpness:** the width of the subject's own edge against the strongest edges of the background (grain is removed first so it is not mistaken for sharpness). A subject that is crisper than the scene is softened (edge included); a softer one is sharpened a little.
* **Grain:** the noise on flat areas of each side. The cleaner side gets the difference: a clean picture behind grainy footage gets grain; clean footage in front of a grainy picture gets grain on the subject only.
* **Black level:** if the subject has real dark tones and the scene's blacks are lifted (or the other way round), the subject's black point moves to the scene's. A bright face with no blacks is left alone.
The panel lists what was measured and what is applied. The sliders **match softness**, **subject grain**, **background grain** and **match blacks** override the measured values, and **fringe** adds lens colour fringing (red and blue separate from green towards the frame edges) to the whole picture. The Studio Look's Master amount scales the camera match too. Limits: it compares one frame (the middle of the look's range), cannot tell texture from focus on a featureless subject, and does not know your camera; check the result at 100% in the Before/After loupe.

## Hair detail: insurance for difficult hair
Background Removal in **screen** mode has a **Hair detail** card. Turn it on and the AI matting model also looks at every frame; in a band around the subject's edge (set the width in px) it **restores what the key lost**: dark or green-tinted strands that turned see-through, dropouts in wispy hair. It can only *add*: strands the key already found are never softened, the AI's wider, softer halo over pure green screen never comes in, and a frame where the AI does not find the same person (for example nobody, or someone else) uses the plain key. The panel reports on how many checked frames the AI was used. Honest expectation: on clean footage the result is almost identical to the plain key (tested on a real green-screen clip: the matte differs on 0.03 levels of 255 on average, the curls stay crisp), because a good key is already better than the AI at fine hair. Use it when the Key doctor or the Matte view shows lost strands. Cost: it downloads a 15 MB model on first use and runs the network on every frame, so it is slower (a graphics card helps).

## Floor: reflection and contact shadow
If the person stands on a floor in the new scene, select the **Background Replacement** and open its **Floor** card:
* **Reflection:** the subject mirrored about the floor line, fading with distance (length and blur adjustable). A polished floor is sharp, a matte floor blurry.
* **Contact shadow:** a tight dark blob right under the feet, made from the lowest slice of the silhouette, so it follows every step. Its softness adjusts (hard light = tight, diffuse light = wide). It works together with the existing cast shadow (the softer, offset shadow).
Both are built from the subject itself, so they follow every movement (a jump lifts the contact shadow away and the reflection shows the gap). The **floor line** is found automatically (the lowest point of the subject's matte on the middle frame, followed through Subject scale / position) or set by hand; the panel reports where it found it. If the subject touches the bottom of the frame (cut at the waist) there is no floor in view and both effects are skipped, with a note. Limits: one floor line for the whole clip (a moving camera or a person who walks towards the camera changes where the floor is); the reflection is a flat mirror image, not a rendering of a curved or bumpy floor.

## Auto enhance: one master slider for a flat, hazy, dim or colour-cast clip
**Color** panel → **Auto enhance (one slider)** (or the **Auto enhance** card at the top of any Color Grading). The clip is measured on three frames: how bright the middle tones are, how white the whites (shirt, wall, sky) are, how milky the blacks are, the colour cast of things that should be neutral, the contrast and how colourful it is. From that the matching corrections are worked out (exposure, whites, blacks, highlights, dehaze, contrast, white balance, colour, clarity, texture) and the **master slider** applies them all together: 0 = off, 100 % = the measured correction, up to 150 % = pushed further. Double-click the slider to switch it off.
* **Natural:** gentle, keeps the mood of the shot. **Crisp:** bright, clean and a little cool, a clear-day look (bright clean whites, deep hair and beard, more detail). **Vivid:** more colour and punch.
* **Apply to: Both / Subject only / Background only.** With a removed background (key + background replacement) the grade can go on the subject only (the person or object you keyed; the background never changes, the cut-out is untouched) or on the background only (the subject never changes). Auto enhance then measures only that part, e.g. the subject's own brightness and colour cast. This choice holds for the whole Color Grading, your own sliders included. Without a background replacement there is nothing to tell apart: it grades the whole picture and the project check says so. With a removed background, Both measures the finished picture (not the green screen).
* The card shows what was measured and exactly what is applied at the current strength. Your own sliders in the same Color Grading add on top (e.g. a little more exposure).
* Checked on a real dim, warm overcast phone clip against an AI-enhanced version of the same shot: with Crisp at 100 % the face, the white shirt and the overall brightness came out within a few percent of it.
* Limits: it only changes brightness and colour. It cannot turn a white (clipped) sky blue, and it does not redraw detail the way AI photo apps do (they invent hair strands and reshape faces); for extra real detail add AI Resolution Enhancement.
* In a blueprint: `{"type": "color_grading", "parameters": {"auto_enhance": 1.0, "enhance_style": "crisp", "apply_to": "subject"}}` (`apply_to`: both / subject / background).

## AI face restoration (Remini-style detail)
**Effects → AI Face Restoration** (Face, over the whole clip). The AI (GFPGAN v1.4, Apache-2.0) rebuilds sharp eyes, eyebrows, hair strands, beard and skin detail of small, soft or noisy faces, like the "enhance" in phone apps. Each face is found, tracked and aligned, restored at 512 px, and pasted back with a soft face-shaped feather; **everything outside the faces stays exactly as it was** (the render blends with a mask that is zero away from the faces).
* **First use:** the model (349 MB, Apache-2.0) is downloaded once from GitHub and converted on your computer. Press **Download the model** in the effect's card (it shows progress); until then a live frame says what is missing instead of freezing.
* **Strength** (how much of the restored face is used), **Identity** (0 = the AI's face as it is, 1 = only its fine detail on top of your own colours and face shape), **Max faces** (the largest ones), **Smallest face** (faces narrower than this are skipped, default 96 px).
* **Honest limits:** the AI **re-draws** detail, so the result is plausible, not exact. At Identity 0 it can lighten skin and slightly change eyes; on a small, blurry face it makes up a different face, which is why tiny faces are skipped by default. For a sharp face it changes little. It does not rebuild what is not there at all. Best for faces about 100 to 500 px wide.
* **Video:** noise is fixed (no random shimmer), face alignment is smoothed over time, and a nearly still face reuses the previous result. Measured on a 2 s handheld-style clip: no added flicker (the restored face changes from frame to frame about 0.8 x as much as the original).
* **Speed (measured, 4-core processor, 1080 x 1440 frame with one face):** the first live frame about 3.5 s, then about 1.5 s per slider change (the network result is reused when only Strength / Identity change at the same playhead); a whole clip about 1.5 s per frame per face, so a 25 s, 25 fps clip takes roughly 16 minutes; the result is cached. A graphics card is much faster (not measured here).
* In a blueprint: `{"type": "face_restoration", "parameters": {"strength": 0.8, "identity": 0.5, "max_faces": 3, "min_face": 96}}`.
* Stack it before Skin Retouch and Smart HDR; a typical "phone portrait" result is Portrait Mode + AI Face Restoration (Identity 0.5) + Smart HDR + Auto enhance.

## Skin retouch: smoother skin, sharp features
**Effects → Skin Retouch** (Face, over the whole clip). Each face is found and tracked; only its **skin** is smoothed (pores, small blemishes, uneven texture). The eyes, eyebrows and lips are cut out from the face landmarks, and the beard, moustache and hair are left out because they are much darker than the person's own skin, so they all stay exactly as sharp as before. Nothing outside the faces changes and the face shape is never altered.
* **Amount** (how much), **Texture** (how much of the real skin texture is kept: more = more natural, less = smoother), **Size** (small = pores and fine texture only, large = also larger blemishes and blotches).
* Edge-preserving (bilateral) smoothing, with part of the original texture mixed back in, so skin looks like skin, not plastic. Start around Amount 50 %, Texture 35 %.
* The face detector downloads once (230 KB); faces that are very small, strongly turned or covered may not be found in every frame.
* In a blueprint: `{"type": "skin_retouch", "parameters": {"amount": 0.5, "texture": 0.35, "size": 0.5}}`.

## Smart HDR: fix backlit and high-contrast shots
**Color → Smart HDR** (or Effects → Smart HDR, over the whole clip). A normal exposure change cannot fix a face in shadow next to a bright sky: one gets right, the other wrong. Smart HDR works area by area: dark regions (a face in shadow, dark clothes) are brightened, bright regions (sky, a sunlit wall) are held back, mid-grey regions are left alone, and the detail inside each region and every colour are kept.
* **Amount** (overall), **Shadows** (how much the darks come up), **Highlights** (how much the brights are held back), **Size** (small = more local, the stronger "HDR" look; large = more natural).
* No halos: the brightness of each region is found with an edge-aware (bilateral) filter, so a dark person in front of a bright window gets no glowing outline.
* It evens out the light, so it lowers the overall punch a little: pair it with a gentle Auto enhance (Natural) or some Clarity rather than a strong one.
* In a blueprint: `{"type": "smart_hdr", "parameters": {"amount": 0.7, "shadows": 0.6, "highlights": 0.5, "size": 0.5}}`.
* Auto enhance now measures the picture exactly as it reaches it (after Smart HDR, Portrait mode and any other effect before it), not the untouched source.

## Portrait mode: sharp person, blurred real background
**Effects → Portrait Mode (background blur)** (added over the whole clip). The person is found by the same AI matting model the background removal uses (no green screen), stays sharp, and the real background behind them is blurred like a portrait lens.
* **Blur:** how soft the background gets. **Falloff:** background close to the person is a little less blurred than background far away, like a real lens (0 = the same everywhere). **Edge:** softness of the sharp / blurred transition.
* No halo: the background is blurred with the person masked out (normalised blur), so their face and hair colours never glow into the blur. Between fine hair strands the person's estimated colour is used, so the blur shows through the gaps.
* Combine with **Auto enhance** and the **Cinematic look** for the full "phone portrait / film" result. Speed (1080p frame): the first live frame needs the AI person detection (about 2 s); after that each slider change about 1 s.
* Limits: it knows "person" and "not person", not real distance, so things at the same distance as you (the chair you lean on) are blurred too; the AI finds people best (other subjects may not be found).
* In a blueprint: `{"type": "portrait_blur", "parameters": {"blur": 0.6, "falloff": 0.5, "edge": 0.3}}`.

## Cinematic look: a real film grade in one control
**Color** panel → **Cinematic look** (an auto-enhanced base + the film look over the whole video), or the orange **Cinematic look** card in any Color Grading. What a colourist does for a film look, in one slider (0 – 150 %):
* filmic tones: slightly lifted, soft blacks and highlights that roll off instead of glaring, a gentle S-curve;
* colour **by hue**, not only by brightness: skin (reds / yellows) stays warm, greens turn toward teal, magentas and blues calm down; teal shadows and warm highlights with the brightness preserved;
* restrained overall colour, a vignette, and fine film grain in the brightness only (no colour speckles).
Styles: **Teal & orange** (the blockbuster look), **Moody** (darker, muted, cold shadows), **Golden** (warm golden-hour film), **Bleach** (bleach bypass: low colour, high contrast). **Cinema bars** (2.39 / 2 / 1.85 : 1) add black bars top and bottom; on a tall (portrait) video they would hide most of the picture, so they are skipped there and the project check says so. In a blueprint: `"cinematic": 1.0, "cinematic_style": "teal_orange", "letterbox": "2.39"` on a color_grading. The colour-by-hue step needs FFmpeg 5.1 or newer (any current build); with an older one the rest of the look still works.

## Colour lights: cyan from the left, magenta from the right (portrait / stage look)
On a **Studio Look** (with a Background Replacement behind the subject) open **Colour lights** and press **Cyan / magenta portrait**. Two coloured lights fall on the **subject only**, each from a side, and fade across it; where they meet in the middle the colours mix. A glow also hugs the outline on the side facing each light (jaw, cheek, hair, fins).

- **Light A / Light B:** pick each colour and which side it comes from (left, right, top, bottom, or off to use a single light).
- **How far it reaches:** 0 = only the near edge, 1 = across the whole subject. **Edge glow:** the rim along the outline facing the light.
- **Where the lights sit** (three choices):
  - **Fixed (subject, middle frame):** a stage light. The zone is placed from where the subject is on the middle frame, then stays put in the picture. When the subject moves out of it, the colour leaves it; when a face or hand moves back in, it is lit again. If the subject starts far from the middle position, the lit area may not be where you expect, so use one of the next two.
  - **Follow the subject:** the zone travels with the subject. Its position is measured about every quarter second (up to 24 moments) and smoothed, so the light stays on it wherever it goes. Very fast, sudden moves can lag a little between measurements. The first preview of a long clip takes a moment to measure.
  - **My own spot:** drag the dashed rectangle on the preview to move it and its white handles to resize it. Only inside it is lit (with a soft edge you can adjust), and the subject is lit only where it is inside; move out and it is back to normal, move back in and the colour returns.
- **Light strength** and the Studio Look's **Master amount** scale everything.
- The light is added to the subject layer before it is placed over the background, so it can never appear on the background. The subject's bounds are measured on the middle frame, so "from the left" really starts at the subject's left edge.

In a blueprint: `colour_light`, `colour_light_color_a`, `colour_light_side_a`, `colour_light_color_b`, `colour_light_side_b`, `colour_light_reach`, `colour_light_edge`, `colour_light_place` (fixed / follow / zone), `colour_light_zone_x0` / `y0` / `x1` / `y1` / `soft`. Limits: the light is placed by position and outline, not by the real 3D shape of the face (no real relighting of the nose or cheeks); in Fixed mode the bounds are measured once (use Follow for a subject that walks across the frame); very bright skin has little room to take a colour (the light adds, it does not recolour), so for strong colours darken the subject a little in the grade first.

## Light pattern: ripples, solid or particles, in your colours
A scene lights the person too. On a **Studio Look** (with a Background Replacement behind the subject) open **Light pattern** and press **Underwater ripples**; then shape it:

- **Style:** *Ripples* (soft moving water-light web), *Solid* (hard-edged light shapes) or *Particles* (floating specks of light).
- **Colours (two):** **Light colour** (default white = brightness only) tints the lit parts; an optional **Second colour** tints the shadowed parts between them (e.g. orange light over a blue shadow). Remove it to go back to one colour.
- **Spread (softness):** 0 = crisp edges, 1 = a wide soft glow. **Bend (warp):** 0 = straight regular lines, 2 = strongly wobbling, liquid shapes. **Pattern size**, **Speed** (0 = still) and **Fade towards the bottom** as before.
- **Apply to:** **Subject only (recommended)**, **Background only** or **Both**. With *Subject only* the light is applied to the subject layer before it is composited, so it can never show on the background, even with a large spread; the subject's matte is never changed. (With *Background only* the subject is untouched, the same way.)

It redistributes light, so average brightness stays about the same. **Light pattern asset id** uses your own picture or looping video (bright shapes on a dark ground) instead. Master amount scales it. Recommended strength 0.5–0.7. In a blueprint: `light_pattern`, `light_pattern_style`, `light_pattern_color`, `light_pattern_color2`, `light_pattern_spread`, `light_pattern_warp`, `light_pattern_on`, `light_pattern_scale`, `light_pattern_speed`, `light_pattern_falloff`, `light_pattern_asset_id`. Limits: it is a flat projection (it does not follow the 3D shape of the body); soft edge pixels of the subject blend with the background by their own transparency, so a few edge pixels can pick up background light; generated patterns are regular, a real caustics loop as your own pattern looks more natural.

## Photo mode

Open a photo (JPG, PNG, WebP or BMP) the same way you open a video: drop it on the start screen, or use **Import → Video…** and choose a picture. The editor switches to a simpler screen: no timeline and no play button, just the picture, the effects list under it and every effect you already know (colour, Auto enhance, Cinematic look, Portrait mode, Skin retouch, AI face restoration, Smart HDR, …) with the live preview.

- **Save photo** (top right): choose JPG, PNG or WebP, the quality and the folder. The picture is saved at its full size. The original photo is never changed and an existing file is never overwritten: a second save becomes “name (2)”.
- **Apply this look to a whole folder**: in the Save dialog choose a folder of photos and press **Edit all**. Every photo gets the effects you have set up (each keeps its own size), saved into a new “edited” folder next to them (or the folder chosen above). A photo that cannot be read is listed and skipped; the others carry on. You can stop the batch at any time.
- Phone photos keep their rotation. Transparent PNGs are placed on white. Pictures larger than 8192 px on the long side are scaled down.
- Effects that depend on motion (clean plate, timeline cuts) do not apply to a single picture.

## Ready-made looks

Open **Ready-made** in the left bar for finished, tested sets of effects that you add with one click (**Add to project**). Nothing you already have is removed, and Undo takes them away again. After adding, every setting can still be changed. The same looks also appear at the top of the Effects, Backgrounds and Color panels.

- **Start to finish**: *Club night* (green-screen key with fine hair strands, club backdrop, face restoration and retouch, cinematic grade, finishing look) and the same with a 2x AI upscale (slow without a strong graphics card; it also sets the output size to twice the video).
- **Green screen**: clean cut-out; cut-out on a dark stage floor with shadow and reflection.
- **Face**: natural portrait; soft skin glow.
- **Colour**: cinematic teal and orange, moody night, golden hour, clean and punchy.
- **Quality**: clean and sharp; 2x AI upscale.

The background of the club looks is a plain dark colour: choose your own picture or video in **Backgrounds** afterwards.

## Many coloured light boxes

In **Studio Look → Colour lights** you can draw up to **six boxes** on the preview. Each box has its own colour and side (the light comes from the left, right, top or bottom of that box), its own reach, strength and soft edge, and it lights the **subject only, and only inside itself**. The background is never touched.

1. Turn Colour lights on, then press **Add a box**. A box appears on the preview.
2. Drag it to move it. Click a box to select it, then drag its white handles to resize it.
3. Use the **Box 1, Box 2…** tabs in Properties to change a box's colour, side and strength, or delete it.
4. Press **Boxes only** to switch Light A and B off so that only your boxes light the subject. Otherwise the boxes are added to Light A and B.

The boxes stay in the same place in the picture for the whole video (like stage lights). The Ready-made tab has a three-box example: *Club lights: three coloured boxes*.

## When a render is waiting

The editor renders one video at a time (previews, exports and the AI analysis share one slot). If you press **Apply to full video** while an earlier render is still running, the bar at the bottom says **Waiting for the earlier preview/export render** and shows what that render is doing and how far it is. Press **Stop the earlier render** to cancel it so yours starts now, or just wait. **Cancel** on a waiting render takes effect at once.

### Boxes that follow the subject

Tick **Follow the subject** on a box and it travels with the subject instead of staying put. Place the box where you want it when the subject is in the **middle of the clip**; it then keeps that position relative to the subject (the app measures where the subject is about every quarter second). Boxes without the tick stay fixed, and you can mix both on the same look.
