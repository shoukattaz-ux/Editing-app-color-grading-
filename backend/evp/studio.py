"""Studio Look: presets and parameter resolution (shared by the compiler and the docs)."""
from __future__ import annotations

# Every control below can be overridden in the blueprint; whatever a blueprint leaves out comes from its preset.
PRESETS: dict[str, dict] = {
    "studio_warm": dict(ambient_match=0.5, light_wrap=0.5, wrap_width=14, depth=0.3, contrast=0.35, shadow_tint="#1b3a66", highlight_tint="#ffb870",
                        tint_amount=0.45, glow=0.30, glow_threshold=0.65, glow_radius=14, vignette=0.35, clarity=0.30, grain=0.08, saturation=1.08),
    "studio_cool": dict(ambient_match=0.55, light_wrap=0.6, wrap_width=16, depth=0.35, contrast=0.40, shadow_tint="#0a2a7a", highlight_tint="#8fb8ff",
                        tint_amount=0.5, glow=0.35, glow_threshold=0.6, glow_radius=16, vignette=0.40, clarity=0.30, grain=0.08, saturation=1.10),
    "cinematic": dict(ambient_match=0.5, light_wrap=0.5, wrap_width=14, depth=0.35, contrast=0.45, shadow_tint="#0f4c5c", highlight_tint="#ff9f5a",
                      tint_amount=0.55, glow=0.20, glow_threshold=0.7, glow_radius=12, vignette=0.45, clarity=0.20, grain=0.12, saturation=1.05),
    "clean_bright": dict(ambient_match=0.35, light_wrap=0.35, wrap_width=10, depth=0.2, contrast=0.20, shadow_tint="#41506b", highlight_tint="#fff1de",
                         tint_amount=0.15, glow=0.15, glow_threshold=0.75, glow_radius=10, vignette=0.15, clarity=0.25, grain=0.04, saturation=1.05),
}
PRESET_NAMES = ("none", *PRESETS)
NEUTRAL = dict(ambient_match=0.0, light_wrap=0.0, wrap_width=12, depth=0.0, contrast=0.0, shadow_tint="#000000", highlight_tint="#ffffff",
               tint_amount=0.0, glow=0.0, glow_threshold=0.7, glow_radius=12, vignette=0.0, clarity=0.0, grain=0.0, saturation=1.0)
CONTROLS = tuple(NEUTRAL)


def resolve(params: dict) -> dict:
    """preset values, overridden by whatever the blueprint set explicitly"""
    base = dict(NEUTRAL if params.get("preset", "studio_warm") == "none" else PRESETS[params.get("preset", "studio_warm")])
    base.update({k: params[k] for k in CONTROLS if params.get(k) is not None})
    return base


def tint_vector(hex_colour: str, amount: float, gain: float = 0.3) -> tuple[float, float, float]:
    """colour -> (r,g,b) shift for FFmpeg's colorbalance: the colour's hue direction scaled by `amount` (its brightness is ignored)"""
    c = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    m = sum(c) / 3
    d = [x - m for x in c]
    peak = max(abs(x) for x in d) or 1.0
    return tuple(round(gain * amount * x / peak, 4) for x in d)


def match_curves(frame_rgb, ref_rgb, strength: float, points: int = 17, max_shift: float = 0.25):
    """Per-channel histogram specification: curves that move `frame_rgb`'s tonal distribution toward `ref_rgb`'s.
    Inputs are HxWx3 uint8 RGB arrays (different sizes are fine). Returns three lists of (x, y) control points in 0..1.
    The correction is capped at `max_shift` and scaled by `strength`, so content differences between the two pictures cannot wreck the image."""
    import numpy as np
    out = []
    xs = np.linspace(0.0, 1.0, points)
    for ch in range(3):
        a = np.bincount(frame_rgb[..., ch].ravel(), minlength=256).astype(float)
        b = np.bincount(ref_rgb[..., ch].ravel(), minlength=256).astype(float)
        ca, cb = np.cumsum(a) / a.sum(), np.cumsum(b) / b.sum()
        lut = np.interp(ca, cb, np.arange(256) / 255.0)                     # frame level -> reference level with the same rank
        k = np.ones(9) / 9
        lut = np.convolve(np.pad(lut, 4, mode="edge"), k, mode="valid")        # smooth: histograms are noisy
        y = np.interp(xs, np.arange(256) / 255.0, lut)
        y = xs + np.clip(y - xs, -max_shift, max_shift) * strength
        y[0], y[-1] = max(0.0, min(0.15, y[0])), 1.0 if strength == 0 else min(1.0, max(0.85, y[-1]))
        y = np.maximum.accumulate(np.clip(y, 0, 1))                          # monotonic
        out.append([(round(float(x), 4), round(float(v), 4)) for x, v in zip(xs, y)])
    return out
