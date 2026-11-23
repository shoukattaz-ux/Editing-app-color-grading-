"""Shared frame decoding for analysis passes, aligned with the engine's base timeline."""
from __future__ import annotations
import subprocess
from typing import Iterator

import numpy as np

from ..media import ffmpeg_bin

# accurate rounding + full chroma: unbiased YUV<->RGB conversions (default flags darken by ~0.5 level)
SWS = "bicubic+accurate_rnd+full_chroma_int+full_chroma_inp"


def decode(source: str, W: int, H: int, fps: float, first: int, total: int) -> Iterator[np.ndarray]:
    """Yield `total` RGB frames (H,W,3 uint8) starting at output-grid frame `first`.
    Frame numbering matches the render (same fps + scale/pad). Short previews seek directly
    (fast, may be off by one frame); longer ranges decode from 0 so numbering is exact."""
    seek_args, trim_from = (["-ss", f"{first / fps:.6f}"], 0) if total <= 16 and first > 0 else ([], first)
    p = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", *seek_args, "-i", source, "-sws_flags", SWS, "-vf",
                          f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bilinear,"
                          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,"
                          f"trim=start_frame={trim_from}:end_frame={trim_from + total},setpts=PTS-STARTPTS,"
                          f"scale=flags={SWS},format=rgb24",   # explicit, accurate YUV->RGB (the implicit one ignores -sws_flags)
                          "-fps_mode", "passthrough", "-frames:v", str(total), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
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


class Writer:
    """Pipe raw frames into a lossless FFV1 file; atomic rename on close()."""

    def __init__(self, path, W: int, H: int, fps: float, pix_in: str, pix_out: str):
        from pathlib import Path
        self.final = Path(path)
        self.tmp = self.final.with_suffix(".part.mkv")
        self.p = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{W}x{H}",
                                   "-r", f"{fps:g}", "-i", "-", "-c:v", "ffv1", "-pix_fmt", pix_out, str(self.tmp)],
                                  stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, arr: np.ndarray):
        self.p.stdin.write(np.ascontiguousarray(arr).tobytes())

    def close(self):
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("encode failed: " + self.p.stderr.read().decode(errors="replace")[-300:])
        import os
        os.replace(self.tmp, self.final)

    def abort(self):
        try: self.p.kill()
        except Exception: pass
        self.tmp.unlink(missing_ok=True)
