"""AI background removal (no green screen) with Robust Video Matting via ONNX Runtime.

The matte is generated as a lossless greyscale video aligned frame-for-frame with the
engine's base timeline (same fps + scale/pad), cached, and consumed by the FFmpeg graph.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .models import ensure_model
from ..media import ffmpeg_bin

DETAIL_TARGET = {"fast": 320, "balanced": 512, "high": 768}   # downsampled long side fed to the network


@dataclass
class MatteRef:
    path: str               # greyscale alpha video
    fgr_path: str | None    # RGB foreground-colour video (edge colour decontamination), or None
    start_frame: int        # first frame (on the output grid) contained in the file
    frames: int


class Cancelled(Exception):
    pass


_MODEL: "RVM | None" = None
_MODEL_LOCK = threading.Lock()


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
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads or os.cpu_count() or 4
        avail = ort.get_available_providers()
        prefer = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider") if p in avail]
        try:
            self.sess = ort.InferenceSession(str(ensure_model("rvm")), so, providers=prefer + ["CPUExecutionProvider"])
        except Exception:
            self.sess = ort.InferenceSession(str(ensure_model("rvm")), so, providers=["CPUExecutionProvider"])
        self.provider = self.sess.get_providers()[0]
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


def matte_key(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int) -> str:
    st = os.stat(source)
    p = op.params
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames,
         p["model"], p["detail"], p["matte_threshold"], p["despeckle"], p["edge_color_fix"], "v2"]
    return hashlib.sha1(json.dumps(d).encode()).hexdigest()[:20]


def generate_matte(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                   progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                   warmup: int = 0) -> MatteRef:
    """Run RVM over [start_frame, start_frame+frames) of the normalised timeline. Cached on disk."""
    out = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, op, W, H, fps, start_frame, frames)}.mkv"
    fgr_out = out.with_name(out.stem + "_fgr.mkv") if op.params["edge_color_fix"] else None
    if out.is_file() and (fgr_out is None or fgr_out.is_file()):
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = op.params
    ds = min(1.0, DETAIL_TARGET[p["detail"]] / max(W, H))
    first = max(0, start_frame - warmup)           # warm-up frames prime the recurrent state (better single-frame previews)
    total = start_frame + frames - first
    # decode exactly like the engine's base chain so frame N here == frame N in the render
    # previews (few frames) seek straight to the frame; full-range mattes decode from 0 so frame numbering is exact
    seek_args, trim_from = (["-ss", f"{first / fps:.6f}"], 0) if total <= 16 and first > 0 else ([], first)
    from .frames import SWS
    dec = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", *seek_args, "-i", source, "-sws_flags", SWS, "-vf",
                            f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,trim=start_frame={trim_from}:end_frame={trim_from + total},setpts=PTS-STARTPTS,"
                            f"scale=flags={SWS},format=rgb24",   # explicit, accurate YUV->RGB
                            "-fps_mode", "passthrough", "-frames:v", str(total), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tmp = out.with_suffix(".part.mkv")
    enc = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
                            "-r", f"{fps:g}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(tmp)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    ftmp = fgr_out.with_suffix(".part.mkv") if fgr_out else None
    fenc = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264rgb", "-crf", "8", "-preset", "veryfast", str(ftmp)],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE) if fgr_out else None
    model = _model()
    n = 0
    fsize = W * H * 3
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = dec.stdout.read(fsize)
            if len(buf) < fsize:
                break
            rgb = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            a, fg = model(rgb, ds)
            n += 1
            if first + n - 1 < start_frame:
                continue                       # warm-up frame, not written
            a = clean_matte(a, p["matte_threshold"], p["despeckle"])
            enc.stdin.write((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
            if fenc:
                fenc.stdin.write((np.clip(fg, 0, 1) * 255.0 + 0.5).astype(np.uint8).tobytes())
            if progress: progress(n - (start_frame - first), frames)
        for e in (enc, fenc):
            if e is None: continue
            e.stdin.close()
            if e.wait() != 0:
                raise RuntimeError("matte encode failed: " + e.stderr.read().decode(errors="replace")[-300:])
        dec.wait()
        written = n - (start_frame - first)
        if written <= 0:
            raise RuntimeError("AI matting produced no frames (could not decode the source in this range)")
        if fenc: os.replace(ftmp, fgr_out)
        os.replace(tmp, out)
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, written)
    except BaseException:
        if ftmp: ftmp.unlink(missing_ok=True)
        for pr in (dec, enc, fenc):
            if pr is None: continue
            try: pr.kill()
            except Exception: pass
        tmp.unlink(missing_ok=True)
        raise
