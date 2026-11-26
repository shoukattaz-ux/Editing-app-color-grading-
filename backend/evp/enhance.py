"""Auto enhance: one master slider for a flat, hazy, colour-cast clip.

A few frames of the clip are measured the way a colourist reads a shot - how milky the blacks are (haze), the colour cast of things that
should be neutral (white shirts, walls, sky), exposure, contrast and colourfulness - and turned into ordinary Color Grading settings. The
master `auto_enhance` slider scales all of them together (0 = off, 1 = the measured correction, 1.5 = pushed further); the user's own
sliders in the same Color Grading add on top. Only standard grading controls are used, so everything stays visible and editable.
"""
from __future__ import annotations
import math
import numpy as np

STYLES = ("natural", "crisp", "vivid")


def analyze(frames: list[np.ndarray], masks: list[np.ndarray] | None = None) -> dict:
    """frames: RGB uint8 (any size); masks (optional, bool HxW): measure only those pixels (the subject, or the background).
    Returns plain numbers (JSON) describing the shot."""
    import cv2
    st = {k: [] for k in ("p1", "p5", "p50", "p95", "p99", "clip", "haze", "r", "g", "b", "sat", "neutral_share", "white")}
    for i, fr in enumerate(frames):
        f = fr.astype(np.float32) / 255.0
        sel = masks[i] if masks is not None else None
        if sel is not None and sel.sum() < 200: continue                       # (the part is not in this frame)
        if sel is not None:
            dark_all = cv2.erode(f.min(2), np.ones((7, 7), np.uint8))
            f = f[sel][None, :, :]                                            # a 1 x N "picture" of only the measured pixels
            dark_sel = dark_all[sel][None, :]
        y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
        for k, q in (("p1", 1), ("p5", 5), ("p50", 50), ("p95", 95), ("p99", 99)): st[k].append(float(np.percentile(y, q)))
        st["clip"].append(float((y > 0.97).mean()))
        dark = dark_sel if sel is not None else cv2.erode(f.min(2), np.ones((7, 7), np.uint8))   # "dark channel": a clear picture has very dark pixels nearly everywhere
        st["haze"].append(float(np.percentile(dark, 25)))
        mx, mn = f.max(2), f.min(2)
        chroma = (mx - mn) / np.maximum(mx, 1e-3)
        st["sat"].append(float(chroma.mean()))
        neutral = (chroma < 0.18) & (y > 0.35) & (y < 0.96)                   # should-be-neutral things: white shirt, walls, overcast sky
        share = float(neutral.mean())
        src = f[neutral] if share > 0.02 else f.reshape(-1, 3)                 # too few: fall back to the whole picture (grey world)
        m = src.mean(0)
        for i, k in enumerate("rgb"): st[k].append(float(m[i]))
        st["neutral_share"].append(share)
        st["white"].append(float(np.percentile(y[neutral], 90)) if share > 0.02 else float(np.percentile(y, 97)))    # how white the whites are
    if not st["p50"]: raise ValueError("nothing to measure")
    return {k: float(np.median(v)) for k, v in st.items()}


def corrections(m: dict, style: str = "crisp") -> dict:
    """the full-strength (slider = 100 %) Color Grading changes for a measured shot"""
    style = style if style in STYLES else "crisp"
    c: dict = {}
    # haze / milky blacks
    haze = float(np.clip((m["haze"] - 0.06) / 0.22, 0, 1)) * 0.6 + float(np.clip((m["p1"] - 0.02) / 0.15, 0, 1)) * 0.4
    c["dehaze"] = round(float(np.clip(haze * {"natural": 0.45, "crisp": 0.6, "vivid": 0.6}[style], 0, 0.6)), 3)
    c["blacks"] = round(-float(np.clip((m["p1"] - 0.02) * 3.0, 0, 0.6)) - {"natural": 0.0, "crisp": 0.15, "vivid": 0.12}[style], 3)      # crisp: deep, clean darks (hair, beard)
    c["highlights"] = round(-float(np.clip(m["clip"] * 4.0, 0, 0.45)), 3)
    # exposure: dim footage is brightened toward a bright, daylight middle; only really over-exposed footage is darkened
    target = {"natural": 0.6, "crisp": 0.66, "vivid": 0.65}[style]
    if m["p50"] < target: ev = math.log2(target / max(m["p50"], 0.02))
    elif m["p50"] > 0.74: ev = math.log2(0.7 / m["p50"])
    else: ev = 0.0
    c["exposure"] = round(float(np.clip(ev, -0.6, 0.6)), 3)
    # whites: what should be white (shirt, wall, sky) is lifted toward clean white
    sure = 1.0 if m.get("neutral_share", 0.0) >= 0.02 else 0.5                  # few white / grey things to judge by: be careful (no blown highlights)
    c["whites"] = round(float(np.clip((0.93 - m.get("white", m["p99"])) * 2.2, 0, 0.45)) * sure, 3)
    # contrast for a flat shot
    spread = m["p95"] - m["p5"]
    c["contrast"] = round(1.0 + float(np.clip((0.78 - spread) * 0.45, 0, 0.22)) + {"natural": 0.0, "crisp": 0.04, "vivid": 0.06}[style], 4)
    # colour cast of the neutral things -> temperature / tint (the grade's mixer: r*(1+0.2T), b*(1-0.2T), g*(1-0.2I))
    r, g, b = max(m["r"], 1e-3), max(m["g"], 1e-3), max(m["b"], 1e-3)
    keep = {"natural": 0.6, "crisp": 0.8, "vivid": 0.7}[style]                  # how much of the cast is removed (not all: a scene keeps its mood)
    temp = 100 * (b - r) / (0.2 * (r + b)) * keep + {"natural": -5, "crisp": -24, "vivid": -15}[style]      # crisp = a cool, clear-day white
    tint = 100 * (1 - (r + b) / (2 * g)) / 0.2 * keep + {"natural": 3, "crisp": 15, "vivid": 10}[style]      # cooling lowers red only: balance the green it leaves
    c["temperature"] = round(float(np.clip(temp, -40, 40)), 2)
    c["tint"] = round(float(np.clip(tint, -30, 30)), 2)
    # colourfulness and detail
    c["vibrance"] = round(float(np.clip((0.32 - m["sat"]) * 1.2, 0, 0.45)) + {"natural": 0.0, "crisp": 0.0, "vivid": 0.12}[style], 3)      # (vibrance mostly reddens skin: kept low)
    c["saturation"] = round({"natural": 1.0, "crisp": 1.04, "vivid": 1.1}[style], 3)
    c["clarity"] = {"natural": 0.12, "crisp": 0.3, "vivid": 0.32}[style]
    c["texture"] = {"natural": 0.1, "crisp": 0.3, "vivid": 0.25}[style]
    return c


MULT = ("contrast", "saturation")                                              # factors (1 = unchanged); everything else adds


def apply(params: dict, measured: dict | None) -> dict:
    """the Color Grading parameters actually used: the user's own values + master amount x the measured corrections"""
    k = float(params.get("auto_enhance", 0.0) or 0.0)
    if k <= 0 or not measured: return params
    c = corrections(measured, params.get("enhance_style") or "crisp")
    out = dict(params)
    lim = {"exposure": (-3, 3), "contrast": (0, 3), "highlights": (-1, 1), "whites": (-1, 1), "blacks": (-1, 1), "temperature": (-100, 100),
           "tint": (-100, 100), "saturation": (0, 3), "vibrance": (-2, 2), "texture": (-1, 1), "clarity": (-1, 1), "dehaze": (-1, 1)}
    for key, v in c.items():
        base = float(out.get(key) if out.get(key) is not None else (1.0 if key in MULT else 0.0))
        new = base * (1 + k * (v - 1)) if key in MULT else base + k * v
        lo, hi = lim[key]
        out[key] = float(np.clip(new, lo, hi))
    return out
