"""Face analysis -> per-frame 'control' video for face tone correction and 2D relighting.

Control video (lossless, RGB, analysis resolution, upscaled in the graph):
  R = tone weight  (where warmth/tint/saturation corrections apply: skin inside the face ellipse)
  G = brighten weight  (blend toward a x4 gain stream)       -> pixel gain 1 + 3*G
  B = darken weight    (blend toward a x0.25 gain stream)    -> pixel gain 1 - 0.75*B
The adjustment never changes face geometry or texture (no smoothing / reshaping): only per-pixel gain and colour.
"""
from __future__ import annotations
import hashlib, json, os, threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .. import edl as _edl
from .. import fsutil
from ..animate import op_params_at, keyframes_key
from .frames import decode, Writer
from .models import ensure_model

UP_GAIN, DOWN_GAIN = 4.0, 0.25
ANALYSIS_LONG_SIDE = 480


class Cancelled(Exception):
    pass


@dataclass
class ControlRef:
    path: str
    start_frame: int
    frames: int
    faces_found: int = 0          # frames with at least one face


@dataclass
class Track:
    box: np.ndarray               # x, y, w, h
    lm: np.ndarray                # 5x2 landmarks: eyes, nose, mouth corners
    score: float
    missed: int = 0
    skin_ref: np.ndarray | None = None   # adaptive (Cr, Cb) of this person's skin
    gain: float | None = None            # smoothed auto-exposure gain


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax2, bx2) - max(a[0], b[0])), max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / max(1e-6, a[2] * a[3] + b[2] * b[3] - inter)


class FaceTracker:
    def __init__(self, w: int, h: int, conf: float = 0.6, hold: int = 8, smooth: float = 0.5):
        self.det = cv2.FaceDetectorYN.create(str(ensure_model("yunet")), "", (w, h), conf, 0.3, 50)
        self.tracks: list[Track] = []
        self.hold, self.a = hold, smooth

    def update(self, bgr: np.ndarray) -> list[tuple[Track, float]]:
        """returns [(track, visibility 0..1)] - visibility fades out over `hold` frames after a miss."""
        _, dets = self.det.detect(bgr)
        dets = [] if dets is None else [d for d in dets if d[2] > 8 and d[3] > 8]
        used = set()
        for t in self.tracks:
            best, bi = 0.3, -1
            for i, d in enumerate(dets):
                if i in used: continue
                v = _iou(t.box, d[:4])
                if v > best: best, bi = v, i
            if bi >= 0:
                d = dets[bi]; used.add(bi)
                t.box = self.a * t.box + (1 - self.a) * d[:4]
                t.lm = self.a * t.lm + (1 - self.a) * d[4:14].reshape(5, 2)
                t.score, t.missed = float(d[14]), 0
            else:
                t.missed += 1
        for i, d in enumerate(dets):
            if i not in used:
                self.tracks.append(Track(d[:4].astype(np.float64), d[4:14].reshape(5, 2).astype(np.float64), float(d[14])))
        self.tracks = [t for t in self.tracks if t.missed <= self.hold]
        return [(t, 1.0 - t.missed / (self.hold + 1)) for t in self.tracks]


def face_ellipse(shape, t: Track, feather: float) -> tuple[np.ndarray, tuple]:
    """Soft ellipse covering forehead..chin, oriented by the eye line."""
    h, w = shape
    x, y, bw, bh = t.box
    cx, cy = x + bw / 2, y + bh * 0.47
    ax, ay = bw * 0.60, bh * 0.70
    (lx, ly), (rx, ry) = t.lm[0], t.lm[1]
    ang = float(np.degrees(np.arctan2(ry - ly, rx - lx)))
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (int(cx), int(cy)), (max(1, int(ax)), max(1, int(ay))), ang, 0, 360, 1.0, -1, cv2.LINE_AA)
    s = max(1.0, feather * bw * 0.25)
    m = cv2.GaussianBlur(m, (0, 0), s)
    return m, (cx, cy, ax, ay, ang)


def skin_weight(ycc: np.ndarray, t: Track, ell: np.ndarray, sigma: float = 11.0) -> np.ndarray:
    """Soft skin membership measured relative to this person's own cheek colour (works for any skin tone)."""
    x, y, bw, bh = t.box
    nose = t.lm[2]
    # sample the cheeks/nose bridge: a small box around the nose, between the eyes and the mouth
    x0, x1 = int(max(0, nose[0] - bw * 0.28)), int(min(ycc.shape[1], nose[0] + bw * 0.28))
    y0, y1 = int(max(0, t.lm[0][1] + bh * 0.05)), int(min(ycc.shape[0], nose[1] + bh * 0.05))
    if x1 - x0 > 2 and y1 - y0 > 2:
        ref = np.median(ycc[y0:y1, x0:x1, 1:].reshape(-1, 2), axis=0)
        t.skin_ref = ref if t.skin_ref is None else 0.9 * t.skin_ref + 0.1 * ref
    if t.skin_ref is None:
        return ell
    d2 = ((ycc[..., 1:].astype(np.float32) - t.skin_ref) ** 2).sum(-1)
    sk = np.exp(-d2 / (2 * sigma * sigma)).astype(np.float32)
    sk = cv2.GaussianBlur(sk, (0, 0), max(1.0, bw * 0.02))
    return sk * ell


def control_key(kind: str, source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, edl: list | None = None) -> str:
    st = os.stat(source)
    d = [kind, os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames,
         sorted(op.params.items()), keyframes_key(op), "v3", _edl.key(edl)]
    return hashlib.sha1(json.dumps(d, default=str).encode()).hexdigest()[:20]


def _weights_tone(rgb, faces, p, tr_cache) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h, w = rgb.shape[:2]
    R = np.zeros((h, w), np.float32); G = np.zeros_like(R); B = np.zeros_like(R)
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    strength = p["strength"]
    for t, vis in faces:
        ell, _ = face_ellipse((h, w), t, p["feather"])
        skin = skin_weight(ycc, t, ell)
        tone_m = (skin if p["skin_only"] else ell) * vis * strength
        R = np.maximum(R, tone_m)
        # exposure: manual EV + auto (median skin luma -> target), smoothed over time
        g = 2.0 ** p["exposure"]
        if p["auto_exposure"]:
            sel = skin > 0.5
            if sel.sum() > 30:
                lum = float(np.median(ycc[..., 0][sel])) / 255.0
                lim = 2.0 ** p["max_auto_ev"]
                a = float(np.clip((p["target_brightness"] / max(lum, 1e-3)) ** p["auto_strength"], 1 / lim, lim))
                t.gain = a if t.gain is None else 0.85 * t.gain + 0.15 * a
            g *= (t.gain or 1.0)
        exp_m = ell * vis * strength
        if g > 1: G = np.maximum(G, exp_m * min(1.0, (g - 1) / (UP_GAIN - 1)))
        elif g < 1: B = np.maximum(B, exp_m * min(1.0, (1 - g) / (1 - DOWN_GAIN)))
    return R, G, B


def _weights_relight(rgb, faces, p, tr_cache) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """2D relighting: key light gradient across the face from the chosen side, fill lifts shadows, optional shadow deepening."""
    h, w = rgb.shape[:2]
    R = np.zeros((h, w), np.float32); G = np.zeros_like(R); B = np.zeros_like(R)
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    # direction toward the light in screen space: key_angle -90 (frame left) .. +90 (frame right); elevation tilts it upward
    az, el = np.radians(p["key_angle"]), np.radians(p["key_elevation"])
    d = np.array([np.sin(az) * np.cos(el), -np.sin(el)])
    n = np.linalg.norm(d)
    d = d / n if n > 1e-6 else np.array([0.0, -1.0])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for t, vis in faces:
        ell, (cx, cy, ax, ay, _) = face_ellipse((h, w), t, p["feather"])
        u = ((xx - cx) / max(ax, 1)) * d[0] + ((yy - cy) / max(ay, 1)) * d[1]
        side = np.clip((u + 1) / 2, 0, 1)                     # 1 on the lit side, 0 on the far side
        key_ev = p["key_intensity"] * 1.5 * side ** 1.5
        fill_ev = p["fill"] * 1.0 * np.clip(1 - lum / 0.6, 0, 1) ** 1.5      # lift only darker areas
        ev = key_ev + fill_ev - p["shadow_depth"] * 1.0 * (1 - side) ** 1.5
        gain = 2.0 ** ev
        m = ell * vis
        G = np.maximum(G, m * np.clip((gain - 1) / (UP_GAIN - 1), 0, 1))
        B = np.maximum(B, m * np.clip((1 - gain) / (1 - DOWN_GAIN), 0, 1))
    return R, G, B


def _weights_retouch(rgb, faces, p, tr_cache) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """skin retouch: R = where the skin may be smoothed - the person's own skin colour inside the face, WITHOUT the eyes, eyebrows, lips
    (cut out from the face landmarks) and without beard / hair / brows (much darker than the skin)."""
    h, w = rgb.shape[:2]
    R = np.zeros((h, w), np.float32)
    ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    for t, vis in faces:
        ell, _ = face_ellipse((h, w), t, p["feather"])
        skin = skin_weight(ycc, t, ell)
        x, y, bw, bh = t.box
        nose = t.lm[2]
        x0, x1 = int(max(0, nose[0] - bw * 0.25)), int(min(w, nose[0] + bw * 0.25))
        y0, y1 = int(max(0, t.lm[0][1])), int(min(h, nose[1] + bh * 0.05))
        if x1 - x0 > 2 and y1 - y0 > 2:                                       # this person's skin brightness: darker things (beard, brows, hair) are not skin
            ref_y = float(np.median(ycc[y0:y1, x0:x1, 0]))
            dark = np.clip((ycc[..., 0].astype(np.float32) - 0.55 * ref_y) / max(1.0, 0.2 * ref_y), 0, 1)
            skin = skin * dark
        keep = np.ones((h, w), np.float32)                                    # features that must stay sharp
        for e in (t.lm[0], t.lm[1]):
            cv2.ellipse(keep, (int(e[0]), int(e[1])), (max(1, int(bw * 0.17)), max(1, int(bh * 0.09))), 0, 0, 360, 0.0, -1, cv2.LINE_AA)
            cv2.ellipse(keep, (int(e[0]), int(e[1] - bh * 0.13)), (max(1, int(bw * 0.2)), max(1, int(bh * 0.06))), 0, 0, 360, 0.0, -1, cv2.LINE_AA)
        (mlx, mly), (mrx, mry) = t.lm[3], t.lm[4]
        cv2.ellipse(keep, (int((mlx + mrx) / 2), int((mly + mry) / 2)), (max(1, int(abs(mrx - mlx) * 0.65)), max(1, int(bh * 0.09))), 0, 0, 360, 0.0, -1, cv2.LINE_AA)
        keep = cv2.GaussianBlur(keep, (0, 0), max(1.0, bw * 0.03))
        R = np.maximum(R, skin * keep * vis)
    return R, np.zeros_like(R), np.zeros_like(R)


KINDS = {"face_tone_correction": _weights_tone, "face_relighting": _weights_relight, "skin_retouch": _weights_retouch}


def generate_control(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                     progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                     warmup: int = 0, edl: list | None = None) -> ControlRef:
    s = min(1.0, ANALYSIS_LONG_SIDE / max(W, H))
    aw, ah = max(2, int(W * s) // 2 * 2), max(2, int(H * s) // 2 * 2)
    out = Path(cache_dir) / "face" / f"ctrl_{control_key(op.type, source, op, W, H, fps, start_frame, frames, edl)}.mkv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with fsutil.exclusive(out):             # the same analysis requested twice at once (the preview and a test, two previews): the second waits and reuses it
        return _generate_control(source, op, W, H, fps, start_frame, frames, cache_dir, progress, cancel, warmup, edl)


def _generate_control(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                      progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                      warmup: int = 0, edl: list | None = None) -> ControlRef:
    s = min(1.0, ANALYSIS_LONG_SIDE / max(W, H))
    aw, ah = max(2, int(W * s) // 2 * 2), max(2, int(H * s) // 2 * 2)
    out = Path(cache_dir) / "face" / f"ctrl_{control_key(op.type, source, op, W, H, fps, start_frame, frames, edl)}.mkv"
    meta = out.with_suffix(".json")
    if out.is_file() and meta.is_file():
        return ControlRef(str(out), start_frame, frames, json.loads(meta.read_text(encoding="utf-8"))["faces_found"])
    out.parent.mkdir(parents=True, exist_ok=True)
    fn = KINDS[op.type]
    tracker = FaceTracker(aw, ah, conf=op.params["detection_confidence"])
    first = max(0, start_frame - warmup)
    total = start_frame + frames - first
    wr = Writer(out, aw, ah, fps, "rgb24", "gbrp")
    found = written = 0
    try:
        for i, rgb in enumerate(decode(source, aw, ah, fps, first, total, edl)):
            if cancel is not None and cancel.is_set(): raise Cancelled()
            faces = tracker.update(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            R, G, B = fn(rgb, faces, op_params_at(op, fps, first + i), None)       # first+i = timeline frame (keyframes are per frame)
            if first + i < start_frame: continue
            found += bool(faces)
            wr.write((np.stack([R, G, B], -1).clip(0, 1) * 255 + 0.5).astype(np.uint8))
            written += 1
            if progress: progress(written, frames)
        if written == 0: raise RuntimeError("face analysis decoded no frames in this range")
        wr.close()
        meta.write_text(json.dumps({"faces_found": found, "frames": written}), encoding="utf-8")
        return ControlRef(str(out), start_frame, written, found)
    except BaseException:
        wr.abort(); raise
