"""Shared frame decoding for analysis passes, aligned with the engine's base timeline."""
from __future__ import annotations
from .. import fsutil
from .. import proc
import subprocess
from typing import Iterator

import numpy as np

from ..media import ffmpeg_bin

# accurate rounding + full chroma: unbiased YUV<->RGB conversions (default flags darken by ~0.5 level)
SWS = "bicubic+accurate_rnd+full_chroma_int+full_chroma_inp"


def _decode_pieces(source: str, W: int, H: int, fps: float, a: int, n: int, flags: str) -> Iterator[np.ndarray]:
    """n RGB frames of the *source* grid starting at grid frame a (exact: seeks half a frame early, like the render does)."""
    seek = ["-ss", f"{max(0.0, (a - 0.5) / fps):.6f}"] if a > 0 else []
    p = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", *seek, "-i", source, "-sws_flags", SWS, "-vf",
                          f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=decrease:flags={flags},"
                          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                          f"trim=end_frame={n},setpts=PTS-STARTPTS,"
                          f"scale=flags={SWS},format=rgb24",   # explicit, accurate YUV->RGB (the implicit one ignores -sws_flags)
                          "-fps_mode", "passthrough", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size: break
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        try: p.kill()
        except Exception: pass
        p.wait()


def decode(source: str, W: int, H: int, fps: float, first: int, total: int, edl: list | None = None,
           flags: str = "bilinear") -> Iterator[np.ndarray]:
    """Yield up to `total` RGB frames (H,W,3 uint8) starting at *timeline* frame `first`.
    Frame numbering matches the render (same fps + scale/pad and the same cuts: `edl` = [(src_a, src_b)] pieces,
    None = uncut). Each piece is decoded with its own exact seek, so late ranges and cut timelines are cheap."""
    from ..edl import pieces_for_range
    for a, b in ([(first, first + total)] if edl is None else pieces_for_range(edl, first, total)):
        yield from _decode_pieces(source, W, H, fps, a, b - a, flags)


# ---- live preview: the same few frames are decoded again for every slider tweak at the same playhead. Keep the last windows in memory.
import threading as _threading
from collections import OrderedDict as _OD
_WIN: "_OD[str, list[np.ndarray]]" = _OD()
_WIN_LOCK = _threading.Lock()
_WIN_MAX_BYTES = 400 * 1024 * 1024                 # ~ 60 frames of 1080p; a few playhead positions


def decode_cached(source: str, W: int, H: int, fps: float, first: int, total: int, edl: list | None = None, flags: str = "bilinear") -> list[np.ndarray]:
    """decode() for SHORT windows (live previews), remembered in memory: tweaking a setting at the same playhead decodes nothing again.
    Keyed by the file's identity (path, size, modification time), so an edited or replaced file is decoded afresh."""
    import json, os
    st = os.stat(source)
    key = json.dumps([os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), first, total, edl, flags], default=str)
    with _WIN_LOCK:
        if key in _WIN:
            _WIN.move_to_end(key); return _WIN[key]
    frames = [f for f in decode(source, W, H, fps, first, total, edl, flags)]
    for f in frames: f.setflags(write=False)        # shared between requests: nobody may change them in place
    with _WIN_LOCK:
        _WIN[key] = frames
        while len(_WIN) > 1 and sum(sum(x.nbytes for x in v) for v in _WIN.values()) > _WIN_MAX_BYTES: _WIN.popitem(last=False)
    return frames


class Writer:
    """Pipe raw frames into a lossless FFV1 file; atomic rename on close()."""

    def __init__(self, path, W: int, H: int, fps: float, pix_in: str, pix_out: str):
        from pathlib import Path
        self.final = Path(path)
        self.tmp = self.final.with_suffix(".part.mkv")
        self.p = proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{W}x{H}",
                                   "-r", f"{fps:g}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", pix_out, str(self.tmp)],
                                  stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, arr: np.ndarray):
        self.p.stdin.write(np.ascontiguousarray(arr).tobytes())

    def close(self):
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("encode failed: " + self.p.stderr.read().decode(errors="replace")[-300:])
        import os
        fsutil.replace(self.tmp, self.final)

    def abort(self):
        try: self.p.kill()
        except Exception: pass
        fsutil.unlink(self.tmp)
