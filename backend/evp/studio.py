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


AUTO_KEYS = CONTROLS + ("subject_exposure", "shadow", "shadow_blur", "shadow_dx", "shadow_dy")
NUMERIC_AUTO = tuple(k for k in AUTO_KEYS if k not in ("shadow_tint", "highlight_tint"))


def resolve(params: dict, auto: dict | None = None) -> dict:
    """preset values, then the auto-measured values (blended in by `auto_match`), then whatever the blueprint set explicitly"""
    base = dict(NEUTRAL if params.get("preset", "studio_warm") == "none" else PRESETS[params.get("preset", "studio_warm")])
    base.setdefault("subject_exposure", 0.0)
    a = float(params.get("auto_match") or 0.0)
    if auto and a > 0:
        for k, v in auto.items():
            if k in ("shadow_tint", "highlight_tint"):
                if a >= 0.5: base[k] = v
            elif k in NUMERIC_AUTO and isinstance(v, (int, float)):
                base[k] = base.get(k, 0.0) + a * (v - base.get(k, 0.0))
            elif k in AUTO_KEYS: base[k] = v
    base.update({k: params[k] for k in AUTO_KEYS if params.get(k) is not None})
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


def _luma(rgb):
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(min(255, max(0, v)))) for v in c)


def analyze(comp_rgb, subject_mask) -> dict:
    """Measure the finished composite (no look applied yet) and derive studio-look settings the way a colourist reads a scene.

    comp_rgb: HxWx3 uint8; subject_mask: HxW bool (the keyed person). Returns {control: value} plus a `measured` dict for the user.
      * light colour / shadow colour  <- the background's brightest 10% and darkest 25% pixels  -> highlight_tint / shadow_tint
      * key-light side                <- where the background's luminance is concentrated      -> shadow offset (falls away from the light)
      * contrast, vignette, glow      <- background's tonal range, overall darkness, share of blown highlights
      * saturation / ambient / tints  <- how colourful the background is
      * depth                         <- background sharpness (an already soft background needs less extra blur)
      * subject_exposure              <- subject highlights (face) vs background upper tones: lifted/lowered only outside a natural band
      * light_wrap                    <- brightness just behind the subject's edge
    Strengths are deliberately conservative (skin tones must stay natural); raise them by hand if you want a stronger look.
    """
    import numpy as np, cv2
    h, w = comp_rgb.shape[:2]
    m = subject_mask.astype(np.uint8)
    has_subject = m.mean() > 0.02
    bg = ~cv2.dilate(m, np.ones((15, 15), np.uint8)).astype(bool) if has_subject else np.ones((h, w), bool)
    subj = cv2.erode(m, np.ones((9, 9), np.uint8)).astype(bool) if has_subject else np.zeros((h, w), bool)
    if bg.sum() < 500: bg = np.ones((h, w), bool)
    L = _luma(comp_rgb)
    lb = L[bg]
    p5, p25, p75, p95, p98 = np.percentile(lb, [5, 25, 75, 95, 98])
    px = comp_rgb[bg].astype(float)
    hi = px[lb >= np.percentile(lb, 90)].mean(0)
    sh = px[lb <= p25].mean(0)
    mx, mn = px.max(1), px.min(1)
    sat = float(np.mean((mx - mn) / np.maximum(mx, 1)))
    # where the light is: luminance centroid of the background (person filled with the background mean so they don't pull it)
    Lf = np.where(bg, L, lb.mean())
    cols, rows = Lf.mean(0), Lf.mean(1)
    gx = float((np.arange(w) @ cols) / cols.sum() / (w / 2) - 1.0)
    gy = float((np.arange(h) @ rows) / rows.sum() / (h / 2) - 1.0)
    lap = cv2.Laplacian(cv2.cvtColor(comp_rgb, cv2.COLOR_RGB2GRAY), cv2.CV_64F)[bg].var()
    sharp = float(np.clip(lap / 400.0, 0, 1))
    blown = float((lb > 0.9).mean())
    lbm = float(lb.mean())
    out = dict(
        contrast=round(float(np.clip(0.10 + 0.25 * (p95 - p5), 0.1, 0.35)), 3),
        saturation=round(float(np.clip(1.0 + 0.25 * (sat - 0.25), 0.95, 1.12)), 3),
        ambient_match=round(float(np.clip(0.06 + 0.2 * sat, 0.06, 0.18)), 3),
        tint_amount=round(float(np.clip(0.05 + 0.2 * sat, 0.05, 0.15)), 3),
        shadow_tint=_hex(sh), highlight_tint=_hex(hi),
        glow=round(float(np.clip(0.08 + 6 * blown, 0.08, 0.4)), 3),
        glow_threshold=round(float(np.clip(p98 - 0.05, 0.6, 0.92)), 3),
        vignette=round(float(np.clip(0.06 + 0.2 * (1 - lbm), 0.06, 0.22)), 3),
        depth=round(float(0.08 + 0.27 * sharp), 3),
        clarity=0.25, grain=0.06,
    )
    measured = dict(background_brightness=round(lbm, 3), background_contrast=round(float(p95 - p5), 3), background_saturation=round(sat, 3),
                    light_side_x=round(gx, 2), light_side_y=round(gy, 2), background_sharpness=round(sharp, 2), highlights_blown=round(blown, 3))
    if has_subject and subj.sum() > 500:
        ring = cv2.dilate(m, np.ones((41, 41), np.uint8)).astype(bool) & ~cv2.dilate(m, np.ones((9, 9), np.uint8)).astype(bool) & bg
        lr = float(L[ring].mean()) if ring.sum() > 100 else lbm
        ls = float(np.percentile(L[subj], 90))
        ratio = ls / max(1e-3, float(p75))
        ev = 0.0
        if ratio < 0.6: ev = 0.6 * np.log2(0.6 / ratio)
        elif ratio > 1.6: ev = -0.6 * np.log2(ratio / 1.6)
        sgn = 1.0 if gx < 0 else -1.0                      # light on the left -> shadow falls to the right
        out.update(subject_exposure=round(float(np.clip(ev, -0.6, 0.6)), 3),
                   light_wrap=round(float(np.clip(0.12 + 0.5 * lr, 0.15, 0.4)), 3), wrap_width=12,
                   shadow=round(float(np.clip(0.12 + 0.2 * lbm, 0.12, 0.3)), 3), shadow_blur=30.0,
                   shadow_dx=round(sgn * float(np.clip(0.01 + 0.05 * abs(gx), 0.01, 0.04)), 3), shadow_dy=0.015)
        measured.update(subject_highlight_vs_background=round(ratio, 2), edge_brightness=round(lr, 3))
    out["measured"] = measured
    return out


def skin_shift(before_rgb, after_rgb, subject_mask):
    """How far did the look move skin tones? Returns (hue shift in degrees, chroma ratio after/before) or None if no skin is found.
    Skin = subject pixels whose CbCr in `before` falls in the usual skin range (Cb 77-127, Cr 133-173)."""
    import numpy as np, cv2
    ycc = cv2.cvtColor(np.ascontiguousarray(before_rgb), cv2.COLOR_RGB2YCrCb).astype(float)
    sel = subject_mask & (ycc[..., 2] >= 77) & (ycc[..., 2] <= 127) & (ycc[..., 1] >= 133) & (ycc[..., 1] <= 173) & (ycc[..., 0] > 40)
    if sel.sum() < 400: return None
    a = cv2.cvtColor(np.ascontiguousarray(after_rgb), cv2.COLOR_RGB2YCrCb).astype(float)
    def hc(y):
        cb, cr = y[..., 2][sel].mean() - 128, y[..., 1][sel].mean() - 128
        return np.degrees(np.arctan2(cr, cb)), float(np.hypot(cb, cr))
    (h0, c0), (h1, c1) = hc(ycc), hc(a)
    return float(abs((h1 - h0 + 180) % 360 - 180)), c1 / max(c0, 1e-3)
