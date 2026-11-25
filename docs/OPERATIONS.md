# Operation reference

_Generated from `backend/evp/registry.py` by `backend/scripts/gen_docs.py` — do not edit by hand._

## `chroma_key` — Chroma Key

Key out a solid colour (green/blue screen) and composite on a background colour or the next replacement.

◆ = can be keyframed (animated over time).

Timeline track: **effects** · provides an alpha layer for `background_replacement`

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `amount` | number | `1.0` | 0.0 … 1.0 x | Master amount: how much of the removal applies. 1.0 = fully removed, 0 = the original footage (the removal is mixed out), in between = a cross-fade between the two. For a transparent export it is the matte strength. |
| `key_color` | color | `#00ff00` |  | Colour to remove: '#rrggbb', 'green' or 'blue'. |
| `similarity` ◆ | number | `0.12` | 0.01 … 1.0 | How close a pixel must be to the key colour to become transparent. |
| `blend` ◆ | number | `0.08` | 0.0 … 1.0 | Soft transition between keyed and kept pixels. |
| `spill_suppression` | boolean | `True` |  | Remove green/blue colour cast from the subject's edges. |
| `spill_strength` ◆ | number | `0.5` | 0.0 … 1.0 | Despill mix strength. |
| `edge_erode` | integer | `0` | 0 … 10 px | Shrink the matte to remove fringe. |
| `edge_feather` | number | `0.0` | 0.0 … 20.0 px | Gaussian softening of the matte edge. |
| `background_color` | color | `#000000` |  | Fill used when no background_replacement consumes this key. |

## `matte_mask` — Garbage / Keep Mask

Correct a key by hand with a shape. Garbage = this area is always removed (lights, stands, the edge of the screen). Keep = this area is always kept (a face or shirt the key made see-through). Applies to the keying operation(s) it overlaps (or the one in depends_on); coordinates are fractions of the frame (0-1, x/y from the top-left), so they work at any resolution.

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `mode` | enum | `garbage` | garbage, keep | garbage = always removed; keep = always kept. |
| `shape` | enum | `rect` | rect, ellipse, polygon | rect and ellipse use x, y, width, height; polygon uses `polygon`. |
| `x` | number | `0.0` | 0.0 … 1.0 | [rect, ellipse] left edge, fraction of the frame width. |
| `y` | number | `0.0` | 0.0 … 1.0 | [rect, ellipse] top edge, fraction of the frame height. |
| `width` | number | `0.15` | 0.0 … 1.0 | [rect, ellipse] width, fraction of the frame width. |
| `height` | number | `1.0` | 0.0 … 1.0 | [rect, ellipse] height, fraction of the frame height. |
| `polygon` | points |  |  | [polygon] 3 to 64 corner points [[x, y], ...], each 0-1 (fractions of the frame). |
| `feather` | number | `6.0` | 0.0 … 60.0 px at 1080p | Soft edge of the shape, so the correction blends in. |
| `invert` | boolean | `False` |  | Apply the mask OUTSIDE the shape instead of inside (e.g. garbage everything except the area around the person). |

## `background_removal` — Background Removal

Remove the background. 'ai' works on ordinary footage (no green screen) using the Robust Video Matting model; 'chroma' keys a green/blue screen.

◆ = can be keyframed (animated over time).

Timeline track: **effects** · provides an alpha layer for `background_replacement`

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `amount` | number | `1.0` | 0.0 … 1.0 x | Master amount: how much of the removal applies. 1.0 = fully removed, 0 = the original footage (the removal is mixed out), in between = a cross-fade between the two. For a transparent export it is the matte strength. |
| `mode` | enum | `chroma` | chroma, ai, screen | 'screen' = professional green/blue-screen key (hair-level partial transparency, automatic screen colour, uneven lighting, clean edges; no model); 'ai' = person matting without green screen (downloads a 15 MB model on first use); 'chroma' = simple colour key. |
| `auto_screen` | boolean | `True` |  | [screen] Find the screen colour automatically (and follow it over time). Off = use key_color. |
| `fill_holes` | boolean | `False` |  | [screen] Fill tiny enclosed see-through spots inside the subject (key noise on green-ish clothing). Off by default: in dense hair it can fill real gaps between strands. |
| `edge_shrink` | number | `0.0` | 0.0 … 5.0 px | [screen] Pull the matte edge inward to remove a remaining fringe. |
| `edge_soften` | number | `0.0` | 0.0 … 10.0 px | [screen] Soften the matte edge. |
| `hair_detail` | number | `0.0` | 0.0 … 1.0 | [screen] HAIR DETAIL: also run the AI matting model and let it RESTORE what the key lost in a band around the subject's edge (dark or green-tinted strands, dropouts). It can only add: the key's crisp strands are never softened, the AI's wider halo over pure screen never comes in, and a frame where the AI does not find the same person uses the plain key. On clean footage the result is almost identical to the plain key. 0 = off. Downloads a 15 MB model on first use and is slower (it runs the network on every frame). |
| `hair_width` | number | `16.0` | 2.0 … 60.0 px at 1080p | [screen, hair_detail] How far from the edge (outside, and a little inside) the AI may restore strands. Wider catches stray strands further out; narrower trusts the key more. |
| `clean_plate_asset_id` | asset |  |  | [screen] CLEAN PLATE: a recording (or picture) of the EMPTY screen with the same camera, framing and lighting. The keyer then knows the screen pixel by pixel (wrinkles, folds, hot spots, shadow bands) instead of guessing, which gives much cleaner edges. Only the slow brightness drift is corrected; if it does not fit a frame (camera moved) that frame uses the normal estimate. |
| `clean_plate_strength` | number | `1.0` | 0.0 … 1.0 | [screen] How much of the clean plate to use (1 = fully, 0 = ignore it). |
| `temporal_smooth` | number | `0.5` | 0.0 … 1.0 | [screen] Calm edge flicker on still parts of the picture; moving edges pass through unsmoothed. |
| `model` | enum | `rvm` | rvm | [ai] Matting model. rvm = Robust Video Matting (GPL-3.0), hair-level detail, temporally stable. |
| `detail` | enum | `fast` | fast, balanced, high | [ai] Network resolution. fast is the most robust on busy/patterned backgrounds; balanced/high resolve finer hair on plain backgrounds but are slower and can pick up background texture. |
| `matte_threshold` | number | `0.03` | 0.0 … 0.5 | [ai, screen] Alpha below this becomes fully transparent (removes faint background haze). |
| `solidity` | number | `0.0` | 0.0 … 1.0 | [ai, screen] Make partly see-through areas inside the person solid: motion-blurred hands, fine fabric. 0 = natural (default); 0.4-0.7 fixes see-through fingers; higher makes hair edges harder. |
| `despeckle` | boolean | `True` |  | [ai, screen] Remove background blobs that are not connected to the person. |
| `edge_color_fix` | boolean | `True` |  | [ai, screen] Replace old-background colour in soft hair edges with the estimated foreground colour (removes halos). |
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
| `subject_scale` | number | `1.0` | 0.3 … 2.5 | Resize the keyed subject (1 = as filmed). Above 1 is safe; below 1 reveals the footage's own frame edge where the person touches it (shoulders), see SUBJECT_EDGE_VISIBLE. |
| `subject_x` | number | `0.0` | -1.0 … 1.0 | Move the subject sideways, fraction of frame width. |
| `subject_y` | number | `0.0` | -1.0 … 1.0 | Move the subject up (-) or down (+), fraction of frame height. |
| `subject_anchor` | enum | `bottom` | bottom, center | Point that stays put when the subject is resized. 'bottom' keeps the cut-off shoulders on the frame's bottom edge. |
| `shadow` | number | `0.0` | 0.0 … 1.0 | Opacity of a soft cast shadow of the subject on the background (0 = off). |
| `shadow_blur` | number | `28.0` | 1.0 … 100.0 px at 1080p | Softness of the shadow; bigger = a wall further behind the subject. |
| `shadow_dx` | number | `0.03` | -0.3 … 0.3 | Shadow offset sideways, fraction of frame width (opposite the key light). |
| `shadow_dy` | number | `0.02` | -0.3 … 0.3 | Shadow offset downward, fraction of frame height. |
| `floor_y` | number |  | 0.0 … 1.0 | [reflection, contact shadow] Where the floor is, as a fraction of the frame height from the top. Empty = automatic: the lowest point of the subject on the middle frame. If the subject is cut off by the bottom of the frame there is no floor in view and both effects are skipped. |
| `reflection` | number | `0.0` | 0.0 … 1.0 | Floor reflection: the subject mirrored about the floor line, fading with distance. 0 = off. |
| `reflection_length` | number | `0.25` | 0.05 … 0.8 | [reflection] How far down the reflection reaches, as a fraction of the frame height. |
| `reflection_blur` | number | `6.0` | 0.0 … 40.0 px at 1080p | [reflection] Softness of the reflection (a polished floor is sharp, a matte floor blurry). |
| `contact_shadow` | number | `0.0` | 0.0 … 1.0 | Contact shadow: a tight dark blob under the feet, made from the lowest slice of the subject, so it follows every step. 0 = off. |
| `contact_height` | number | `0.03` | 0.005 … 0.2 | [contact shadow] How tall a slice of the subject (above the floor line) makes the shadow, as a fraction of the frame height. |
| `contact_blur` | number | `10.0` | 1.0 … 60.0 px at 1080p | [contact shadow] Softness; wider and softer for a diffuse light, tighter for a hard one. |

## `color_grading` — Color Grading

Exposure, contrast, tone, white balance, saturation, LGG, RGB curves, LUT, presets.

◆ = can be keyframed (animated over time).

Timeline track: **effects**

| parameter | type | default | range / values | description |
|---|---|---|---|---|
| `amount` | number | `1.0` | 0.0 … 1.5 x | Master amount. 1.0 = the effect as set below, 0 = the original footage (the effect is mixed out), above 1 = pushed further. The sliders below keep their own values. |
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
| `texture` | number | `0.0` | -1.0 … 1.0 | Fine/medium detail on the brightness only (skin pores, fabric, hair): + sharpens it, - smooths it. Colour is untouched. Between sharpen (finest) and clarity (broad). |
| `clarity` | number | `0.0` | -1.0 … 1.0 | Broad local contrast on the brightness only (punch, depth): + adds it, - softens it to a dreamy look. Wider than texture; colour is untouched. |
| `dehaze` | number | `0.0` | -1.0 … 1.0 | + removes a milky veil (deeper blacks, more contrast and colour in hazy areas), - adds haze. A global approximation: one curve for the whole frame, not a per-pixel haze map. |
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
| `amount` | number | `1.0` | 0.0 … 1.5 x | Master amount. 1.0 = the effect as set below, 0 = the original footage (the effect is mixed out), above 1 = pushed further. The sliders below keep their own values. |
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
| `auto_match` | number | `0.0` | 0.0 … 1.0 | Measure the background (and the subject) and set the controls you did not set yourself, the way a colourist reads a scene: light and shadow colours, which side the light comes from (drives the cast shadow), contrast, saturation, glow, vignette, depth, subject exposure, edge light. 0 = off, 1 = fully automatic. Your own values always win. The measured values are reported with the plan. |
| `subject_exposure` | number |  | -1.5 … 1.5 EV | [needs background_replacement] Brighten or darken only the subject to sit naturally in the scene. |
| `reference_asset_id` | asset |  |  | A reference picture whose overall colour and tone you want. The app measures how the finished frame differs from it (per colour channel, mid-point frame of the look's range) and adds the correction as RGB curves. It matches colour and tone only - not content, framing or lighting direction. |
| `reference_strength` | number | `0.8` | 0.0 … 1.0 | How much of the measured correction to apply. |
| `camera_match` | number | `0.0` | 0.0 … 1.0 | [needs background_replacement] CAMERA MATCH: measure how the camera 'saw' the subject and the background (sharpness, grain, black level) and make the subject match the scene, so it does not look pasted on. 0 = off, 1 = the measured correction. Controls you set below always win. |
| `match_softness` | number |  | -3.0 … 6.0 px at 1080p | [needs background_replacement] Blur (+) or sharpen (-) the SUBJECT only (its edge too). Automatic with camera_match. |
| `subject_grain` | number |  | 0.0 … 2.0 | [needs background_replacement] Film grain / sensor noise added to the subject only (1.0 = about 4.6 levels of noise). Automatic with camera_match when the background is grainier. |
| `background_grain` | number |  | 0.0 … 2.0 | [needs background_replacement] Grain added to the background only, so a clean picture gets the noise of the footage. Automatic with camera_match when the footage is grainier. |
| `match_blacks` | number |  | -0.25 … 0.25 | [needs background_replacement] Move the SUBJECT's black point to the scene's: + lifts blacks (a milky / faded background), - deepens them. Automatic with camera_match. |
| `fringe` | number | `0.0` | 0.0 … 4.0 px at 1080p | Colour fringing of a lens (lateral chromatic aberration): red and blue separate from green towards the frame edges, by this many pixels at the very edge, on the whole picture. |

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

> **Not available in this build.** Not a separate operation: use background_removal with mode 'screen' (green/blue screen) or 'ai' (no screen). Both produce hair-level partial transparency, solve the subject colour out of edge pixels and clean the matte; edge_shrink, edge_soften and temporal_smooth fine-tune it.

