"""Garbage / keep masks: hand-drawn shapes that correct a key.

garbage = this area is always removed (lights, stands, the edge of the screen, anything that is not the person);
keep    = this area is always kept (a face or shirt the key made see-through).
A mask is a gray picture the size of the frame (0..255); the compiler mixes it into the keyer's matte. Shapes are stored as fractions of the
frame (0..1), so they stay valid at any resolution; the soft edge (feather) is in pixels at 1080p and scales with the frame.
"""
from __future__ import annotations
import hashlib, os, uuid
from pathlib import Path
import numpy as np


def render_mask(p: dict, W: int, H: int) -> np.ndarray:
    import cv2
    img = np.zeros((H, W), np.uint8)
    shape = p.get("shape", "rect")
    if shape == "polygon":
        pts = p.get("polygon") or []
        if len(pts) >= 3:
            cv2.fillPoly(img, [np.array([[x * (W - 1), y * (H - 1)] for x, y in pts], np.int32)], 255)
    else:
        x0, y0 = p.get("x", 0.0) * W, p.get("y", 0.0) * H
        x1, y1 = x0 + p.get("width", 0.15) * W, y0 + p.get("height", 1.0) * H
        if shape == "ellipse":
            cv2.ellipse(img, (int(round((x0 + x1) / 2)), int(round((y0 + y1) / 2))), (max(1, int(round((x1 - x0) / 2))), max(1, int(round((y1 - y0) / 2)))), 0, 0, 360, 255, -1)
        else:
            cv2.rectangle(img, (int(round(x0)), int(round(y0))), (max(int(round(x0)), int(round(x1)) - 1), max(int(round(y0)), int(round(y1)) - 1)), 255, -1)
    sigma = float(p.get("feather", 0.0)) * (H / 1080.0) / 2.0
    if sigma > 0.3: img = cv2.GaussianBlur(img, (0, 0), sigma)
    if p.get("invert"): img = 255 - img
    return img


def write_mask(p: dict, W: int, H: int, work_dir: str) -> str:
    import cv2
    key = hashlib.sha1(repr(sorted((k, repr(v)) for k, v in p.items() if k in ("shape", "x", "y", "width", "height", "polygon", "feather", "invert")) + [W, H]).encode()).hexdigest()[:12]
    out = Path(work_dir) / f"mask_{key}_{uuid.uuid4().hex[:6]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), render_mask(p, W, H))
    return str(out)
