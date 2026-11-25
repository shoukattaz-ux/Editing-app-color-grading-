"""Floor reflection and contact shadow: what makes a person look like they stand ON the floor of the new scene.

Both effects are made from the keyed subject itself, so they follow every movement:
  * reflection      the subject flipped about the floor line, faded with distance, blurred, laid under the subject
  * contact shadow  the lowest slice of the silhouette (feet / shoes), squashed and blurred into a tight dark blob right at the floor line
The floor line is either set by hand (`floor_y`) or measured once: the lowest point of the subject's matte on the middle frame.
"""
from __future__ import annotations
import hashlib, os, uuid
from pathlib import Path
import numpy as np


def measure_bottom(alpha: np.ndarray) -> tuple[float, bool]:
    """alpha: HxW uint8/float matte of the (un-placed) subject. Returns (lowest solid row as a fraction of the height, touches the bottom of the frame)"""
    a = alpha.astype(np.float32)
    if a.max() > 1.5: a = a / 255.0
    h, w = a.shape
    rows = (a > 0.5).mean(1) > 0.004                        # a row counts when at least 0.4 % of it is subject (ignores a stray speck)
    if not rows.any(): return 0.0, False
    last = int(np.where(rows)[0].max())
    return (last + 1) / h, last >= h - 2


def placed_y(frac: float, p: dict) -> float:
    """a height measured on the un-placed subject -> the same point after subject_scale / subject_y (mirrors the compiler's place_subject)"""
    s, dy = float(p.get("subject_scale", 1.0)), float(p.get("subject_y", 0.0))
    top = (1.0 - s) if p.get("subject_anchor", "bottom") == "bottom" else (1.0 - s) / 2.0
    return top + frac * s + dy


def reflection_gradient(H: int, W: int, floor_px: int, length_px: float, opacity: float) -> np.ndarray:
    """HxW uint8 multiplier: 0 above the floor, then opacity fading to 0 over `length_px` (ease-out, so the reflection is strongest at the feet)"""
    y = np.arange(H, dtype=np.float32)
    g = np.where(y < floor_px, 0.0, np.power(np.clip(1.0 - (y - floor_px) / max(1.0, length_px), 0.0, 1.0), 1.4)) * opacity
    return np.repeat((np.clip(g, 0, 1) * 255 + 0.5).astype(np.uint8)[:, None], W, 1)


def band_mask(H: int, W: int, floor_px: int, band_px: int) -> np.ndarray:
    """HxW uint8: 255 on the rows just above the floor line (the footprint), 0 elsewhere"""
    m = np.zeros((H, W), np.uint8)
    m[max(0, floor_px - band_px):max(0, min(H, floor_px + 1))] = 255
    return m


def write_gray(arr: np.ndarray, work_dir: str, tag: str) -> str:
    import cv2
    out = Path(work_dir) / f"{tag}_{hashlib.sha1(arr.tobytes()[:4096] + repr(arr.shape).encode()).hexdigest()[:8]}_{uuid.uuid4().hex[:6]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), arr)
    return str(out)
