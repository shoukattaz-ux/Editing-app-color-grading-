"""AI background removal (no green screen) with Robust Video Matting via ONNX Runtime.

The matte is generated as a lossless greyscale video aligned frame-for-frame with the
engine's base timeline (same fps + scale/pad), cached, and consumed by the FFmpeg graph.
"""
from __future__ import annotations
from .. import fsutil
from .. import proc
import hashlib, json, os, subprocess, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .. import edl as _edl
from .frames import decode
from .models import ensure_model
from ..media import ffmpeg_bin

DETAIL_TARGET = {"fast": 320, "balanced": 512, "high": 768}   # downsampled long side fed to the network


@dataclass
class MatteRef:
    path: str               # greyscale alpha video
    fgr_path: str | None    # RGB foreground-colour video (edge colour decontamination), or None
    start_frame: int        # first frame (on the output grid) contained in the file
    frames: int
    screen: str | None = None   # "green" | "blue" for mode 'screen' (so the compiler despills the right colour)


class Cancelled(Exception):
    pass


_MODEL: "RVM | None" = None
_MODEL_LOCK = threading.Lock()


def drop_model() -> None:
    """forget the loaded session (the AI device setting changed)"""
    global _MODEL
    with _MODEL_LOCK: _MODEL = None


def _model() -> "RVM":
    """One ONNX session per process (loading costs ~1 s); state is reset per clip."""
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None: _MODEL = RVM()
        _MODEL.reset()
        return _MODEL


class RVM:
    def __init__(self, threads: int | None = None):
        import onnxruntime as ort
        from . import device
        path = str(ensure_model("rvm"))
        try:
            self.sess = ort.InferenceSession(path, device.session_options(threads), providers=device.provider_chain())
        except Exception:
            so = ort.SessionOptions(); so.intra_op_num_threads = threads or os.cpu_count() or 4
            self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.provider = self.sess.get_providers()[0]
        device.note_session(self.sess)
        self.reset()

    def reset(self):
        self.rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4

    def __call__(self, rgb: np.ndarray, downsample: float) -> tuple[np.ndarray, np.ndarray]:
        """returns (alpha HxW float 0..1, foreground HxWx3 float 0..1)"""
        x = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        fgr, pha, *self.rec = self.sess.run(None, {"src": x, "r1i": self.rec[0], "r2i": self.rec[1], "r3i": self.rec[2],
                                                 "r4i": self.rec[3], "downsample_ratio": np.array([downsample], np.float32)})
        return pha[0, 0], fgr[0].transpose(1, 2, 0)


def clean_matte(a: np.ndarray, threshold: float, despeckle: bool, min_area_frac: float = 0.02) -> np.ndarray:
    """Zero low-confidence noise and drop opaque blobs not connected to the main subject(s)."""
    import cv2
    if threshold > 0:
        a = np.where(a < threshold, 0.0, a)
    if despeckle:
        core = (a > 0.5).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
        if n > 2:
            areas = stats[1:, cv2.CC_STAT_AREA]
            keep = np.zeros(n, bool)
            keep[1:] = areas >= max(areas.max() * 0.15, min_area_frac * a.size)
            region = keep[lab].astype(np.uint8)
            # also keep soft edges (hair) that touch the kept regions
            region = cv2.dilate(region, np.ones((25, 25), np.uint8))
            a = a * region
    return a.astype(np.float32)


def solidify(a: np.ndarray, s: float) -> np.ndarray:
    """Make partly-transparent areas solid: values above `lo` rise to 1, values below `lo` fall to 0 (smooth S-curve).
    s = 0 leaves the matte untouched; 1 is nearly a hard cut. Fixes motion-blurred hands and fine fabric that the network
    marks as, say, 60% opaque (the background then shows through them)."""
    if s <= 0: return a
    lo, hi = 0.05 + 0.25 * s, 1.0 - 0.45 * s
    t = np.clip((a - lo) / max(1e-3, hi - lo), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def matte_key(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, edl: list | None = None) -> str:
    st = os.stat(source)
    p = op.params
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames,
         p["model"], p["detail"], p["matte_threshold"], p["despeckle"], p["edge_color_fix"], "v3", _edl.key(edl)]
    if p.get("solidity", 0.0): d += ["solid", p["solidity"]]
    if p.get("mode") == "screen":
        d += ["screen4", p["auto_screen"], p["key_color"] if not p["auto_screen"] else "", p["similarity"], p["blend"], p["fill_holes"], p["edge_shrink"],
              p["edge_soften"], p["temporal_smooth"], p["spill_suppression"], p["spill_strength"]]
    return hashlib.sha1(json.dumps(d).encode()).hexdigest()[:20]


def generate_matte(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                   progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                   warmup: int = 0, edl: list | None = None) -> MatteRef:
    """Run RVM over [start_frame, start_frame+frames) of the normalised timeline. Cached on disk."""
    out = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, op, W, H, fps, start_frame, frames, edl)}.mkv"
    fgr_out = out.with_name(out.stem + "_fgr.mkv") if op.params["edge_color_fix"] else None
    side = out.with_suffix(".json")
    if out.is_file() and (fgr_out is None or fgr_out.is_file()):
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, frames,
                        json.loads(side.read_text(encoding="utf-8")).get("screen") if side.is_file() else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = op.params
    ds = min(1.0, DETAIL_TARGET[p["detail"]] / max(W, H))
    first = max(0, start_frame - warmup)           # warm-up frames prime the recurrent state (better single-frame previews)
    total = start_frame + frames - first
    tmp = out.with_suffix(".part.mkv")
    enc = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
                            "-r", f"{fps:g}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(tmp)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    ftmp = fgr_out.with_suffix(".part.mkv") if fgr_out else None
    fenc = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264rgb", "-crf", "8", "-preset", "veryfast", str(ftmp)],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE) if fgr_out else None
    keyer = None
    if p.get("mode") == "screen":
        from .screenkey import ScreenKeyer
        keyer = ScreenKeyer(p)
    else:
        model = _model()
    n = 0
    try:
        for rgb in decode(source, W, H, fps, first, total, edl, flags="lanczos"):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if keyer is not None: a, fg = keyer(rgb)
            else: a, fg = model(rgb, ds)
            n += 1
            if first + n - 1 < start_frame:
                continue                       # warm-up frame, not written
            if keyer is None: a = clean_matte(a, p["matte_threshold"], p["despeckle"])
            a = solidify(a, float(p.get("solidity", 0.0)))
            enc.stdin.write((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
            if fenc and fg is not None:
                fenc.stdin.write((np.clip(fg, 0, 1) * 255.0 + 0.5).astype(np.uint8).tobytes())
            if progress: progress(n - (start_frame - first), frames)
        for e in (enc, fenc):
            if e is None: continue
            e.stdin.close()
            if e.wait() != 0:
                raise RuntimeError("matte encode failed: " + e.stderr.read().decode(errors="replace")[-300:])
        written = n - (start_frame - first)
        if written <= 0:
            raise RuntimeError("AI matting produced no frames (could not decode the source in this range)")
        if fenc: fsutil.replace(ftmp, fgr_out)
        scr = keyer.screen_name if keyer is not None else None
        if scr: side.write_text(json.dumps({"screen": scr}), encoding="utf-8")
        fsutil.replace(tmp, out)
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, written, scr)
    except BaseException:
        if ftmp: fsutil.unlink(ftmp)
        for pr in (enc, fenc):
            if pr is None: continue
            try: pr.kill()
            except Exception: pass
        fsutil.unlink(tmp)
        raise
