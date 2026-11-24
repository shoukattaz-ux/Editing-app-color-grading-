# Operation reference

_Generated from `backend/evp/registry.py` by `backend/scripts/gen_docs.py` — do not edit by hand._

## `chroma_key` — Chroma Key

Key out a solid colour (green/blue screen) and composite on a background colour or the next replacement.

◆ = can be keyframed (animated over time).

Timeline track: **effects** · provides an alpha layer for `background_replacement`

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `key_color` | color | `#00ff00` |  | Colour to remove: '#rrggbb', 'green' or 'blue'. |
| `similarity` ◆ | number | `0.12` | 0.01 … 1.0 | How close a pixel must be to the key colour to become transparent. |
| `blend` ◆ | number | `0.08` | 0.0 … 1.0 | Soft transition between keyed and kept pixels. |
| `spill_suppression` | boolean | `True` |  | Remove green/blue colour cast from the subject's edges. |
| `spill_strength` ◆ | number | `0.5` | 0.0 … 1.0 | Despill mix strength. |
| `edge_erode` | integer | `0` | 0 … 10 px | Shrink the matte to remove fringe. |
| `edge_feather` | number | `0.0` | 0.0 … 20.0 px | Gaussian softening of the matte edge. |
| `background_color` | color | `#000000` |  | Fill used when no background_replacement consumes this key. |

## `background_removal` — Background Removal

Remove the background. 'ai' works on ordinary footage (no green screen) using the Robust Video Matting model; 'chroma' keys a green/blue screen.

◆ = can be keyframed (animated over time).

Timeline track: **effects** · provides an alpha layer for `background_replacement`

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `mode` | enum | `chroma` | chroma, ai | 'ai' = person matting without green screen (downloads a 15 MB model on first use); 'chroma' = green/blue screen. |
| `model` | enum | `rvm` | rvm | [ai] Matting model. rvm = Robust Video Matting (GPL-3.0), hair-level detail, temporally stable. |
| `detail` | enum | `fast` | fast, balanced, high | [ai] Network resolution. fast is the most robust on busy/patterned backgrounds; balanced/high resolve finer hair on plain backgrounds but are slower and can pick up background texture. |
| `matte_threshold` | number | `0.03` | 0.0 … 0.5 | [ai] Alpha below this becomes fully transparent (removes faint background haze). |
| `despeckle` | boolean | `True` |  | [ai] Remove background blobs that are not connected to the person. |
| `edge_color_fix` | boolean | `True` |  | [ai] Replace old-background colour in soft hair edges with the model's estimated foreground colour (removes halos). |
| `quality` | enum | `high` | draft, standard, high | [chroma] Higher = more edge processing. |
| `edge_refinement` | boolean | `True` |  | [chroma] Erode + feather the matte edge. |
| `key_color` | color | `#00ff00` |  | Colour to remove: '#rrggbb', 'green' or 'blue'. |
| `similarity` ◆ | number | `0.12` | 0.01 … 1.0 | How close a pixel must be to the key colour to become transparent. |
| `blend` ◆ | number | `0.08` | 0.0 … 1.0 | Soft transition between keyed and kept pixels. |
| `spill_suppression` | boolean | `True` |  | Remove green/blue colour cast from the subject's edges. |
| `spill_strength` ◆ | number | `0.5` | 0.0 … 1.0 | Despill mix strength. |
| `background_color` | color | `#000000` |  | Fill used when no background_replacement consumes this removal. |

## `background_replacement` — Background Replacement

Place the keyed subject over an image, video or solid colour. Needs an alpha source (chroma_key/background_removal).

◆ = can be keyframed (animated over time).

Timeline track: **background**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `asset_id` | asset |  |  | Background asset. Omit to use `color`. |
| `color` | color | `#000000` |  | Solid background if no asset_id. |
| `fit` | enum | `cover` | cover, contain, stretch |  |
| `scale` ◆ | number | `1.0` | 0.1 … 4.0 | Additional zoom on the background. |
| `offset_x` ◆ | number | `0.0` | -1.0 … 1.0 | Horizontal shift as fraction of frame width. |
| `offset_y` ◆ | number | `0.0` | -1.0 … 1.0 | Vertical shift as fraction of frame height. |
| `blur` | number | `0.0` | 0.0 … 50.0 px | Gaussian background blur (fake depth of field). |
| `brightness` ◆ | number | `0.0` | -1.0 … 1.0 | Background brightness to match the subject. |
| `saturation` ◆ | number | `1.0` | 0.0 … 3.0 | Background saturation to match the subject. |

## `color_grading` — Color Grading

Exposure, contrast, tone, white balance, saturation, LGG, RGB curves, LUT, presets.

◆ = can be keyframed (animated over time).

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `preset` | enum | `none` | none, cinematic_teal_orange, warm_film, cool_blue, bleach_bypass, faded_matte, high_contrast_bw | Starting look; individual controls are applied on top. |
| `exposure` ◆ | number | `0.0` | -3.0 … 3.0 EV | Exposure compensation in stops. |
| `contrast` ◆ | number | `1.0` | 0.0 … 3.0 | 1.0 = unchanged. |
| `brightness` ◆ | number | `0.0` | -1.0 … 1.0 |  |
| `highlights` ◆ | number | `0.0` | -1.0 … 1.0 | Lift (+) or pull down (-) highlights. |
| `shadows` ◆ | number | `0.0` | -1.0 … 1.0 | Lift (+) or crush (-) shadows. |
| `whites` ◆ | number | `0.0` | -1.0 … 1.0 |  |
| `blacks` ◆ | number | `0.0` | -1.0 … 1.0 |  |
| `temperature` ◆ | number | `0.0` | -100.0 … 100.0 | -100 cool (blue) ... +100 warm (orange). |
| `tint` ◆ | number | `0.0` | -100.0 … 100.0 | -100 green ... +100 magenta. |
| `saturation` ◆ | number | `1.0` | 0.0 … 3.0 | 1.0 = unchanged. |
| `vibrance` ◆ | number | `0.0` | -2.0 … 2.0 |  |
| `gamma` ◆ | number | `1.0` | 0.1 … 5.0 |  |
| `lift` | points |  |  | [r,g,b] each -1..1. Lift: out = in + lift*(1-in): moves the black point per channel, white stays. Equal values = brightness of the shadows; unequal = colour cast in the shadows (colour wheel). |
| `gamma_rgb` | points |  |  | [r,g,b] each -1..1. Gamma: out = in ** 2**(-gamma): bends the midtones per channel, black and white stay (+ brightens). Colour wheel for the midtones. |
| `gain` | points |  |  | [r,g,b] each -1..1. Gain: out = in * (1+gain): scales per channel, black stays. Colour wheel for the highlights. |
| `curve_master` | points |  |  | Master curve control points [[x,y],...] in 0..1. |
| `curve_red` | points |  |  | Red curve control points [[x,y],...] in 0..1. |
| `curve_green` | points |  |  | Green curve control points [[x,y],...] in 0..1. |
| `curve_blue` | points |  |  | Blue curve control points [[x,y],...] in 0..1. |
| `lut_asset_id` | asset |  |  | A .cube/.3dl LUT asset to apply. |
| `lut_intensity` | number | `1.0` | 0.0 … 1.0 | Blend between graded-without-LUT and LUT. |

## `studio_look` — Studio Look

One-step 'real studio' finish. With a background_replacement it first integrates the subject into the new scene (the background's colour falls on the subject, its light wraps around the edges, slight depth of field), then grades the whole composite (S-curve, split-tone, highlight glow, vignette, local contrast, film grain). Without a replacement only the global grade runs. Pick a preset; any control you set overrides it.

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `preset` | enum | `studio_warm` | none, studio_warm, studio_cool, cinematic, clean_bright | Starting look. Controls you set override it; 'none' starts from nothing. |
| `ambient_match` | number |  | 0.0 … 1.0 | [needs background_replacement] Subject picks up the background's colour cast, following where the light is in the scene. |
| `light_wrap` | number |  | 0.0 … 1.0 | [needs background_replacement] Background light spilling over the subject's edges - removes the 'cut-out' look. |
| `wrap_width` | number |  | 1.0 … 60.0 px at 1080p | [needs background_replacement] How far the light wrap reaches into the subject. |
| `depth` | number |  | 0.0 … 1.0 | [needs background_replacement] Extra background softness (depth of field) so the subject stands out. |
| `contrast` | number |  | 0.0 … 1.0 | S-curve strength. |
| `shadow_tint` | color |  |  | Colour pushed into the shadows (only its hue matters). |
| `highlight_tint` | color |  |  | Colour pushed into the highlights (only its hue matters). |
| `tint_amount` | number |  | 0.0 … 1.0 | Strength of the split-tone. |
| `glow` | number |  | 0.0 … 1.0 | Soft bloom around bright areas (lamps, screens). |
| `glow_threshold` | number |  | 0.3 … 0.95 | Brightness above which the glow starts. |
| `glow_radius` | number |  | 1.0 … 60.0 px at 1080p | Glow spread. |
| `vignette` | number |  | 0.0 … 1.0 | Darken the frame edges. |
| `clarity` | number |  | 0.0 … 1.0 | Local contrast (mid-frequency sharpening). |
| `grain` | number |  | 0.0 … 1.0 | Fine film grain; also hides banding and the 'digital' look of a composite. |
| `saturation` | number |  | 0.5 … 1.5 | Overall saturation. |

## `video_enhancement` — Video Enhancement

Noise reduction, sharpening and basic stabilisation (classical algorithms; limited by source quality).

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `denoise` | number | `0.0` | 0.0 … 1.0 | hqdn3d strength. |
| `sharpen` | number | `0.0` | 0.0 … 2.0 | Unsharp-mask amount. |
| `stabilize` | boolean | `False` |  | Single-pass deshake. Modest results; fully-featured stabilisation is not claimed. |
| `stabilize_strength` | integer | `16` | 1 … 64 | Max shift in px searched by deshake. |

## `text_overlay` — Text Overlay

Draw static text.

◆ = can be keyframed (animated over time).

Timeline track: **overlay**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `text` | string (required) |  |  | Text to draw (max 500 chars). |
| `font_size` ◆ | integer | `48` | 8 … 400 |  |
| `color` | color | `#ffffff` |  |  |
| `x` ◆ | number | `0.5` | 0.0 … 1.0 | Horizontal anchor (fraction, centred). |
| `y` ◆ | number | `0.85` | 0.0 … 1.0 | Vertical anchor (fraction, centred). |
| `opacity` ◆ | number | `1.0` | 0.0 … 1.0 | Text opacity (keyframe it for fade in/out titles). |
| `box` | boolean | `True` |  | Draw a translucent box behind the text. |
| `box_opacity` ◆ | number | `0.5` | 0.0 … 1.0 |  |

## `audio_gain` — Audio Gain

Change volume over a range.

◆ = can be keyframed (animated over time).

Timeline track: **audio**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `gain_db` ◆ | number | `0.0` | -60.0 … 24.0 dB |  |
| `fade_in` | number | `0.0` | 0.0 … 30.0 s |  |
| `fade_out` | number | `0.0` | 0.0 … 30.0 s |  |

## `face_tone_correction` — Face & Skin Tone Correction

Detects and tracks faces (YuNet), balances face exposure automatically and adjusts skin colour only on the person's own skin tone. Never smooths skin or changes facial features.

◆ = can be keyframed (animated over time).

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `strength` ◆ | number | `1.0` | 0.0 … 1.0 | Overall amount of the correction. |
| `auto_exposure` | boolean | `True` |  | Bring the face toward target_brightness, smoothly over time. |
| `target_brightness` ◆ | number | `0.55` | 0.3 … 0.8 | Desired median skin brightness (0..1) for auto exposure. |
| `auto_strength` ◆ | number | `0.7` | 0.0 … 1.0 | How far auto exposure moves toward the target. |
| `max_auto_ev` ◆ | number | `1.0` | 0.0 … 2.0 EV | Largest automatic exposure change. |
| `exposure` ◆ | number | `0.0` | -2.0 … 2.0 EV | Manual face exposure (added to the automatic one). |
| `warmth` ◆ | number | `0.0` | -100.0 … 100.0 | Skin warmth: + warmer, - cooler. |
| `tint` ◆ | number | `0.0` | -100.0 … 100.0 | Skin tint: + magenta, - green. |
| `saturation` ◆ | number | `1.0` | 0.0 … 2.0 | Skin saturation (1 = unchanged). |
| `skin_only` | boolean | `True` |  | Colour changes only on skin (not beard, eyes, hair). |
| `feather` | number | `0.5` | 0.0 … 1.0 | Softness of the face region edge (relative to face size). |
| `detection_confidence` | number | `0.6` | 0.3 … 0.95 | Face detector threshold. Lower finds small/turned faces but may misfire. |

## `face_relighting` — Face Relighting

2D virtual lighting on tracked faces: key light from a chosen side, fill that lifts shadows, optional shadow deepening. Image-space approximation (no 3D face model), so it suits gentle corrections.

◆ = can be keyframed (animated over time).

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `key_angle` ◆ | number | `-45.0` | -90.0 … 90.0 deg | Key light side: -90 frame-left, 0 front, +90 frame-right. |
| `key_elevation` ◆ | number | `20.0` | 0.0 … 60.0 deg | Key light height (raises the bright side toward the forehead). |
| `key_intensity` ◆ | number | `0.5` | 0.0 … 1.0 | Key light strength (up to +1.5 EV on the lit side). |
| `fill` ◆ | number | `0.3` | 0.0 … 1.0 | Lifts dark areas of the face (up to +1 EV). |
| `shadow_depth` ◆ | number | `0.0` | 0.0 … 1.0 | Darkens the side away from the key for more contrast. |
| `feather` | number | `0.5` | 0.0 … 1.0 | Softness of the face region edge (relative to face size). |
| `detection_confidence` | number | `0.6` | 0.3 … 0.95 | Face detector threshold. Lower finds small/turned faces but may misfire. |

## `ai_upscale` — AI Resolution Enhancement

Real-ESRGAN super-resolution (x4 network, BSD-3) used instead of the normal scaler when the output is larger than the source. Runs on CPU or GPU (ONNX Runtime). It reconstructs plausible detail; it cannot recover detail that was never recorded, and on CPU it is slow (~0.5-3 s per frame).

◆ = can be keyframed (animated over time).

Timeline track: **video**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `model` | enum | `general` | general, quality, quality_x2, anime | general: fast real-world model (CPU friendly). quality: Real-ESRGAN x4plus, finest detail, ~12x slower (GPU recommended). quality_x2: native 2x x2plus model, best for 1.5-2x jobs. anime: cartoons/anime/flat graphics. |
| `grain` ◆ | number | `0.0` | 0.0 … 1.0 | Re-add the source's own fine grain/noise so the result looks camera-made rather than painted. |
| `quality` | enum | `fast` | fast, best | fast: when the output is at most 2x the source, the network runs on a half-size frame (about 4x faster). best: always full size. |
| `denoise` | number | `0.5` | 0.0 … 1.0 | [general model] 0 keeps all texture (and noise); 1 removes noise strongly (smoother). |
| `strength` ◆ | number | `1.0` | 0.0 … 1.0 | Blend with a classical Lanczos upscale; lower looks more natural. |
| `temporal_stability` ◆ | number | `0.6` | 0.0 … 1.0 | Motion-compensated smoothing of the AI detail between frames (removes shimmer). 0 = per-frame. |
| `color_fidelity` | boolean | `True` |  | Keep the original's colour/exposure/contrast exactly; the AI only adds fine detail. |
| `face_detail` ◆ | number | `0.8` | 0.0 … 1.0 | Amount of AI detail on faces (1 = same as elsewhere). Lower avoids plastic skin and redrawn features. |

## `hair_edge_refinement` — Hair & Edge Refinement (matting)

Hair & Edge Refinement (matting)

> **Not available in this build.** Use background_removal with mode 'ai': its Robust Video Matting model already produces hair-level alpha. A separate refinement pass for chroma keys is not implemented.

