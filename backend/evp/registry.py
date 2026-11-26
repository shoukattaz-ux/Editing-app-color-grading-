"""Operation registry: the ONLY operations a blueprint may invoke.

Every operation declares its parameters (type, default, limits). The validator,
the compiler, the properties panel in the UI and the published JSON schema are
all generated from this single source of truth.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Any

# Pipeline stages: lower runs earlier in the filter graph.
STAGE_UPSCALE = 5       # AI super-resolution replaces the base scaler
STAGE_RESTORE = 10      # stabilise / denoise
STAGE_KEY = 20          # produce an alpha layer from the footage
STAGE_COMPOSITE = 30    # place keyed subject over a new background
STAGE_FACE = 35         # face tone / relight (before the global grade, like a colourist's face pass)
STAGE_GRADE = 40
STAGE_LOOK = 45         # studio look: the finishing grade over the whole composite
STAGE_FINISH = 50       # sharpen
STAGE_OVERLAY = 60      # text
STAGE_AUDIO = 70

TRACKS = {"video": "Video", "background": "Background", "effects": "Effects",
          "overlay": "Overlay", "audio": "Audio"}


@dataclass(frozen=True)
class Param:
    type: str                      # number | integer | boolean | string | color | enum | asset | points
    default: Any = None
    min: float | None = None
    max: float | None = None
    enum: tuple = ()
    required: bool = False
    desc: str = ""
    unit: str = ""
    asset_types: tuple = ()        # for type == "asset"
    animatable: bool = False       # can be keyframed (only parameters whose animated render was verified exact)
    static_reason: str = ""        # why it cannot be animated (shown in validation errors)

    def to_json(self) -> dict:
        d: dict[str, Any] = {"type": self.type, "description": self.desc}
        if self.default is not None: d["default"] = self.default
        if self.min is not None: d["minimum"] = self.min
        if self.max is not None: d["maximum"] = self.max
        if self.enum: d["enum"] = list(self.enum)
        if self.required: d["required"] = True
        if self.unit: d["unit"] = self.unit
        if self.asset_types: d["asset_types"] = list(self.asset_types)
        d["animatable"] = self.animatable
        return d


@dataclass(frozen=True)
class OpSpec:
    type: str
    title: str
    category: str
    stage: int
    track: str
    description: str
    params: dict[str, Param] = field(default_factory=dict)
    supported: bool = True
    unsupported_reason: str = ""
    # does this op yield an alpha layer that background_replacement can consume?
    provides_alpha: bool = False
    # does it need a range? (all do; start/end default to whole video)
    needs_audio: bool = False

    def to_json(self) -> dict:
        return {"type": self.type, "title": self.title, "category": self.category,
                "track": self.track, "description": self.description,
                "supported": self.supported, "unsupported_reason": self.unsupported_reason,
                "provides_alpha": self.provides_alpha,
                "parameters": {k: v.to_json() for k, v in self.params.items()}}


from .studio import PRESET_NAMES
N, I, B, S, C, E, A, PT = ("number", "integer", "boolean", "string", "color", "enum", "asset", "points")

_KEY_PARAMS = {
    "key_color": Param(C, "#00ff00", desc="Colour to remove: '#rrggbb', 'green' or 'blue'."),
    "similarity": Param(N, 0.12, 0.01, 1.0, desc="How close a pixel must be to the key colour to become transparent."),
    "blend": Param(N, 0.08, 0.0, 1.0, desc="Soft transition between keyed and kept pixels."),
    "spill_suppression": Param(B, True, desc="Remove green/blue colour cast from the subject's edges."),
    "spill_strength": Param(N, 0.5, 0.0, 1.0, desc="Despill mix strength."),
    "edge_erode": Param(I, 0, 0, 10, unit="px", desc="Shrink the matte to remove fringe."),
    "edge_feather": Param(N, 0.0, 0.0, 20.0, unit="px", desc="Gaussian softening of the matte edge."),
}

# Master amount (Inspector 'Amount' slider): one control that fades the whole effect between the original footage and the full effect
_AMOUNT_FX = Param(N, 1.0, 0.0, 1.5, unit="x", desc="Master amount. 1.0 = the effect as set below, 0 = the original footage (the effect is mixed out), above 1 = pushed further. The sliders below keep their own values.")
_AMOUNT_KEY = Param(N, 1.0, 0.0, 1.0, unit="x", desc="Master amount: how much of the removal applies. 1.0 = fully removed, 0 = the original footage (the removal is mixed out), in between = a cross-fade between the two. For a transparent export it is the matte strength.")

_COLOR_PARAMS = {
    "auto_enhance": Param(N, 0.0, 0.0, 1.5, desc="AUTO ENHANCE master slider: the clip is measured (haze, colour cast, exposure, contrast, colourfulness) and the matching corrections - dehaze, blacks, whites, highlights, exposure, contrast, white balance, vibrance, clarity, texture - are applied together. 0 = off, 1 = the measured correction, 1.5 = pushed further. Your own sliders in this Color Grading add on top."),
    "cinematic": Param(N, 0.0, 0.0, 1.5, desc="CINEMATIC LOOK: a real film grade in one control - filmic tone curve (soft lifted blacks, rolled-off highlights), colour by hue (skin stays warm, greens toward teal, magentas / blues calmed), split tone with brightness preserved, restrained saturation, vignette and fine film grain. 0 = off, 1 = the full look, 1.5 = stronger."),
    "cinematic_style": Param(E, "teal_orange", enum=("teal_orange", "moody", "golden", "bleach"), desc="[cinematic] teal_orange = the blockbuster look (teal shadows, warm skin); moody = darker, muted, cold shadows; golden = warm golden-hour film; bleach = low-saturation, high-contrast bleach bypass."),
    "letterbox": Param(E, "none", enum=("none", "2.39", "2.0", "1.85"), desc="Black cinema bars top and bottom so the picture shows at this aspect ratio (2.39 = anamorphic widescreen). Applies where this grade runs."),
    "apply_to": Param(E, "both", enum=("both", "subject", "background"), desc="Where this grade (and its auto_enhance) applies: both = the whole picture; subject = only the keyed person / object (its cut-out is untouched, the background never changes); background = only the picture behind the subject. subject / background need a background_replacement."),
    "enhance_style": Param(E, "crisp", enum=("natural", "crisp", "vivid"), desc="[auto_enhance] natural = gentle, keeps the mood; crisp = clean, cool and sharp (a clear-day look); vivid = more colour and punch."),
    "preset": Param(E, "none", enum=("none", "cinematic_teal_orange", "warm_film", "cool_blue", "bleach_bypass", "faded_matte", "high_contrast_bw"),
                    desc="Starting look; individual controls are applied on top."),
    "exposure": Param(N, 0.0, -3.0, 3.0, unit="EV", desc="Exposure compensation in stops."),
    "contrast": Param(N, 1.0, 0.0, 3.0, desc="1.0 = unchanged."),
    "brightness": Param(N, 0.0, -1.0, 1.0),
    "highlights": Param(N, 0.0, -1.0, 1.0, desc="Lift (+) or pull down (-) highlights."),
    "shadows": Param(N, 0.0, -1.0, 1.0, desc="Lift (+) or crush (-) shadows."),
    "whites": Param(N, 0.0, -1.0, 1.0),
    "blacks": Param(N, 0.0, -1.0, 1.0),
    "temperature": Param(N, 0.0, -100.0, 100.0, desc="-100 cool (blue) ... +100 warm (orange)."),
    "tint": Param(N, 0.0, -100.0, 100.0, desc="-100 green ... +100 magenta."),
    "saturation": Param(N, 1.0, 0.0, 3.0, desc="1.0 = unchanged."),
    "vibrance": Param(N, 0.0, -2.0, 2.0),
    "gamma": Param(N, 1.0, 0.1, 5.0),
    "texture": Param(N, 0.0, -1.0, 1.0, desc="Fine/medium detail on the brightness only (skin pores, fabric, hair): + sharpens it, - smooths it. Colour is untouched. Between sharpen (finest) and clarity (broad)."),
    "clarity": Param(N, 0.0, -1.0, 1.0, desc="Broad local contrast on the brightness only (punch, depth): + adds it, - softens it to a dreamy look. Wider than texture; colour is untouched."),
    "dehaze": Param(N, 0.0, -1.0, 1.0, desc="+ removes a milky veil (deeper blacks, more contrast and colour in hazy areas), - adds haze. A global approximation: one curve for the whole frame, not a per-pixel haze map."),
    "lift": Param(PT, None, desc="[r,g,b] each -1..1. Lift: out = in + lift*(1-in): moves the black point per channel, white stays. Equal values = brightness of the shadows; unequal = colour cast in the shadows (colour wheel)."),
    "gamma_rgb": Param(PT, None, desc="[r,g,b] each -1..1. Gamma: out = in ** 2**(-gamma): bends the midtones per channel, black and white stay (+ brightens). Colour wheel for the midtones."),
    "gain": Param(PT, None, desc="[r,g,b] each -1..1. Gain: out = in * (1+gain): scales per channel, black stays. Colour wheel for the highlights."),
    "curve_master": Param(PT, None, desc="Master curve control points [[x,y],...] in 0..1."),
    "curve_red": Param(PT, None, desc="Red curve control points [[x,y],...] in 0..1."),
    "curve_green": Param(PT, None, desc="Green curve control points [[x,y],...] in 0..1."),
    "curve_blue": Param(PT, None, desc="Blue curve control points [[x,y],...] in 0..1."),
    "lut_asset_id": Param(A, None, asset_types=("lut",), desc="A .cube/.3dl LUT asset to apply."),
    "lut_intensity": Param(N, 1.0, 0.0, 1.0, desc="Blend between graded-without-LUT and LUT."),
}

_MODEL_REASON = ("Requires a trained model (and ideally a GPU) that is not bundled. "
                 "No fake fallback is provided: use a supported operation or add a model plugin.")

OPERATIONS: dict[str, OpSpec] = {}


def _reg(spec: OpSpec):
    OPERATIONS[spec.type] = spec


_reg(OpSpec("chroma_key", "Chroma Key", "Keying", STAGE_KEY, "effects",
            "Key out a solid colour (green/blue screen) and composite on a background colour or the next replacement.",
            {"amount": _AMOUNT_KEY, **_KEY_PARAMS, "background_color": Param(C, "#000000", desc="Fill used when no background_replacement consumes this key.")},
            provides_alpha=True))
_reg(OpSpec("matte_mask", "Garbage / Keep Mask", "Keying", STAGE_KEY, "effects",
            "Correct a key by hand with a shape. Garbage = this area is always removed (lights, stands, the edge of the screen). Keep = this area is always kept (a face or "
            "shirt the key made see-through). Applies to the keying operation(s) it overlaps (or the one in depends_on); coordinates are fractions of the frame (0-1, "
            "x/y from the top-left), so they work at any resolution.",
            {"mode": Param(E, "garbage", enum=("garbage", "keep"), desc="garbage = always removed; keep = always kept."),
             "shape": Param(E, "rect", enum=("rect", "ellipse", "polygon"), desc="rect and ellipse use x, y, width, height; polygon uses `polygon`."),
             "x": Param(N, 0.0, 0.0, 1.0, desc="[rect, ellipse] left edge, fraction of the frame width."),
             "y": Param(N, 0.0, 0.0, 1.0, desc="[rect, ellipse] top edge, fraction of the frame height."),
             "width": Param(N, 0.15, 0.0, 1.0, desc="[rect, ellipse] width, fraction of the frame width."),
             "height": Param(N, 1.0, 0.0, 1.0, desc="[rect, ellipse] height, fraction of the frame height."),
             "polygon": Param(PT, None, desc="[polygon] 3 to 64 corner points [[x, y], ...], each 0-1 (fractions of the frame)."),
             "feather": Param(N, 6.0, 0.0, 60.0, unit="px at 1080p", desc="Soft edge of the shape, so the correction blends in."),
             "invert": Param(B, False, desc="Apply the mask OUTSIDE the shape instead of inside (e.g. garbage everything except the area around the person).")}))
_reg(OpSpec("background_removal", "Background Removal", "Keying", STAGE_KEY, "effects",
            "Remove the background. 'ai' works on ordinary footage (no green screen) using the Robust Video Matting model; "
            "'chroma' keys a green/blue screen.",
            {"amount": _AMOUNT_KEY,
             "mode": Param(E, "chroma", enum=("chroma", "ai", "screen"), desc="'screen' = professional green/blue-screen key (hair-level partial transparency, automatic screen colour, uneven lighting, clean edges; no model); 'ai' = person matting without green screen (downloads a 15 MB model on first use); 'chroma' = simple colour key."),
             "auto_screen": Param(B, True, desc="[screen] Find the screen colour automatically (and follow it over time). Off = use key_color."),
             "fill_holes": Param(B, False, desc="[screen] Fill tiny enclosed see-through spots inside the subject (key noise on green-ish clothing). Off by default: in dense hair it can fill real gaps between strands."),
             "edge_shrink": Param(N, 0.0, 0.0, 5.0, unit="px", desc="[screen] Pull the matte edge inward to remove a remaining fringe."),
             "edge_soften": Param(N, 0.0, 0.0, 10.0, unit="px", desc="[screen] Soften the matte edge."),
             "hair_detail": Param(N, 0.0, 0.0, 1.0, desc="[screen] HAIR DETAIL: also run the AI matting model and let it RESTORE what the key lost in a band around the subject's edge (dark or green-tinted strands, dropouts). It can only add: the key's crisp strands are never softened, the AI's wider halo over pure screen never comes in, and a frame where the AI does not find the same person uses the plain key. On clean footage the result is almost identical to the plain key. 0 = off. Downloads a 15 MB model on first use and is slower (it runs the network on every frame)."),
             "hair_width": Param(N, 16.0, 2.0, 60.0, unit="px at 1080p", desc="[screen, hair_detail] How far from the edge (outside, and a little inside) the AI may restore strands. Wider catches stray strands further out; narrower trusts the key more."),
             "clean_plate_asset_id": Param(A, None, asset_types=("image", "video"), desc="[screen] CLEAN PLATE: a recording (or picture) of the EMPTY screen with the same camera, framing and lighting. The keyer then knows the screen pixel by pixel (wrinkles, folds, hot spots, shadow bands) instead of guessing, which gives much cleaner edges. Only the slow brightness drift is corrected; if it does not fit a frame (camera moved) that frame uses the normal estimate."),
             "clean_plate_strength": Param(N, 1.0, 0.0, 1.0, desc="[screen] How much of the clean plate to use (1 = fully, 0 = ignore it)."),
             "temporal_smooth": Param(N, 0.5, 0.0, 1.0, desc="[screen] Calm edge flicker on still parts of the picture; moving edges pass through unsmoothed."),
             "model": Param(E, "rvm", enum=("rvm",), desc="[ai] Matting model. rvm = Robust Video Matting (GPL-3.0), hair-level detail, temporally stable."),
             "detail": Param(E, "fast", enum=("fast", "balanced", "high"), desc="[ai] Network resolution. fast is the most robust on busy/patterned backgrounds; balanced/high resolve finer hair on plain backgrounds but are slower and can pick up background texture."),
             "matte_threshold": Param(N, 0.03, 0.0, 0.5, desc="[ai, screen] Alpha below this becomes fully transparent (removes faint background haze)."),
             "solidity": Param(N, 0.0, 0.0, 1.0, desc="[ai, screen] Make partly see-through areas inside the person solid: motion-blurred hands, fine fabric. 0 = natural (default); 0.4-0.7 fixes see-through fingers; higher makes hair edges harder."),
             "despeckle": Param(B, True, desc="[ai, screen] Remove background blobs that are not connected to the person."),
             "edge_color_fix": Param(B, True, desc="[ai, screen] Replace old-background colour in soft hair edges with the estimated foreground colour (removes halos)."),
             "quality": Param(E, "high", enum=("draft", "standard", "high"), desc="[chroma] Higher = more edge processing."),
             "edge_refinement": Param(B, True, desc="[chroma] Erode + feather the matte edge."),
             **{k: v for k, v in _KEY_PARAMS.items() if k not in ("edge_erode", "edge_feather")},
             "background_color": Param(C, "#000000", desc="Fill used when no background_replacement consumes this removal.")},
            provides_alpha=True))
_reg(OpSpec("background_replacement", "Background Replacement", "Compositing", STAGE_COMPOSITE, "background",
            "Place the keyed subject over an image, video or solid colour. Needs an alpha source (chroma_key/background_removal).",
            {"asset_id": Param(A, None, asset_types=("image", "video"), desc="Background asset. Omit to use `color`."),
             "color": Param(C, "#000000", desc="Solid background if no asset_id."),
             "fit": Param(E, "cover", enum=("cover", "contain", "stretch")),
             "scale": Param(N, 1.0, 0.1, 4.0, desc="Additional zoom on the background."),
             "offset_x": Param(N, 0.0, -1.0, 1.0, desc="Horizontal shift as fraction of frame width."),
             "offset_y": Param(N, 0.0, -1.0, 1.0, desc="Vertical shift as fraction of frame height."),
             "blur": Param(N, 0.0, 0.0, 50.0, unit="px", desc="Gaussian background blur (fake depth of field)."),
             "brightness": Param(N, 0.0, -1.0, 1.0, desc="Background brightness to match the subject."),
             "saturation": Param(N, 1.0, 0.0, 3.0, desc="Background saturation to match the subject."),
             "subject_scale": Param(N, 1.0, 0.3, 2.5, desc="Resize the keyed subject (1 = as filmed). Above 1 is safe; below 1 reveals the footage's own frame edge where the person touches it (shoulders), see SUBJECT_EDGE_VISIBLE."),
             "subject_x": Param(N, 0.0, -1.0, 1.0, desc="Move the subject sideways, fraction of frame width."),
             "subject_y": Param(N, 0.0, -1.0, 1.0, desc="Move the subject up (-) or down (+), fraction of frame height."),
             "subject_anchor": Param(E, "bottom", enum=("bottom", "center"), desc="Point that stays put when the subject is resized. 'bottom' keeps the cut-off shoulders on the frame's bottom edge."),
             "shadow": Param(N, 0.0, 0.0, 1.0, desc="Opacity of a soft cast shadow of the subject on the background (0 = off)."),
             "shadow_blur": Param(N, 28.0, 1.0, 100.0, unit="px at 1080p", desc="Softness of the shadow; bigger = a wall further behind the subject."),
             "shadow_dx": Param(N, 0.03, -0.3, 0.3, desc="Shadow offset sideways, fraction of frame width (opposite the key light)."),
             "shadow_dy": Param(N, 0.02, -0.3, 0.3, desc="Shadow offset downward, fraction of frame height."),
             "floor_y": Param(N, None, 0.0, 1.0, desc="[reflection, contact shadow] Where the floor is, as a fraction of the frame height from the top. Empty = automatic: the lowest point of the subject on the middle frame. If the subject is cut off by the bottom of the frame there is no floor in view and both effects are skipped."),
             "reflection": Param(N, 0.0, 0.0, 1.0, desc="Floor reflection: the subject mirrored about the floor line, fading with distance. 0 = off."),
             "reflection_length": Param(N, 0.25, 0.05, 0.8, desc="[reflection] How far down the reflection reaches, as a fraction of the frame height."),
             "reflection_blur": Param(N, 6.0, 0.0, 40.0, unit="px at 1080p", desc="[reflection] Softness of the reflection (a polished floor is sharp, a matte floor blurry)."),
             "contact_shadow": Param(N, 0.0, 0.0, 1.0, desc="Contact shadow: a tight dark blob under the feet, made from the lowest slice of the subject, so it follows every step. 0 = off."),
             "contact_height": Param(N, 0.03, 0.005, 0.2, desc="[contact shadow] How tall a slice of the subject (above the floor line) makes the shadow, as a fraction of the frame height."),
             "contact_blur": Param(N, 10.0, 1.0, 60.0, unit="px at 1080p", desc="[contact shadow] Softness; wider and softer for a diffuse light, tighter for a hard one.")}))
_reg(OpSpec("color_grading", "Color Grading", "Color", STAGE_GRADE, "effects",
            "Exposure, contrast, tone, white balance, saturation, LGG, RGB curves, LUT, presets.", {"amount": _AMOUNT_FX, **_COLOR_PARAMS}))
_reg(OpSpec("studio_look", "Studio Look", "Color", STAGE_LOOK, "effects",
            "One-step 'real studio' finish. With a background_replacement it first integrates the subject into the new scene (the background's colour falls on the subject, "
            "its light wraps around the edges, slight depth of field), then grades the whole composite (S-curve, split-tone, highlight glow, vignette, local contrast, film grain). "
            "Without a replacement only the global grade runs. Pick a preset; any control you set overrides it.",
            {"amount": _AMOUNT_FX,
             "preset": Param(E, "studio_warm", enum=PRESET_NAMES, desc="Starting look. Controls you set override it; 'none' starts from nothing."),
             "ambient_match": Param(N, None, 0.0, 1.0, desc="[needs background_replacement] Subject picks up the background's colour cast, following where the light is in the scene."),
             "light_wrap": Param(N, None, 0.0, 1.0, desc="[needs background_replacement] Background light spilling over the subject's edges - removes the 'cut-out' look."),
             "wrap_width": Param(N, None, 1.0, 60.0, unit="px at 1080p", desc="[needs background_replacement] How far the light wrap reaches into the subject."),
             "depth": Param(N, None, 0.0, 1.0, desc="[needs background_replacement] Extra background softness (depth of field) so the subject stands out."),
             "contrast": Param(N, None, 0.0, 1.0, desc="S-curve strength."),
             "shadow_tint": Param(C, None, desc="Colour pushed into the shadows (only its hue matters)."),
             "highlight_tint": Param(C, None, desc="Colour pushed into the highlights (only its hue matters)."),
             "tint_amount": Param(N, None, 0.0, 1.0, desc="Strength of the split-tone."),
             "glow": Param(N, None, 0.0, 1.0, desc="Soft bloom around bright areas (lamps, screens)."),
             "glow_threshold": Param(N, None, 0.3, 0.95, desc="Brightness above which the glow starts."),
             "glow_radius": Param(N, None, 1.0, 60.0, unit="px at 1080p", desc="Glow spread."),
             "vignette": Param(N, None, 0.0, 1.0, desc="Darken the frame edges."),
             "clarity": Param(N, None, 0.0, 1.0, desc="Local contrast (mid-frequency sharpening)."),
             "grain": Param(N, None, 0.0, 1.0, desc="Fine film grain; also hides banding and the 'digital' look of a composite."),
             "saturation": Param(N, None, 0.5, 1.5, desc="Overall saturation."),
             "auto_match": Param(N, 0.0, 0.0, 1.0, desc="Measure the background (and the subject) and set the controls you did not set yourself, the way a colourist reads a scene: light and shadow colours, which side the light comes from (drives the cast shadow), contrast, saturation, glow, vignette, depth, subject exposure, edge light. 0 = off, 1 = fully automatic. Your own values always win. The measured values are reported with the plan."),
             "subject_exposure": Param(N, None, -1.5, 1.5, unit="EV", desc="[needs background_replacement] Brighten or darken only the subject to sit naturally in the scene."),
             "reference_asset_id": Param(A, None, asset_types=("image",), desc="A reference picture whose overall colour and tone you want. The app measures how the finished frame differs from it (per colour channel, mid-point frame of the look's range) and adds the correction as RGB curves. It matches colour and tone only - not content, framing or lighting direction."),
             "reference_strength": Param(N, 0.8, 0.0, 1.0, desc="How much of the measured correction to apply."),
             "camera_match": Param(N, 0.0, 0.0, 1.0, desc="[needs background_replacement] CAMERA MATCH: measure how the camera 'saw' the subject and the background (sharpness, grain, black level) and make the subject match the scene, so it does not look pasted on. 0 = off, 1 = the measured correction. Controls you set below always win."),
             "match_softness": Param(N, None, -3.0, 6.0, unit="px at 1080p", desc="[needs background_replacement] Blur (+) or sharpen (-) the SUBJECT only (its edge too). Automatic with camera_match."),
             "subject_grain": Param(N, None, 0.0, 2.0, desc="[needs background_replacement] Film grain / sensor noise added to the subject only (1.0 = about 4.6 levels of noise). Automatic with camera_match when the background is grainier."),
             "background_grain": Param(N, None, 0.0, 2.0, desc="[needs background_replacement] Grain added to the background only, so a clean picture gets the noise of the footage. Automatic with camera_match when the footage is grainier."),
             "match_blacks": Param(N, None, -0.25, 0.25, desc="[needs background_replacement] Move the SUBJECT's black point to the scene's: + lifts blacks (a milky / faded background), - deepens them. Automatic with camera_match."),
             "light_pattern": Param(N, 0.0, 0.0, 1.0, desc="[needs background_replacement] LIGHT PATTERN: moving ripple light (caustics, like sun through water) projected onto the SUBJECT only, so it is lit by the same light as the scene. 0 = off. The pattern is generated; or use your own with light_pattern_asset_id."),
             "light_pattern_scale": Param(N, 1.0, 0.3, 3.0, desc="[light_pattern] Size of the ripples (1 = about nine cells across the frame; larger = bigger, calmer ripples)."),
             "light_pattern_speed": Param(N, 1.0, 0.0, 3.0, desc="[light_pattern] How fast the ripples move (0 = a still pattern)."),
             "light_pattern_falloff": Param(N, 0.6, 0.0, 1.0, desc="[light_pattern] How much weaker the light is towards the bottom of the frame (it comes from above): 0 = even, 1 = nothing at the bottom."),
             "light_pattern_style": Param(E, "ripples", enum=("ripples", "solid", "particles"), desc="[light_pattern] ripples = a moving web of thin bright lines (caustics); solid = hard-edged moving patches of light (dappled light); particles = drifting glints / dust / bubbles of light."),
             "light_pattern_color": Param(C, "#ffffff", desc="[light_pattern] Colour of the light. White leaves the subject's own colours; any other colour tints the lit parts towards it (cyan, gold, warm white ...)."),
             "light_pattern_color2": Param(C, None, desc="[light_pattern] Optional second colour for the darker ground between the lit parts (e.g. a deep blue for a two-tone light). Empty = no tint there."),
             "light_pattern_spread": Param(N, 0.15, 0.0, 1.0, desc="[light_pattern] How far the light spreads: 0 = crisp lines / hard edges, 1 = a wide soft glow."),
             "light_pattern_warp": Param(N, 1.0, 0.0, 2.0, desc="[light_pattern, ripples / solid] How strongly the pattern bends and wobbles (0 = straight regular lines, 2 = heavily distorted water)."),
             "light_pattern_on": Param(E, "subject", enum=("subject", "background", "both"), desc="[light_pattern] Where the light falls. subject (default) = only on the keyed subject: it can never appear on the background, even with a wide spread. background = only on the picture behind. both = on both."),
             "colour_light": Param(N, 0.0, 0.0, 1.0, desc="[needs background_replacement] COLOUR LIGHTS: stage-style coloured lights on the SUBJECT only (e.g. cyan from the left and magenta from the right, like a studio portrait with gels), fading across the subject, with a glow that hugs the edge facing each light. 0 = off. Never lands on the background."),
             "colour_light_color_a": Param(C, "#19b8ff", desc="[colour_light] Colour of light A (default cyan-blue)."),
             "colour_light_side_a": Param(E, "left", enum=("left", "right", "top", "bottom", "off"), desc="[colour_light] Which side light A comes from (off = unused)."),
             "colour_light_color_b": Param(C, "#b01cff", desc="[colour_light] Colour of light B (default magenta-purple)."),
             "colour_light_side_b": Param(E, "right", enum=("left", "right", "top", "bottom", "off"), desc="[colour_light] Which side light B comes from (off = unused)."),
             "colour_light_reach": Param(N, 0.5, 0.0, 1.0, desc="[colour_light] How far each light reaches across the subject (0 = only the near edge, 1 = across the whole subject, where the two colours mix)."),
             "colour_light_place": Param(E, "fixed", enum=("fixed", "follow", "zone"), desc="[colour_light] Where the lights sit. fixed (default) = the zone is placed from the subject's size and position on the MIDDLE frame and stays put in the picture (a stage light); follow = the zone follows the subject through the clip (measured at up to 24 moments (about every quarter second) and smoothly interpolated), so the light stays on the subject wherever it goes; zone = the lights are confined to a rectangle YOU place (colour_light_zone_*) and nothing outside it is lit."),
             "colour_light_zone_x0": Param(N, 0.25, 0.0, 1.0, desc="[colour_light, place=zone] Left edge of your zone, as a fraction of the frame width."),
             "colour_light_zone_y0": Param(N, 0.1, 0.0, 1.0, desc="[colour_light, place=zone] Top edge of your zone, as a fraction of the frame height."),
             "colour_light_zone_x1": Param(N, 0.75, 0.0, 1.0, desc="[colour_light, place=zone] Right edge of your zone (fraction of the frame width; must be larger than colour_light_zone_x0)."),
             "colour_light_zone_y1": Param(N, 0.9, 0.0, 1.0, desc="[colour_light, place=zone] Bottom edge of your zone (fraction of the frame height; must be larger than colour_light_zone_y0)."),
             "colour_light_zone_soft": Param(N, 0.3, 0.0, 1.0, desc="[colour_light, place=zone] How softly the light fades out at the zone's edges (0 = a hard edge)."),
             "colour_light_edge": Param(N, 0.6, 0.0, 1.0, desc="[colour_light] Extra glow along the subject's outline on the side facing each light (rim light on jaw, cheek and hair). 0 = none."),
             "light_pattern_asset_id": Param(A, None, asset_types=("image", "video"), desc="[light_pattern] Your own light pattern (a picture or a looping video: bright web / rays on a dark ground) instead of the generated ripples."),
             "fringe": Param(N, 0.0, 0.0, 4.0, unit="px at 1080p", desc="Colour fringing of a lens (lateral chromatic aberration): red and blue separate from green towards the frame edges, by this many pixels at the very edge, on the whole picture.")}))
_reg(OpSpec("video_enhancement", "Video Enhancement", "Enhance", STAGE_RESTORE, "effects",
            "Noise reduction, sharpening and basic stabilisation (classical algorithms; limited by source quality).",
            {"denoise": Param(N, 0.0, 0.0, 1.0, desc="hqdn3d strength."),
             "sharpen": Param(N, 0.0, 0.0, 2.0, desc="Unsharp-mask amount."),
             "stabilize": Param(B, False, desc="Single-pass deshake. Modest results; fully-featured stabilisation is not claimed."),
             "stabilize_strength": Param(I, 16, 1, 64, desc="Max shift in px searched by deshake.")}))
_reg(OpSpec("portrait_blur", "Portrait Mode (background blur)", "Enhance", STAGE_KEY - 1, "effects",
            "Keeps the person sharp and blurs the REAL background behind them, like a portrait lens - no green screen needed. "
            "The person is found by the AI matting model; the background is blurred with the person masked out, so no halo.",
            {"blur": Param(N, 0.6, 0.0, 1.0, desc="How strongly the background is blurred (0 = sharp, 1 = very soft, like a wide-open lens)."),
             "falloff": Param(N, 0.5, 0.0, 1.0, desc="Depth: background close to the person is a little less blurred than background far away, like a real lens (0 = the same blur everywhere)."),
             "edge": Param(N, 0.3, 0.0, 1.0, desc="Softness of the transition between the sharp person and the blurred background (hair edges)."),
             "model": Param(E, "rvm", enum=("rvm",), desc="Matting model that finds the person."),
             "detail": Param(E, "fast", enum=("fast", "balanced", "high"), desc="Network resolution of the person detection (fast is the most robust on busy backgrounds)."),
             "matte_threshold": Param(N, 0.03, 0.0, 0.5, desc="Faint detections below this count as background."),
             "solidity": Param(N, 0.3, 0.0, 1.0, desc="Makes partly see-through parts of the person solid (so a moving hand stays sharp)."),
             "despeckle": Param(B, True, desc="Ignore small detections that are not connected to the person."),
             "edge_color_fix": Param(B, True, desc="Between fine hair strands, use the person's estimated colour instead of the old sharp background, so the blur shows through the gaps.")}))
_reg(OpSpec("text_overlay", "Text Overlay", "Graphics", STAGE_OVERLAY, "overlay",
            "Draw static text.",
            {"text": Param(S, None, required=True, desc="Text to draw (max 500 chars)."),
             "font_size": Param(I, 48, 8, 400),
             "color": Param(C, "#ffffff"),
             "x": Param(N, 0.5, 0.0, 1.0, desc="Horizontal anchor (fraction, centred)."),
             "y": Param(N, 0.85, 0.0, 1.0, desc="Vertical anchor (fraction, centred)."),
             "opacity": Param(N, 1.0, 0.0, 1.0, desc="Text opacity (keyframe it for fade in/out titles)."),
             "box": Param(B, True, desc="Draw a translucent box behind the text."),
             "box_opacity": Param(N, 0.5, 0.0, 1.0)}))
_reg(OpSpec("audio_gain", "Audio Gain", "Audio", STAGE_AUDIO, "audio",
            "Change volume over a range.",
            {"gain_db": Param(N, 0.0, -60.0, 24.0, unit="dB"),
             "fade_in": Param(N, 0.0, 0.0, 30.0, unit="s"),
             "fade_out": Param(N, 0.0, 0.0, 30.0, unit="s")}, needs_audio=True))

_FACE_COMMON = {
    "feather": Param(N, 0.5, 0.0, 1.0, desc="Softness of the face region edge (relative to face size)."),
    "detection_confidence": Param(N, 0.6, 0.3, 0.95, desc="Face detector threshold. Lower finds small/turned faces but may misfire."),
}
_reg(OpSpec("face_tone_correction", "Face & Skin Tone Correction", "Face", STAGE_FACE, "effects",
            "Detects and tracks faces (YuNet), balances face exposure automatically and adjusts skin colour only on the "
            "person's own skin tone. Never smooths skin or changes facial features.",
            {"strength": Param(N, 1.0, 0.0, 1.0, desc="Overall amount of the correction."),
             "auto_exposure": Param(B, True, desc="Bring the face toward target_brightness, smoothly over time."),
             "target_brightness": Param(N, 0.55, 0.3, 0.8, desc="Desired median skin brightness (0..1) for auto exposure."),
             "auto_strength": Param(N, 0.7, 0.0, 1.0, desc="How far auto exposure moves toward the target."),
             "max_auto_ev": Param(N, 1.0, 0.0, 2.0, unit="EV", desc="Largest automatic exposure change."),
             "exposure": Param(N, 0.0, -2.0, 2.0, unit="EV", desc="Manual face exposure (added to the automatic one)."),
             "warmth": Param(N, 0.0, -100.0, 100.0, desc="Skin warmth: + warmer, - cooler."),
             "tint": Param(N, 0.0, -100.0, 100.0, desc="Skin tint: + magenta, - green."),
             "saturation": Param(N, 1.0, 0.0, 2.0, desc="Skin saturation (1 = unchanged)."),
             "skin_only": Param(B, True, desc="Colour changes only on skin (not beard, eyes, hair)."),
             **_FACE_COMMON}))
_reg(OpSpec("face_relighting", "Face Relighting", "Face", STAGE_FACE, "effects",
            "2D virtual lighting on tracked faces: key light from a chosen side, fill that lifts shadows, optional shadow "
            "deepening. Image-space approximation (no 3D face model), so it suits gentle corrections.",
            {"key_angle": Param(N, -45.0, -90.0, 90.0, unit="deg", desc="Key light side: -90 frame-left, 0 front, +90 frame-right."),
             "key_elevation": Param(N, 20.0, 0.0, 60.0, unit="deg", desc="Key light height (raises the bright side toward the forehead)."),
             "key_intensity": Param(N, 0.5, 0.0, 1.0, desc="Key light strength (up to +1.5 EV on the lit side)."),
             "fill": Param(N, 0.3, 0.0, 1.0, desc="Lifts dark areas of the face (up to +1 EV)."),
             "shadow_depth": Param(N, 0.0, 0.0, 1.0, desc="Darkens the side away from the key for more contrast."),
             **_FACE_COMMON}))

_reg(OpSpec("ai_upscale", "AI Resolution Enhancement", "Enhance", STAGE_UPSCALE, "video",
            "Real-ESRGAN super-resolution (x4 network, BSD-3) used instead of the normal scaler when the output is larger than "
            "the source. Runs on CPU or GPU (ONNX Runtime). It reconstructs plausible detail; it cannot recover detail that "
            "was never recorded, and on CPU it is slow (~0.5-3 s per frame).",
            {"model": Param(E, "general", enum=("general", "quality", "quality_x2", "anime"),
                            desc="general: fast real-world model (CPU friendly). quality: Real-ESRGAN x4plus, finest detail, ~12x slower (GPU recommended). "
                                 "quality_x2: native 2x x2plus model, best for 1.5-2x jobs. anime: cartoons/anime/flat graphics."),
             "grain": Param(N, 0.0, 0.0, 1.0, desc="Re-add the source's own fine grain/noise so the result looks camera-made rather than painted."),
             "quality": Param(E, "fast", enum=("fast", "best"), desc="fast: when the output is at most 2x the source, the network runs on a half-size frame (about 4x faster). best: always full size."),
             "denoise": Param(N, 0.5, 0.0, 1.0, desc="[general model] 0 keeps all texture (and noise); 1 removes noise strongly (smoother)."),
             "strength": Param(N, 1.0, 0.0, 1.0, desc="Blend with a classical Lanczos upscale; lower looks more natural."),
             "temporal_stability": Param(N, 0.6, 0.0, 1.0, desc="Motion-compensated smoothing of the AI detail between frames (removes shimmer). 0 = per-frame."),
             "color_fidelity": Param(B, True, desc="Keep the original's colour/exposure/contrast exactly; the AI only adds fine detail."),
             "face_detail": Param(N, 0.8, 0.0, 1.0, desc="Amount of AI detail on faces (1 = same as elsewhere). Lower avoids plastic skin and redrawn features.")}))
_reg(OpSpec("hair_edge_refinement", "Hair & Edge Refinement (matting)", "Keying", STAGE_KEY, "effects",
            "Hair & Edge Refinement (matting)", supported=False,
            unsupported_reason="Not a separate operation: use background_removal with mode 'screen' (green/blue screen) or 'ai' (no screen). "
                               "Both produce hair-level partial transparency, solve the subject colour out of edge pixels and clean the matte; "
                               "edge_shrink, edge_soften and temporal_smooth fine-tune it."))


# ---------------------------------------------------------------------------------------------------------------------
# Keyframing. A parameter is animatable ONLY if its animated render was verified bit-exact against static renders
# (see backend/tests/test_animation.py). Everything else says why it is not.
_ANIMATABLE: dict[str, tuple[str, ...]] = {
    "color_grading": ("exposure", "contrast", "brightness", "highlights", "shadows", "whites", "blacks", "temperature", "tint",
                      "saturation", "vibrance", "gamma"),
    "chroma_key": ("similarity", "blend", "spill_strength"),
    "background_removal": ("similarity", "blend", "spill_strength"),
    "background_replacement": ("scale", "offset_x", "offset_y", "brightness", "saturation"),
    "text_overlay": ("x", "y", "font_size", "opacity", "box_opacity"),
    "audio_gain": ("gain_db",),
    "face_tone_correction": ("strength", "exposure", "target_brightness", "auto_strength", "max_auto_ev", "warmth", "tint", "saturation"),
    "face_relighting": ("key_angle", "key_elevation", "key_intensity", "fill", "shadow_depth"),
    "ai_upscale": ("strength", "grain", "face_detail", "temporal_stability"),
}
_REASONS: dict[tuple[str, str], str] = {
    ("background_replacement", "blur"): "FFmpeg's Gaussian blur cannot change its radius per frame reliably (measured: it renders a different blur than requested)",
    ("video_enhancement", "denoise"): "the denoiser keeps temporal state and its strength cannot be changed per frame exactly",
    ("video_enhancement", "sharpen"): "the sharpen filter has no per-frame control",
    ("color_grading", "texture"): "the unsharp filter has no per-frame control",
    ("color_grading", "clarity"): "the unsharp filter has no per-frame control",
    ("color_grading", "dehaze"): "it is built from curves and eq values that are fixed per render in this version",
    ("color_grading", "amount"): "the master amount is a mix of the whole effect with the original; it is not animatable in this version",
    ("studio_look", "amount"): "the master amount is a mix of the whole effect with the original; it is not animatable in this version",
    ("background_removal", "amount"): "the master amount is a mix of the whole effect with the original; it is not animatable in this version",
    ("chroma_key", "amount"): "the master amount is a mix of the whole effect with the original; it is not animatable in this version",
    ("color_grading", "lut_intensity"): "per-frame LUT blending is not frame-exact in FFmpeg",
}
for _t, _names in _ANIMATABLE.items():
    for _n in _names:
        OPERATIONS[_t].params[_n] = replace(OPERATIONS[_t].params[_n], animatable=True)
for (_t, _n), _why in _REASONS.items():
    OPERATIONS[_t].params[_n] = replace(OPERATIONS[_t].params[_n], static_reason=_why)


def json_schema() -> dict:
    """Machine readable operation catalogue (served to the UI and docs)."""
    return {"blueprint_versions": ["1.0"], "operations": {k: v.to_json() for k, v in OPERATIONS.items()}}
