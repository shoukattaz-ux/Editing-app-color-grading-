"""Hair detail: let the AI matting model refine ONLY a band around the subject's edge, where fine hair is, and keep the screen key everywhere else.

The screen key is physically exact on a clean screen but can lose strands where hair is dark against a shadowed screen, or turn green-tinted hair
see-through; a neural matting model knows what hair is, but is softer and less exact on solid edges. The hybrid keeps the best of both:
  * the AI can only ADD alpha, never remove or soften what the key found (tested on real green-screen footage: moving the matte TOWARDS the AI
    replaced crisp curls with a soft halo, so that design was dropped);
  * only the AI's confident part counts, only where the key already saw something (a pure-screen pixel stays background: the AI's halo cannot
    come in), and only in a band across the silhouette edge (`width` px at 1080p);
  * safety: if the AI does not find the same person (its silhouette disagrees with the key's), that frame uses the plain key.
On clean footage the result is therefore almost identical to the plain key; it matters where the key drops hair (dark / green-tinted strands).
"""
from __future__ import annotations
import numpy as np
import cv2

AGREE_IOU = 0.6          # AI and key must overlap at least this much (in/near the subject) for the AI to be trusted on a frame


def band_mask(a_key: np.ndarray, width_px: float, H: int) -> np.ndarray:
    """0..1 weight: 1 across the edge band (outside and a little inside the silhouette), soft at both ends"""
    r = max(2, int(round(width_px * H / 1080.0)))
    sil = (a_key > 0.5).astype(np.uint8)
    outer = cv2.dilate(sil, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    ri = max(1, r // 3)
    inner = cv2.erode(sil, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ri + 1, 2 * ri + 1)))
    band = (outer.astype(bool) & ~inner.astype(bool)).astype(np.float32)
    return np.clip(cv2.GaussianBlur(band, (0, 0), max(1.0, r / 4.0)) * 1.15, 0.0, 1.0)


def agrees(a_key: np.ndarray, a_ai: np.ndarray, width_px: float, H: int) -> bool:
    r = max(2, int(round(width_px * H / 1080.0)))
    k = cv2.dilate((a_key > 0.5).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))).astype(bool)
    ai = (a_ai > 0.5)
    if k.sum() < 50: return False
    inter = float((k & ai).sum()); ai_in = float(ai.sum())
    kc = (a_key > 0.5).sum()
    # the AI's person must lie (almost) inside the dilated key silhouette, and cover most of the key's silhouette
    return (inter / max(1.0, ai_in) >= AGREE_IOU) and (float(((a_key > 0.5) & ai).sum()) / max(1.0, float(kc)) >= AGREE_IOU)


def blend_hair(a_key: np.ndarray, fg_key: np.ndarray | None, a_ai: np.ndarray, fg_ai: np.ndarray | None, strength: float, width_px: float, screen_channel: int | None = None):
    """returns (alpha, fg, used).  The AI can only ADD: it restores what the key lost, it never removes or softens what the key found."""
    if strength <= 0: return a_key, fg_key, False
    H = a_key.shape[0]
    if a_ai.shape != a_key.shape: a_ai = cv2.resize(a_ai, (a_key.shape[1], H), interpolation=cv2.INTER_LINEAR)
    if not agrees(a_key, a_ai, width_px, H): return a_key, fg_key, False
    # The AI's matte is wider and softer than a good key (a halo around every strand), so only its CONFIDENT part counts (alpha 0.4 -> 0, 0.9 -> 1),
    # only where the key itself already saw something (alpha > 0.02: a pure-screen pixel stays background, so the halo can never come in), and only
    # inside the edge band. Where the key is already higher it simply stays: crisp curls stay crisp.
    gate = np.clip((a_ai - 0.4) / 0.5, 0.0, 1.0)
    add = gate * band_mask(a_key, width_px, H) * float(strength) * (a_key > 0.02)
    a = np.maximum(a_key, add).astype(np.float32)
    fg = fg_key
    if fg_key is not None and fg_ai is not None:
        if fg_ai.shape[:2] != fg_key.shape[:2]: fg_ai = cv2.resize(fg_ai, (fg_key.shape[1], H), interpolation=cv2.INTER_LINEAR)
        t = np.clip((a - a_key) * 4.0, 0.0, 1.0)[..., None]                  # restored pixels take the AI's foreground colour (the key had solved theirs at a tiny alpha)
        fg = np.clip(fg_key + (fg_ai - fg_key) * t, 0.0, 1.0).astype(np.float32)
        if screen_channel is not None:                                       # restored strands sit right next to the screen: take the screen's spill out of them
            o1, o2 = [i for i in range(3) if i != screen_channel]
            cap = 0.85 * np.maximum(fg[..., o1], fg[..., o2])
            fg[..., screen_channel] = np.where(t[..., 0] > 0, np.minimum(fg[..., screen_channel], cap), fg[..., screen_channel])
    return a, fg, True
