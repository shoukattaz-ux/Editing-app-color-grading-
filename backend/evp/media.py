"""ffprobe wrapper and hardware detection."""
from __future__ import annotations
import json, os, shutil, subprocess, functools
from pathlib import Path
from fractions import Fraction

from .blueprint import VideoInfo


class MediaError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("EVP_FFMPEG") or shutil.which("ffmpeg") or _fail("ffmpeg not found on PATH (install FFmpeg >= 5 or set EVP_FFMPEG)")


def ffprobe_bin() -> str:
    return os.environ.get("EVP_FFPROBE") or shutil.which("ffprobe") or _fail("ffprobe not found on PATH")


def _fail(msg):
    raise MediaError(msg)


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def probe(path: str | Path) -> VideoInfo:
    p = Path(path)
    if not p.is_file():
        raise MediaError(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXT:
        raise MediaError(f"Unsupported container '{p.suffix}'. Supported: {', '.join(sorted(VIDEO_EXT))}")
    try:
        r = subprocess.run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise MediaError("ffprobe timed out (corrupted file?)")
    if r.returncode != 0:
        raise MediaError(f"Corrupted or unreadable media: {r.stderr.strip()[:300]}")
    info = json.loads(r.stdout)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0]
    if not vs:
        raise MediaError("File contains no video stream")
    v = vs[0]
    try:
        fps = float(Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"))
    except (ZeroDivisionError, ValueError):
        fps = 0.0
    if fps <= 0:
        try: fps = float(Fraction(v.get("r_frame_rate", "30/1")))
        except Exception: fps = 30.0
    dur = float(v.get("duration") or info.get("format", {}).get("duration") or 0)
    if dur <= 0:
        raise MediaError("Could not determine video duration")
    w, h = int(v["width"]), int(v["height"])
    # phones store portrait video as landscape + a rotation flag; FFmpeg auto-rotates on decode, so report display size
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            try: rot = int(float(sd["rotation"]))
            except (TypeError, ValueError): pass
    if not rot and "rotate" in (v.get("tags") or {}):
        try: rot = int(v["tags"]["rotate"])
        except ValueError: pass
    if abs(rot) % 180 == 90:
        w, h = h, w
    return VideoInfo(duration=dur, fps=round(fps, 3), width=w, height=h,
                     has_audio=any(s.get("codec_type") == "audio" for s in info["streams"]),
                     codec=v.get("codec_name", ""))


@functools.lru_cache(maxsize=1)
def ffmpeg_filters() -> dict[str, str]:
    """name -> flags (e.g. 'TSC'); 'T' = supports timeline `enable`."""
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 3:
            res[parts[1]] = parts[0]
    return res


def supports_timeline(name: str) -> bool:
    return "T" in ffmpeg_filters().get(name, "")


@functools.lru_cache(maxsize=1)
def encoders() -> set[str]:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return {l.split()[1] for l in out.splitlines() if len(l.split()) > 2 and l.startswith(" V")}


def hardware_report() -> dict:
    """CPU + usable hardware encoders (a 1-frame test encode proves the device actually works)."""
    hw = []
    for name in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_videotoolbox", "hevc_videotoolbox"):
        if name in encoders() and _try_encoder(name):
            hw.append(name)
    return {"cpu_count": os.cpu_count(), "ffmpeg": subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True).stdout.splitlines()[0],
            "encoders_available": sorted(e for e in encoders() if e in ("libx264", "libx265")),
            "hardware_encoders_working": hw}


@functools.lru_cache(maxsize=16)
def _try_encoder(name: str) -> bool:
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "2",
                        "-c:v", name, "-f", "null", "-"], capture_output=True, timeout=30)
    return r.returncode == 0
