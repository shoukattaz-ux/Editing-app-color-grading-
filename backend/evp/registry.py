"""Operation registry: the ONLY operations a blueprint may invoke.

Every operation declares its parameters (type, default, limits). The validator,
the compiler, the properties panel in the UI and the published JSON schema are
all generated from this single source of truth.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

# Pipeline stages: lower runs earlier in the filter graph.
STAGE_UPSCALE = 5       # AI super-resolution replaces the base scaler
STAGE_RESTORE = 10      # stabilise / denoise
STAGE_KEY = 20          # produce an alpha layer from the footage
STAGE_COMPOSITE = 30    # place keyed subject over a new background
STAGE_FACE = 35         # face tone / relight (before the global grade, like a colourist's face pass)
STAGE_GRADE = 40
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

    def to_json(self) -> dict:
        d: dict[str, Any] = {"type": self.type, "description": self.desc}
        if self.default is not None: d["default"] = self.default
        if self.min is not None: d["minimum"] = self.min
        if self.max is not None: d["maximum"] = self.max
        if self.enum: d["enum"] = list(self.enum)
        if self.required: d["required"] = True
        if self.unit: d["unit"] = self.unit
        if self.asset_types: d["asset_types"] = list(self.asset_types)
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

_COLOR_PARAMS = {
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
    "lift": Param(PT, None, desc="[r,g,b] shadow colour offset, each -1..1 (Lift/Gamma/Gain)."),
    "gamma_rgb": Param(PT, None, desc="[r,g,b] midtone colour offset, each -1..1."),
    "gain": Param(PT, None, desc="[r,g,b] highlight colour offset, each -1..1."),
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
            {**_KEY_PARAMS, "background_color": Param(C, "#000000", desc="Fill used when no background_replacement consumes this key.")},
            provides_alpha=True))
_reg(OpSpec("background_removal", "Background Removal", "Keying", STAGE_KEY, "effects",
            "Remove the background. 'ai' works on ordinary footage (no green screen) using the Robust Video Matting model; "
            "'chroma' keys a green/blue screen.",
            {"mode": Param(E, "chroma", enum=("chroma", "ai"), desc="'ai' = person matting without green screen (downloads a 15 MB model on first use); 'chroma' = green/blue screen."),
             "model": Param(E, "rvm", enum=("rvm",), desc="[ai] Matting model. rvm = Robust Video Matting (GPL-3.0), hair-level detail, temporally stable."),
             "detail": Param(E, "fast", enum=("fast", "balanced", "high"), desc="[ai] Network resolution. fast is the most robust on busy/patterned backgrounds; balanced/high resolve finer hair on plain backgrounds but are slower and can pick up background texture."),
             "matte_threshold": Param(N, 0.03, 0.0, 0.5, desc="[ai] Alpha below this becomes fully transparent (removes faint background haze)."),
             "despeckle": Param(B, True, desc="[ai] Remove background blobs that are not connected to the person."),
             "edge_color_fix": Param(B, True, desc="[ai] Replace old-background colour in soft hair edges with the model's estimated foreground colour (removes halos)."),
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
             "saturation": Param(N, 1.0, 0.0, 3.0, desc="Background saturation to match the subject.")}))
_reg(OpSpec("color_grading", "Color Grading", "Color", STAGE_GRADE, "effects",
            "Exposure, contrast, tone, white balance, saturation, LGG, RGB curves, LUT, presets.", _COLOR_PARAMS))
_reg(OpSpec("video_enhancement", "Video Enhancement", "Enhance", STAGE_RESTORE, "effects",
            "Noise reduction, sharpening and basic stabilisation (classical algorithms; limited by source quality).",
            {"denoise": Param(N, 0.0, 0.0, 1.0, desc="hqdn3d strength."),
             "sharpen": Param(N, 0.0, 0.0, 2.0, desc="Unsharp-mask amount."),
             "stabilize": Param(B, False, desc="Single-pass deshake. Modest results; fully-featured stabilisation is not claimed."),
             "stabilize_strength": Param(I, 16, 1, 64, desc="Max shift in px searched by deshake.")}))
_reg(OpSpec("text_overlay", "Text Overlay", "Graphics", STAGE_OVERLAY, "overlay",
            "Draw static text.",
            {"text": Param(S, None, required=True, desc="Text to draw (max 500 chars)."),
             "font_size": Param(I, 48, 8, 400),
             "color": Param(C, "#ffffff"),
             "x": Param(N, 0.5, 0.0, 1.0, desc="Horizontal anchor (fraction, centred)."),
             "y": Param(N, 0.85, 0.0, 1.0, desc="Vertical anchor (fraction, centred)."),
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
            {"quality": Param(E, "fast", enum=("fast", "best"), desc="fast: when the output is at most 2x the source, the network runs on a half-size frame (about 4x faster). best: always full size."),
             "denoise": Param(N, 0.5, 0.0, 1.0, desc="0 keeps all texture (and noise); 1 removes noise strongly (smoother)."),
             "strength": Param(N, 1.0, 0.0, 1.0, desc="Blend with a classical Lanczos upscale; lower looks more natural."),
             "temporal_stability": Param(N, 0.6, 0.0, 1.0, desc="Motion-compensated smoothing of the AI detail between frames (removes shimmer). 0 = per-frame."),
             "color_fidelity": Param(B, True, desc="Keep the original's colour/exposure/contrast exactly; the AI only adds fine detail."),
             "face_detail": Param(N, 0.8, 0.0, 1.0, desc="Amount of AI detail on faces (1 = same as elsewhere). Lower avoids plastic skin and redrawn features.")}))
_reg(OpSpec("hair_edge_refinement", "Hair & Edge Refinement (matting)", "Keying", STAGE_KEY, "effects",
            "Hair & Edge Refinement (matting)", supported=False,
            unsupported_reason="Use background_removal with mode 'ai': its Robust Video Matting model already produces hair-level alpha. "
                               "A separate refinement pass for chroma keys is not implemented."))


def json_schema() -> dict:
    """Machine readable operation catalogue (served to the UI and docs)."""
    return {"blueprint_versions": ["1.0"], "operations": {k: v.to_json() for k, v in OPERATIONS.items()}}
