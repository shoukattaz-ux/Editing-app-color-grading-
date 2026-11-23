"""AI super-resolution (Real-ESRGAN general x4 v3) on CPU or GPU via ONNX Runtime.

Pre-pass: decode the source at its native size, run the x4 network (tiled), resize to the output
geometry (fit + pad exactly like the base chain), optionally blend with a Lanczos upscale, and write a
high-quality intermediate that replaces the base frames inside the operation's range.
"""
from __future__ import annotations
import hashlib, json, os, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

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


_SESS: dict = {}
_LOCK = threading.Lock()


def onnx_path(denoise: float) -> Path:
    d = int(round(denoise * 20)) * 5            # 0, 5, ... 100 (% of the with-denoise weights)
    p = models_dir() / f"realesr-general-x4v3_dn{d:03d}.onnx"
    if not p.is_file():
        from .srvgg import load_state_dict, build_onnx, dni
        a = load_state_dict(ensure_model("esrgan_general"))
        sd = a if d == 0 else dni(a, load_state_dict(ensure_model("esrgan_wdn")), d / 100.0)
        build_onnx(sd, p)
    return p


def session(denoise: float):
    p = onnx_path(denoise)
    with _LOCK:
        if str(p) not in _SESS:
            _SESS[str(p)] = make_session(p)
        return _SESS[str(p)]


def run_x4(sess, rgb: np.ndarray) -> np.ndarray:
    """uint8 HxWx3 -> float32 (4H)x(4W)x3 in 0..1, tiled with overlap to bound memory."""
    h, w = rgb.shape[:2]
    x = rgb.astype(np.float32) / 255.0
    if h * w <= TILE * TILE * 2:
        y = sess.run(None, {"input": x.transpose(2, 0, 1)[None]})[0][0]
        return y.transpose(1, 2, 0)
    out = np.zeros((h * NET_SCALE, w * NET_SCALE, 3), np.float32)
    for y0 in range(0, h, TILE):
        for x0 in range(0, w, TILE):
            y1, x1 = min(h, y0 + TILE), min(w, x0 + TILE)
            ya, xa, yb, xb = max(0, y0 - PAD), max(0, x0 - PAD), min(h, y1 + PAD), min(w, x1 + PAD)
            t = sess.run(None, {"input": x[ya:yb, xa:xb].transpose(2, 0, 1)[None]})[0][0].transpose(1, 2, 0)
            oy, ox = (y0 - ya) * NET_SCALE, (x0 - xa) * NET_SCALE
            out[y0 * 4:y1 * 4, x0 * 4:x1 * 4] = t[oy:oy + (y1 - y0) * 4, ox:ox + (x1 - x0) * 4]
    return out


def fit_geometry(sw: int, sh: int, W: int, H: int) -> tuple[int, int, int, int]:
    """Same as FFmpeg scale=W:H:force_original_aspect_ratio=decrease + centred pad (even sizes)."""
    r = min(W / sw, H / sh)
    fw, fh = max(2, int(sw * r) // 2 * 2), max(2, int(sh * r) // 2 * 2)
    fw, fh = min(fw, W), min(fh, H)
    return fw, fh, (W - fw) // 2, (H - fh) // 2


def upscale_key(source: str, op, W, H, fps, start_frame, frames) -> str:
    st = os.stat(source)
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames, sorted(op.params.items()), "v1"]
    return hashlib.sha1(json.dumps(d, default=str).encode()).hexdigest()[:20]


def generate_upscale(source: str, op, src_w: int, src_h: int, W: int, H: int, fps: float, start_frame: int, frames: int,
                     cache_dir: str, progress: Callable[[int, int], None] | None = None,
                     cancel: threading.Event | None = None) -> UpscaleRef:
    import subprocess
    out = Path(cache_dir) / "upscale" / f"up_{upscale_key(source, op, W, H, fps, start_frame, frames)}.mkv"
    if out.is_file():
        return UpscaleRef(str(out), start_frame, frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = op.params
    sess = session(p["denoise"])
    fw, fh, px, py = fit_geometry(src_w, src_h, W, H)
    # 'fast' halves the network input when the needed factor is <= 2 (4x fewer pixels to process)
    need = max(fw / src_w, fh / src_h)
    pre = 0.5 if (p["quality"] == "fast" and need <= 2.0) else 1.0
    tmp = out.with_suffix(".part.mkv")
    enc = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(tmp)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    fell_back: list[str] = []
    try:
        for rgb in decode(source, src_w, src_h, fps, start_frame, frames):
            if cancel is not None and cancel.is_set(): raise Cancelled()
            inp = rgb if pre == 1.0 else cv2.resize(rgb, (max(2, int(src_w * pre)), max(2, int(src_h * pre))), interpolation=cv2.INTER_AREA)
            try:
                sr = run_x4(sess, inp)
            except Exception as e:       # e.g. GPU out of memory / driver error -> continue on CPU
                if sess.get_providers()[0] == "CPUExecutionProvider": raise
                from .models import make_session_cpu
                sess = make_session_cpu(onnx_path(p["denoise"]))
                fell_back.append(str(e)[:200])
                sr = run_x4(sess, inp)
            sr = cv2.resize(sr, (fw, fh), interpolation=cv2.INTER_AREA if sr.shape[1] > fw else cv2.INTER_LANCZOS4)
            if p["strength"] < 1.0:
                lz = cv2.resize(rgb, (fw, fh), interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255.0
                sr = p["strength"] * sr + (1 - p["strength"]) * lz
            frame = np.zeros((H, W, 3), np.uint8)
            frame[py:py + fh, px:px + fw] = (np.clip(sr, 0, 1) * 255 + 0.5).astype(np.uint8)
            enc.stdin.write(frame.tobytes())
            n += 1
            if progress: progress(n, frames)
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("upscale encode failed: " + enc.stderr.read().decode(errors="replace")[-300:])
        if n == 0:
            raise RuntimeError("AI upscaling decoded no frames in this range")
        os.replace(tmp, out)
        return UpscaleRef(str(out), start_frame, n, fell_back[0] if fell_back else None)
    except BaseException:
        try: enc.kill()
        except Exception: pass
        tmp.unlink(missing_ok=True)
        raise


def estimate_seconds_per_frame(src_w: int, src_h: int, quality: str, need: float, gpu: bool) -> float:
    """Measured on a 4-thread CPU: ~8.5 us per input pixel. GPU assumed ~25x faster (not measured here)."""
    px = src_w * src_h * (0.25 if quality == "fast" and need <= 2 else 1.0)
    s = px * 8.5e-6
    return s / 25 if gpu else s
