# Operation reference

_Generated from `backend/evp/registry.py` by `backend/scripts/gen_docs.py` — do not edit by hand._

## `chroma_key` — Chroma Key

Key out a solid colour (green/blue screen) and composite on a background colour or the next replacement.

Timeline track: **effects** · provides an alpha layer for `background_replacement`

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `key_color` | color | `#00ff00` |  | Colour to remove: '#rrggbb', 'green' or 'blue'. |
| `similarity` | number | `0.12` | 0.01 … 1.0 | How close a pixel must be to the key colour to become transparent. |
| `blend` | number | `0.08` | 0.0 … 1.0 | Soft transition between keyed and kept pixels. |
| `spill_suppression` | boolean | `True` |  | Remove green/blue colour cast from the subject's edges. |
| `spill_strength` | number | `0.5` | 0.0 … 1.0 | Despill mix strength. |
| `edge_erode` | integer | `0` | 0 … 10 px | Shrink the matte to remove fringe. |
| `edge_feather` | number | `0.0` | 0.0 … 20.0 px | Gaussian softening of the matte edge. |
| `background_color` | color | `#000000` |  | Fill used when no background_replacement consumes this key. |

## `background_removal` — Background Removal

Remove the background. 'ai' works on ordinary footage (no green screen) using the Robust Video Matting model; 'chroma' keys a green/blue screen.

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
| `similarity` | number | `0.12` | 0.01 … 1.0 | How close a pixel must be to the key colour to become transparent. |
| `blend` | number | `0.08` | 0.0 … 1.0 | Soft transition between keyed and kept pixels. |
| `spill_suppression` | boolean | `True` |  | Remove green/blue colour cast from the subject's edges. |
| `spill_strength` | number | `0.5` | 0.0 … 1.0 | Despill mix strength. |
| `background_color` | color | `#000000` |  | Fill used when no background_replacement consumes this removal. |

## `background_replacement` — Background Replacement

Place the keyed subject over an image, video or solid colour. Needs an alpha source (chroma_key/background_removal).

Timeline track: **background**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `asset_id` | asset |  |  | Background asset. Omit to use `color`. |
| `color` | color | `#000000` |  | Solid background if no asset_id. |
| `fit` | enum | `cover` | cover, contain, stretch |  |
| `scale` | number | `1.0` | 0.1 … 4.0 | Additional zoom on the background. |
| `offset_x` | number | `0.0` | -1.0 … 1.0 | Horizontal shift as fraction of frame width. |
| `offset_y` | number | `0.0` | -1.0 … 1.0 | Vertical shift as fraction of frame height. |
| `blur` | number | `0.0` | 0.0 … 50.0 px | Gaussian background blur (fake depth of field). |
| `brightness` | number | `0.0` | -1.0 … 1.0 | Background brightness to match the subject. |
| `saturation` | number | `1.0` | 0.0 … 3.0 | Background saturation to match the subject. |

## `color_grading` — Color Grading

Exposure, contrast, tone, white balance, saturation, LGG, RGB curves, LUT, presets.

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `preset` | enum | `none` | none, cinematic_teal_orange, warm_film, cool_blue, bleach_bypass, faded_matte, high_contrast_bw | Starting look; individual controls are applied on top. |
| `exposure` | number | `0.0` | -3.0 … 3.0 EV | Exposure compensation in stops. |
| `contrast` | number | `1.0` | 0.0 … 3.0 | 1.0 = unchanged. |
| `brightness` | number | `0.0` | -1.0 … 1.0 |  |
| `highlights` | number | `0.0` | -1.0 … 1.0 | Lift (+) or pull down (-) highlights. |
| `shadows` | number | `0.0` | -1.0 … 1.0 | Lift (+) or crush (-) shadows. |
| `whites` | number | `0.0` | -1.0 … 1.0 |  |
| `blacks` | number | `0.0` | -1.0 … 1.0 |  |
| `temperature` | number | `0.0` | -100.0 … 100.0 | -100 cool (blue) ... +100 warm (orange). |
| `tint` | number | `0.0` | -100.0 … 100.0 | -100 green ... +100 magenta. |
| `saturation` | number | `1.0` | 0.0 … 3.0 | 1.0 = unchanged. |
| `vibrance` | number | `0.0` | -2.0 … 2.0 |  |
| `gamma` | number | `1.0` | 0.1 … 5.0 |  |
| `lift` | points |  |  | [r,g,b] shadow colour offset, each -1..1 (Lift/Gamma/Gain). |
| `gamma_rgb` | points |  |  | [r,g,b] midtone colour offset, each -1..1. |
| `gain` | points |  |  | [r,g,b] highlight colour offset, each -1..1. |
| `curve_master` | points |  |  | Master curve control points [[x,y],...] in 0..1. |
| `curve_red` | points |  |  | Red curve control points [[x,y],...] in 0..1. |
| `curve_green` | points |  |  | Green curve control points [[x,y],...] in 0..1. |
| `curve_blue` | points |  |  | Blue curve control points [[x,y],...] in 0..1. |
| `lut_asset_id` | asset |  |  | A .cube/.3dl LUT asset to apply. |
| `lut_intensity` | number | `1.0` | 0.0 … 1.0 | Blend between graded-without-LUT and LUT. |

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

Timeline track: **overlay**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `text` | string (required) |  |  | Text to draw (max 500 chars). |
| `font_size` | integer | `48` | 8 … 400 |  |
| `color` | color | `#ffffff` |  |  |
| `x` | number | `0.5` | 0.0 … 1.0 | Horizontal anchor (fraction, centred). |
| `y` | number | `0.85` | 0.0 … 1.0 | Vertical anchor (fraction, centred). |
| `box` | boolean | `True` |  | Draw a translucent box behind the text. |
| `box_opacity` | number | `0.5` | 0.0 … 1.0 |  |

## `audio_gain` — Audio Gain

Change volume over a range.

Timeline track: **audio**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `gain_db` | number | `0.0` | -60.0 … 24.0 dB |  |
| `fade_in` | number | `0.0` | 0.0 … 30.0 s |  |
| `fade_out` | number | `0.0` | 0.0 … 30.0 s |  |

## `face_tone_correction` — Face & Skin Tone Correction

Face & Skin Tone Correction

> **Not available in this build.** Requires a trained model (and ideally a GPU) that is not bundled. No fake fallback is provided: use a supported operation or add a model plugin.

## `face_relighting` — Face Relighting

Face Relighting

> **Not available in this build.** Requires a trained model (and ideally a GPU) that is not bundled. No fake fallback is provided: use a supported operation or add a model plugin.

## `ai_upscale` — AI Resolution Enhancement

AI Resolution Enhancement

> **Not available in this build.** Requires a trained model (and ideally a GPU) that is not bundled. No fake fallback is provided: use a supported operation or add a model plugin.

## `hair_edge_refinement` — Hair & Edge Refinement (matting)

Hair & Edge Refinement (matting)

> **Not available in this build.** Use background_removal with mode 'ai': its Robust Video Matting model already produces hair-level alpha. A separate refinement pass for chroma keys is not implemented.

