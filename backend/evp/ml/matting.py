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
from .frames import decode, decode_cached
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
    plate: dict | None = None   # clean plate use: {"used": frames the plate fitted, "rejected": frames it did not fit} (None = no plate)
    hair: dict | None = None    # hair detail use: {"used": frames the AI refined, "skipped": frames where the AI did not find the same person} (None = off)


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


def matte_key(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, edl: list | None = None, plate: str | None = None) -> str:
    st = os.stat(source)
    p = op.params
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames,
         p["model"], p["detail"], p["matte_threshold"], p["despeckle"], p["edge_color_fix"], "v3", _edl.key(edl)]
    if p.get("solidity", 0.0): d += ["solid", p["solidity"]]
    if p.get("mode") == "screen":
        d += ["screen4", p["auto_screen"], p["key_color"] if not p["auto_screen"] else "", p["similarity"], p["blend"], p["fill_holes"], p["edge_shrink"],
              p["edge_soften"], p["temporal_smooth"], p["spill_suppression"], p["spill_strength"]]
        if plate:
            from .cleanplate import plate_identity
            d += plate_identity(plate) + [p.get("clean_plate_strength", 1.0)]
        if p.get("hair_detail", 0.0) > 0:
            d += ["hair1", p["hair_detail"], p["hair_width"], p["model"], p["detail"]]
    return hashlib.sha1(json.dumps(d).encode()).hexdigest()[:20]


def generate_matte(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                   progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                   warmup: int = 0, edl: list | None = None, plate: str | None = None) -> MatteRef:
    out = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, op, W, H, fps, start_frame, frames, edl, plate)}.mkv"
    with fsutil.exclusive(out):                                   # same cached result requested twice at once: the second waits and reuses it
        return _generate_matte(source, op, W, H, fps, start_frame, frames, cache_dir, progress, cancel, warmup, edl, plate)


def _generate_matte(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                    progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                    warmup: int = 0, edl: list | None = None, plate: str | None = None) -> MatteRef:
    """Run RVM over [start_frame, start_frame+frames) of the normalised timeline. Cached on disk."""
    out = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, op, W, H, fps, start_frame, frames, edl, plate)}.mkv"
    fgr_out = out.with_name(out.stem + "_fgr.mkv") if op.params["edge_color_fix"] else None
    side = out.with_suffix(".json")
    if out.is_file() and (fgr_out is None or fgr_out.is_file()):
        sd = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, frames, sd.get("screen"), sd.get("plate"), sd.get("hair"))
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
    keyer = keyer2 = None
    hair = 0.0; hair_used = hair_seen = 0
    if p.get("mode") == "screen":
        from .screenkey import ScreenKeyer
        pl = None
        if plate:
            from .cleanplate import load_plate
            pl = load_plate(plate, W, H)
        ps = float(p.get("clean_plate_strength", 1.0))
        keyer = ScreenKeyer(p, pl if ps > 0 else None)
        hair = float(p.get("hair_detail", 0.0))
        if hair > 0: model = _model()                    # hair detail: the AI model refines a band around the edge
        keyer2 = ScreenKeyer(p, None) if (pl is not None and 0.0 < ps < 1.0) else None      # partial strength: mix the plate key with the plain key
    else:
        model = _model()
    if keyer is not None and frames >= PARALLEL_MIN_FRAMES and _workers(frames) > 1:
        return _screen_parallel(source, op, W, H, fps, start_frame, frames, edl, plate, out, fgr_out, side, ds, progress, cancel, warmup)
    n = 0
    try:
        # a live-preview frame (one frame + warm-up): the decoded frames are remembered, so a slider tweak at the same playhead decodes nothing
        src_frames = decode_cached(source, W, H, fps, first, total, edl, flags="lanczos") if frames == 1 else decode(source, W, H, fps, first, total, edl, flags="lanczos")
        for rgb in src_frames:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if keyer is not None:
                warm = first + n < start_frame                      # (n counts frames done so far) a warm-up frame: its matte primes the keyer, its colours are never written
                a, fg = keyer(rgb, need_fg=not warm)
                if hair > 0:
                    a_ai, fg_ai = model(rgb, ds)                       # warm-up frames too: the network is recurrent and needs its history
                if hair > 0 and not warm:                              # ... but only real frames are blended and written
                    from .hair import blend_hair
                    a, fg, used = blend_hair(a, fg, a_ai, fg_ai, hair, float(p["hair_width"]), keyer.c if p.get("spill_suppression", True) else None)
                    hair_used += 1 if used else 0; hair_seen += 1
                if keyer2 is not None:
                    a2, fg2 = keyer2(rgb, need_fg=not warm)
                    a = (ps * a + (1.0 - ps) * a2).astype(np.float32)
                    if fg is not None and fg2 is not None: fg = (ps * fg + (1.0 - ps) * fg2).astype(np.float32)
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
        pinfo = {"used": keyer.plate_frames, "rejected": keyer.plate_rejected} if (keyer is not None and keyer.plate is not None) else None
        hinfo = {"used": hair_used, "skipped": hair_seen - hair_used} if hair > 0 else None
        if scr: side.write_text(json.dumps({"screen": scr, **({"plate": pinfo} if pinfo else {}), **({"hair": hinfo} if hinfo else {})}), encoding="utf-8")
        fsutil.replace(tmp, out)
        return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, written, scr, pinfo, hinfo)
    except BaseException:
        if ftmp: fsutil.unlink(ftmp)
        for pr in (enc, fenc):
            if pr is None: continue
            try: pr.kill()
            except Exception: pass
        fsutil.unlink(tmp)
        raise


# ---------------------------------------------------------------------------------------------------------------------------------------------
# Full-range green-screen keys run on every processor core: the range is split into one piece per core, each piece is keyed with its own keyer
# (primed on the 6 frames before it - the same warm-up a live preview frame uses, which matches an uninterrupted run to within 0.2 of 255),
# written to its own lossless part file, and the parts are joined without re-encoding.
PARALLEL_MIN_FRAMES = 48
CHUNK_WARMUP = 6


def _workers(frames: int) -> int:
    n = int(os.environ.get("EVP_KEY_THREADS") or 0) or (os.cpu_count() or 1)
    return max(1, min(n, 16, frames // 24))


def _screen_parallel(source, op, W, H, fps, start_frame, frames, edl, plate, out, fgr_out, side, ds, progress, cancel, warmup) -> MatteRef:
    from concurrent.futures import ThreadPoolExecutor
    from .screenkey import ScreenKeyer
    p = op.params
    pl = None
    if plate:
        from .cleanplate import load_plate
        pl = load_plate(plate, W, H)
    ps = float(p.get("clean_plate_strength", 1.0))
    hair = float(p.get("hair_detail", 0.0))
    base_model = _model() if hair > 0 else None
    model_lock = threading.Lock()                     # one network, called by one piece at a time (a graphics-card session must not run concurrently)
    nw = _workers(frames)
    size = -(-frames // nw)
    pieces = [(start_frame + i * size, min(size, frames - i * size)) for i in range(nw) if frames - i * size > 0]
    lock = threading.Lock()
    done = [0]
    stats = {"plate": [0, 0], "hair": [0, 0], "screen": None}
    stop = threading.Event()

    def piece(i: int, a0: int, cnt: int):
        part = out.with_name(f"{out.stem}.part{i}.mkv"); fpart = fgr_out.with_name(f"{fgr_out.stem}.part{i}.mkv") if fgr_out else None
        enc = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", f"{fps:g}", "-i", "-",
                          "-c:v", "ffv1", "-pix_fmt", "gray", str(part)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        fenc = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps:g}", "-i", "-",
                           "-c:v", "libx264rgb", "-crf", "8", "-preset", "veryfast", str(fpart)], stdin=subprocess.PIPE, stderr=subprocess.PIPE) if fpart else None
        keyer = ScreenKeyer(p, pl if ps > 0 else None)
        keyer2 = ScreenKeyer(p, None) if (pl is not None and 0.0 < ps < 1.0) else None
        model = None
        if base_model is not None:
            import copy
            model = copy.copy(base_model); model.reset()            # shares the loaded network, keeps its OWN recurrent memory
        warm_n = warmup if i == 0 else CHUNK_WARMUP
        first = max(0, a0 - warm_n)
        n = 0
        try:
            for rgb in decode(source, W, H, fps, first, cnt + (a0 - first), edl, flags="lanczos"):
                if stop.is_set() or (cancel is not None and cancel.is_set()): raise Cancelled()
                warm = first + n < a0
                a, fg = keyer(rgb, need_fg=not warm)
                if hair > 0:
                    with model_lock: a_ai, fg_ai = model(rgb, ds)      # warm-up frames too: the network is recurrent and needs its history
                if hair > 0 and not warm:
                    from .hair import blend_hair
                    a, fg, used = blend_hair(a, fg, a_ai, fg_ai, hair, float(p["hair_width"]), keyer.c if p.get("spill_suppression", True) else None)
                    with lock: stats["hair"][0] += 1 if used else 0; stats["hair"][1] += 1
                if keyer2 is not None:
                    a2, fg2 = keyer2(rgb, need_fg=not warm)
                    a = (ps * a + (1.0 - ps) * a2).astype(np.float32)
                    if fg is not None and fg2 is not None: fg = (ps * fg + (1.0 - ps) * fg2).astype(np.float32)
                n += 1
                if warm: continue
                a = solidify(a, float(p.get("solidity", 0.0)))
                enc.stdin.write((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
                if fenc and fg is not None: fenc.stdin.write((np.clip(fg, 0, 1) * 255.0 + 0.5).astype(np.uint8).tobytes())
                with lock:
                    done[0] += 1
                    if progress: progress(done[0], frames)
            for e in (enc, fenc):
                if e is None: continue
                e.stdin.close()
                if e.wait() != 0: raise RuntimeError("matte encode failed: " + e.stderr.read().decode(errors="replace")[-300:])
            if n - (a0 - first) != cnt: raise RuntimeError(f"keying produced {n - (a0 - first)} of {cnt} frames (could not decode the source in this range)")
            with lock:
                if keyer.plate is not None: stats["plate"][0] += keyer.plate_frames; stats["plate"][1] += keyer.plate_rejected
                if stats["screen"] is None or i == 0: stats["screen"] = keyer.screen_name
            return part, fpart
        except BaseException:
            stop.set()
            for e in (enc, fenc):
                if e is None: continue
                try: e.kill()
                except Exception: pass
            raise

    parts: list = []
    try:
        with ThreadPoolExecutor(len(pieces)) as ex:
            futs = [ex.submit(piece, i, a0, cnt) for i, (a0, cnt) in enumerate(pieces)]
            parts = [f.result() for f in futs]
        _join([pt[0] for pt in parts], out)
        if fgr_out: _join([pt[1] for pt in parts], fgr_out)
    finally:
        for i in range(len(pieces)):
            fsutil.unlink(out.with_name(f"{out.stem}.part{i}.mkv"))
            if fgr_out: fsutil.unlink(fgr_out.with_name(f"{fgr_out.stem}.part{i}.mkv"))
    pinfo = {"used": stats["plate"][0], "rejected": stats["plate"][1]} if pl is not None and ps > 0 else None
    hinfo = {"used": stats["hair"][0], "skipped": stats["hair"][1] - stats["hair"][0]} if hair > 0 else None
    scr = stats["screen"] or "green"
    side.write_text(json.dumps({"screen": scr, **({"plate": pinfo} if pinfo else {}), **({"hair": hinfo} if hinfo else {})}), encoding="utf-8")
    return MatteRef(str(out), str(fgr_out) if fgr_out else None, start_frame, frames, scr, pinfo, hinfo)


def _join(parts: list, dest: Path) -> None:
    """join part files (same size / codec / settings) into dest without re-encoding; atomic"""
    lst = dest.with_suffix(".parts.txt")
    lst.write_text("".join(f"file '{Path(x).as_posix()}'\n" for x in parts), encoding="utf-8")
    tmp = dest.with_name(dest.stem + ".joining.mkv")
    try:
        r = proc.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(tmp)], capture_output=True)
        if r.returncode != 0: raise RuntimeError("joining the key parts failed: " + r.stderr.decode(errors="replace")[-300:])
        fsutil.replace(tmp, dest)
    finally:
        fsutil.unlink(lst); fsutil.unlink(tmp)
