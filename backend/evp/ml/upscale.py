"""AI super-resolution (Real-ESRGAN general x4 v3) on CPU or GPU via ONNX Runtime.

Pre-pass: decode the source at its native size, run the x4 network (tiled), resize to the output
geometry (fit + pad exactly like the base chain), optionally blend with a Lanczos upscale, and write a
high-quality intermediate that replaces the base frames inside the operation's range.
"""
from __future__ import annotations
from .. import fsutil
from .. import proc as _proc
import hashlib, json, os, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .. import edl as _edl
from ..animate import op_params_at, keyframes_key
from .frames import decode
from .models import ensure_model, models_dir, make_session
from ..media import ffmpeg_bin

NET_SCALE = 4
TILE, PAD = 256, 12


class Cancelled(Exception):
    pass


@dataclass
class UpscaleRef:
    path: str
    start_frame: int
    frames: int
    gpu_fallback: str | None = None   # GPU error that made the run continue on CPU
    faces_seen: int = 0               # frames where face protection was active


_SESS: dict = {}
_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()      # the preview frame and the preview video both ask for the converted network at the same moment


# model name -> (weights in the model store, network family, network scale, CPU microseconds per input pixel)
UPSCALE_MODELS = {
    "general":    ("esrgan_general", "srvgg", 4, 8.5),
    "quality":    ("esrgan_x4plus", "rrdb", 4, 100.0),
    "quality_x2": ("esrgan_x2plus", "rrdb", 2, 24.0),
    "anime":      ("esrgan_anime", "srvgg", 4, 4.5),
}


def onnx_path(model: str, denoise: float = 0.0) -> Path:
    weights, family, _, _ = UPSCALE_MODELS[model]
    if model == "general":       # the denoise slider = DNI between the general and the with-denoise weights
        d = int(round(denoise * 20)) * 5
        p = models_dir() / f"realesr-general-x4v3_dn{d:03d}.onnx"
    else:
        p = models_dir() / f"{Path(MODELS_FILE(weights)).stem}.onnx"
    if not p.is_file():
        with _BUILD_LOCK:
            if p.is_file(): return p                                   # another request finished it while this one waited
            from .srvgg import load_state_dict, dni
            sd = load_state_dict(ensure_model(weights))
            if model == "general" and d:
                sd = dni(sd, load_state_dict(ensure_model("esrgan_wdn")), d / 100.0)
            if family == "srvgg":
                from .srvgg import build_onnx
                build_onnx(sd, p)
            else:
                from .rrdb import build_onnx as build_rrdb
                build_rrdb(sd, p)
    return p


def MODELS_FILE(name: str) -> str:
    from .models import MODELS
    return MODELS[name].file


def session(model: str, denoise: float = 0.0):
    p = onnx_path(model, denoise)
    with _LOCK:
        if str(p) not in _SESS:
            _SESS[str(p)] = make_session(p)
        return _SESS[str(p)]


def run_net(sess, rgb: np.ndarray, scale: int = 4) -> np.ndarray:
    """uint8 HxWx3 -> float32 (sH)x(sW)x3 in 0..1. Tiled with overlap to bound memory; odd sizes padded for x2 models."""
    h0, w0 = rgb.shape[:2]
    x = rgb.astype(np.float32) / 255.0
    if scale == 2 and (h0 % 2 or w0 % 2):                       # SpaceToDepth needs even sizes
        x = np.pad(x, ((0, h0 % 2), (0, w0 % 2), (0, 0)), mode="edge")
    h, w = x.shape[:2]
    run = lambda a: sess.run(None, {"input": np.ascontiguousarray(a.transpose(2, 0, 1)[None])})[0][0].transpose(1, 2, 0)
    if h * w <= TILE * TILE * 2:
        return run(x)[:h0 * scale, :w0 * scale]
    out = np.zeros((h * scale, w * scale, 3), np.float32)
    for y0 in range(0, h, TILE):
        for x0 in range(0, w, TILE):
            y1, x1 = min(h, y0 + TILE), min(w, x0 + TILE)
            ya, xa, yb, xb = max(0, y0 - PAD), max(0, x0 - PAD), min(h, y1 + PAD), min(w, x1 + PAD)
            ya, xa = ya - ya % 2, xa - xa % 2                    # keep tile origins even (x2 models)
            yb, xb = yb + (yb - ya) % 2, xb + (xb - xa) % 2
            t = run(np.pad(x[ya:yb, xa:xb], ((0, max(0, yb - h)), (0, max(0, xb - w)), (0, 0)), mode="edge"))
            oy, ox = (y0 - ya) * scale, (x0 - xa) * scale
            out[y0 * scale:y1 * scale, x0 * scale:x1 * scale] = t[oy:oy + (y1 - y0) * scale, ox:ox + (x1 - x0) * scale]
    return out[:h0 * scale, :w0 * scale]


def run_x4(sess, rgb: np.ndarray) -> np.ndarray:      # backwards-compatible helper
    return run_net(sess, rgb, 4)


class DetailProcessor:
    """Turns raw per-frame network output into a temporally stable, colour-faithful result.

    out = lowpass(lanczos) + detail,  detail = AI - lowpass(AI)
      * colour_fidelity: low frequencies (colour, exposure, contrast) come from the classical upscale of the
        original, so the AI can only add fine detail, never shift tone.
      * face protection: detail is attenuated inside tracked face ellipses (avoids plastic skin / redrawn brows).
      * temporal stabilisation: the previous frame's detail is motion-compensated (DIS optical flow measured on the
        source frames) and blended in where the warp is photometrically reliable -> no shimmer, no ghosting.
    """

    def __init__(self, p: dict, fw: int, fh: int, src_w: int, src_h: int):
        self.p, self.fw, self.fh, self.sw, self.sh = p, fw, fh, src_w, src_h
        f = max(fw / src_w, fh / src_h)
        self.sigma = max(1.0, 0.6 * f)               # cut-off ~ the source's Nyquist frequency at output scale
        self.prev_src = None
        self.prev_detail = None
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST) if p["temporal_stability"] > 0 else None
        self.tracker = None
        if p["face_detail"] < 1.0:
            try:
                from .face import FaceTracker
                s = min(1.0, 480 / max(fw, fh))
                self.fs = s
                self.tracker = FaceTracker(max(2, int(fw * s)), max(2, int(fh * s)))
            except Exception:
                self.tracker = None              # detector unavailable -> no face protection (logged by caller)
        self.faces_seen = 0

    def __call__(self, src_rgb: np.ndarray, sr: np.ndarray, lz: np.ndarray) -> np.ndarray:
        p = self.p
        blur = lambda a: cv2.GaussianBlur(a, (0, 0), self.sigma)
        detail = sr - blur(sr)
        low = blur(lz) if p["color_fidelity"] else blur(sr)

        if self.tracker is not None:
            from .face import face_ellipse
            small = cv2.resize(lz, (int(self.fw * self.fs), int(self.fh * self.fs)), interpolation=cv2.INTER_AREA)
            faces = self.tracker.update(cv2.cvtColor((np.clip(small, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
            if faces:
                self.faces_seen += 1
                m = np.zeros(small.shape[:2], np.float32)
                for tr, vis in faces:
                    e, _ = face_ellipse(m.shape, tr, 0.6)
                    m = np.maximum(m, e * vis)
                m = cv2.resize(m, (self.fw, self.fh), interpolation=cv2.INTER_LINEAR)[..., None]
                detail = detail * (1.0 - (1.0 - p["face_detail"]) * m)

        if self.flow is not None:
            g = cv2.cvtColor(src_rgb, cv2.COLOR_RGB2GRAY)
            if self.prev_src is not None and self.prev_detail is not None:
                fl = self.flow.calc(g, self.prev_src, None)              # current -> previous, source pixels
                gx, gy = np.meshgrid(np.arange(self.sw, dtype=np.float32), np.arange(self.sh, dtype=np.float32))
                mx, my = gx + fl[..., 0], gy + fl[..., 1]
                warped_src = cv2.remap(self.prev_src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                err = cv2.GaussianBlur(np.abs(warped_src.astype(np.float32) - g.astype(np.float32)), (0, 0), 1.5)
                conf = np.exp(-(err / 6.0) ** 2)                          # reliable where the warp explains the pixels
                kx, ky = self.fw / self.sw, self.fh / self.sh
                up = lambda a: cv2.resize(a, (self.fw, self.fh), interpolation=cv2.INTER_LINEAR)
                ox, oy = np.meshgrid(np.arange(self.fw, dtype=np.float32), np.arange(self.fh, dtype=np.float32))
                wd = cv2.remap(self.prev_detail, ox + up(fl[..., 0]) * kx, oy + up(fl[..., 1]) * ky, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
                k = (p["temporal_stability"] * 0.85 * up(conf))[..., None]
                detail = (1 - k) * detail + k * wd
            self.prev_src, self.prev_detail = g, detail

        out = low + detail
        if p["grain"] > 0:
            # re-inject the source's own fine grain/noise (edge-preserving residual), so the result looks camera-made
            s = src_rgb.astype(np.float32)
            noise = (s - cv2.bilateralFilter(s, 5, 40, 2)) / 255.0   # captures ~70% of noise, strong edges excluded
            out = out + p["grain"] * cv2.resize(noise, (self.fw, self.fh), interpolation=cv2.INTER_CUBIC)
        if p["strength"] < 1.0:
            out = p["strength"] * out + (1 - p["strength"]) * lz
        return out


def fit_geometry(sw: int, sh: int, W: int, H: int) -> tuple[int, int, int, int]:
    """Same as FFmpeg scale=W:H:force_original_aspect_ratio=decrease + centred pad (even sizes)."""
    r = min(W / sw, H / sh)
    fw, fh = max(2, int(sw * r) // 2 * 2), max(2, int(sh * r) // 2 * 2)
    fw, fh = min(fw, W), min(fh, H)
    return fw, fh, (W - fw) // 2, (H - fh) // 2


def upscale_key(source: str, op, W, H, fps, start_frame, frames, edl=None) -> str:
    st = os.stat(source)
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames, sorted(op.params.items()), keyframes_key(op), "v5", _edl.key(edl)]
    return hashlib.sha1(json.dumps(d, default=str).encode()).hexdigest()[:20]


def generate_upscale(source: str, op, src_w: int, src_h: int, W: int, H: int, fps: float, start_frame: int, frames: int,
                     cache_dir: str, progress: Callable[[int, int], None] | None = None,
                     cancel: threading.Event | None = None, warmup: int = 0, edl: list | None = None) -> UpscaleRef:
    out = Path(cache_dir) / "upscale" / f"up_{upscale_key(source, op, W, H, fps, start_frame, frames, edl)}.mkv"
    with fsutil.exclusive(out):                                   # same cached result requested twice at once: the second waits and reuses it
        return _generate_upscale(source, op, src_w, src_h, W, H, fps, start_frame, frames, cache_dir, progress, cancel, warmup, edl)


def _generate_upscale(source: str, op, src_w: int, src_h: int, W: int, H: int, fps: float, start_frame: int, frames: int,
                      cache_dir: str, progress: Callable[[int, int], None] | None = None,
                      cancel: threading.Event | None = None, warmup: int = 0, edl: list | None = None) -> UpscaleRef:
    import subprocess
    out = Path(cache_dir) / "upscale" / f"up_{upscale_key(source, op, W, H, fps, start_frame, frames, edl)}.mkv"
    if out.is_file():
        return UpscaleRef(str(out), start_frame, frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = op.params
    model = p["model"]
    net_scale = UPSCALE_MODELS[model][2]
    sess = session(model, p["denoise"])
    fw, fh, px, py = fit_geometry(src_w, src_h, W, H)
    # 'fast' halves the network input when the needed factor is <= 2 (4x fewer pixels to process)
    need = max(fw / src_w, fh / src_h)
    pre = 0.5 if (p["quality"] == "fast" and need <= net_scale / 2) else 1.0
    tmp = out.with_suffix(".part.mkv")
    enc = _proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264rgb", "-crf", "8", "-preset", "veryfast", str(tmp)],   # stays RGB: no extra YUV round trip
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    fell_back: list[str] = []
    # processor components are created once, so decide from the extreme keyframe values whether they can ever be needed
    pi = dict(p)
    if "temporal_stability" in op.keyframes: pi["temporal_stability"] = max(k.value for k in op.keyframes["temporal_stability"])
    if "face_detail" in op.keyframes: pi["face_detail"] = min(k.value for k in op.keyframes["face_detail"])
    proc = DetailProcessor(pi, fw, fh, src_w, src_h)
    first = max(0, start_frame - warmup) if pi["temporal_stability"] > 0 else start_frame
    warm = start_frame - first
    try:
        for rgb in decode(source, src_w, src_h, fps, first, frames + warm, edl):
            if cancel is not None and cancel.is_set(): raise Cancelled()
            inp = rgb if pre == 1.0 else cv2.resize(rgb, (max(2, int(src_w * pre)), max(2, int(src_h * pre))), interpolation=cv2.INTER_AREA)
            try:
                sr = run_net(sess, inp, net_scale)
            except Exception as e:       # e.g. GPU out of memory / driver error -> continue on CPU
                if sess.get_providers()[0] == "CPUExecutionProvider": raise
                from .models import make_session_cpu
                sess = make_session_cpu(onnx_path(model, p["denoise"]))
                fell_back.append(str(e)[:200])
                sr = run_net(sess, inp, net_scale)
            sr = cv2.resize(sr, (fw, fh), interpolation=cv2.INTER_AREA if sr.shape[1] > fw else cv2.INTER_LANCZOS4)
            lz = cv2.resize(rgb, (fw, fh), interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255.0
            if op.keyframes: proc.p = op_params_at(op, fps, first + n)     # per-frame parameters (n counts decoded frames incl. warm-up)
            sr = proc(rgb, sr, lz)
            if n < warm: n += 1; continue          # priming frames for the temporal filter (previews only)
            frame = np.zeros((H, W, 3), np.uint8)
            frame[py:py + fh, px:px + fw] = (np.clip(sr, 0, 1) * 255 + 0.5).astype(np.uint8)
            enc.stdin.write(frame.tobytes())
            n += 1
            if progress: progress(n - warm, frames)
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("upscale encode failed: " + enc.stderr.read().decode(errors="replace")[-300:])
        if n - warm <= 0:
            raise RuntimeError("AI upscaling decoded no frames in this range")
        fsutil.replace(tmp, out)
        return UpscaleRef(str(out), start_frame, n - warm, fell_back[0] if fell_back else None, proc.faces_seen)
    except BaseException:
        try: enc.kill()
        except Exception: pass
        fsutil.unlink(tmp)
        raise


def estimate_seconds_per_frame(src_w: int, src_h: int, quality: str, need: float, gpu: bool, model: str = "general") -> float:
    """CPU cost measured on a 4-thread machine (us per network input pixel); GPU assumed ~25x faster (not measured here)."""
    _, _, net_scale, us = UPSCALE_MODELS[model]
    px = src_w * src_h * (0.25 if quality == "fast" and need <= net_scale / 2 else 1.0)
    s = px * us * 1e-6
    return s / 25 if gpu else s
