"""Photo mode: a photo is edited with the very same engine as a video.

The photo is converted ONCE into a lossless one-second clip stored inside the project (the original file is never touched); every effect, the
live preview and the AI features then work unchanged. Saving renders that one frame at the output size and writes a PNG / JPEG / WebP.
Phone photos keep their orientation (EXIF rotation is applied when the picture is read).
"""
from __future__ import annotations
import hashlib, os, subprocess
from pathlib import Path

import cv2
import numpy as np

from . import proc
from .media import ffmpeg_bin, MediaError

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_SIDE = 8192            # larger pictures (a 100 MP panorama) are scaled down to this on their long side
FPS, SECONDS = 25, 1


def is_photo(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PHOTO_EXT


def read_photo(path: str | Path) -> np.ndarray:
    """BGR uint8, EXIF-rotated, transparency flattened onto white, even width / height, at most MAX_SIDE on the long side"""
    p = Path(path)
    if not p.is_file(): raise MediaError(f"File not found: {p}")
    data = np.fromfile(str(p), np.uint8)                          # (imread cannot open non-ASCII Windows paths; this can)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED if p.suffix.lower() in (".png", ".webp") else cv2.IMREAD_COLOR)
    if img is None: raise MediaError(f"This picture could not be read (damaged or not really a {p.suffix} file): {p.name}")
    if img.ndim == 2: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.dtype != np.uint8: img = np.clip(img.astype(np.float32) / (65535.0 if img.dtype == np.uint16 else 1.0) * 255.0, 0, 255).astype(np.uint8)
    if img.shape[2] == 4:                                         # transparent areas become white
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3] * a + 255 * (1 - a) + 0.5).astype(np.uint8)
    h, w = img.shape[:2]
    s = min(1.0, MAX_SIDE / max(w, h))
    if s < 1.0: img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA); h, w = img.shape[:2]
    return np.ascontiguousarray(img[: h // 2 * 2, : w // 2 * 2])


def ingest(path: str | Path, dest_dir: str | Path) -> tuple[Path, dict]:
    """photo -> lossless one-second clip in dest_dir; returns (clip path, {"original", "name", "width", "height"}). Reuses an earlier conversion of the same file."""
    p = Path(path).resolve()
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:8]
    dest = Path(dest_dir); dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{p.stem}_{key}.mkv"
    img = read_photo(p)
    h, w = img.shape[:2]
    info = {"original": str(p), "name": p.name, "width": w, "height": h}
    if out.is_file(): return out, info
    tmp = out.with_suffix(".part.mkv")
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-framerate", str(FPS), "-i", "-",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv444p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv",
           "-frames:v", str(FPS * SECONDS), "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p", str(tmp)]
    pr = proc.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        frame = img.tobytes()
        for _ in range(FPS * SECONDS): pr.stdin.write(frame)
        pr.stdin.close()
        if pr.wait() != 0: raise MediaError("Could not convert the picture: " + pr.stderr.read().decode(errors="replace")[-300:])
        os.replace(tmp, out)
    except BaseException:
        try: pr.kill()
        except Exception: pass
        try: tmp.unlink()
        except OSError: pass
        raise
    return out, info


FORMATS = {"png": ".png", "jpg": ".jpg", "webp": ".webp"}


def encode(png_bytes: bytes, fmt: str, quality: int = 95) -> bytes:
    """the rendered frame (PNG from the engine) as the file the user asked for"""
    fmt = fmt.lower()
    if fmt not in FORMATS: raise ValueError(f"unknown picture format '{fmt}' (png, jpg or webp)")
    if fmt == "png": return png_bytes
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    q = int(min(100, max(1, quality)))
    if fmt == "jpg":
        params = [cv2.IMWRITE_JPEG_QUALITY, q, cv2.IMWRITE_JPEG_OPTIMIZE, 1]
        if q >= 90 and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR"): params += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
        ok, buf = cv2.imencode(".jpg", img, params)
    else:
        ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, q])
    if not ok: raise RuntimeError(f"could not encode the picture as {fmt}")
    return buf.tobytes()


def unique_path(folder: Path, name: str, ext: str, forbid: set[str] | None = None) -> Path:
    """folder/name.ext, or name (2).ext ... - an existing file (or the original photo) is never overwritten"""
    out, n = folder / f"{name}{ext}", 1
    forbid = {os.path.normcase(str(Path(x).resolve())) for x in (forbid or set())}
    while out.exists() or os.path.normcase(str(out.resolve())) in forbid:
        n += 1; out = folder / f"{name} ({n}){ext}"
    return out
