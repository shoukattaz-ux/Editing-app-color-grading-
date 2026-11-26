"""The project's Media bin: every picture, video and LUT that was ever imported stays listed (and reusable) - independent of whether
the current blueprint still references it - plus the source videos the project has used.

Assets are copies inside <project>/assets/, so they are listed from that folder (undoing an edit never loses a file). Source videos are only
referenced (never copied or modified): the project remembers the paths it has used in project.json -> "sources_history".
"""
from __future__ import annotations
import re
import hashlib, json, os, time
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
LUT_EXT = {".cube", ".3dl"}
HISTORY_MAX = 30


def kind_of(p: Path) -> str | None:
    e = p.suffix.lower()
    return "image" if e in IMAGE_EXT else "video" if e in VIDEO_EXT else "lut" if e in LUT_EXT else None


def remember_source(project, path: str) -> None:
    """newest first, no duplicates (case-insensitive on Windows paths)"""
    key = os.path.normcase(os.path.abspath(path))
    hist = [h for h in project.data.get("sources_history", []) if os.path.normcase(os.path.abspath(h)) != key]
    project.data["sources_history"] = ([str(path)] + hist)[:HISTORY_MAX]


def forget_source(project, path: str) -> bool:
    """take a video out of the Media bin's list (only the reference: the file itself is never touched). False if it was not listed."""
    key = os.path.normcase(os.path.abspath(path))
    hist = list(project.data.get("sources_history", []))
    keep = [h for h in hist if os.path.normcase(os.path.abspath(h)) != key]
    project.data["sources_history"] = keep
    return len(keep) != len(hist)


def _meta_cache(project) -> tuple[Path, dict]:
    f = project.cache_dir / "media_meta.json"
    try: return f, json.loads(f.read_text(encoding="utf-8"))
    except Exception: return f, {}


def _measure(p: Path, kind: str) -> dict:
    """dimensions / duration; never raises (a damaged file is still listed)"""
    try:
        if kind == "video":
            from .media import probe
            v = probe(p)
            return {"width": v.width, "height": v.height, "duration": round(v.duration, 3), "fps": round(v.fps, 3), "has_audio": v.has_audio}
        if kind == "image":
            import cv2, numpy as np
            im = cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
            if im is not None: return {"width": int(im.shape[1]), "height": int(im.shape[0])}
    except Exception:
        pass
    return {}


def _describe(p: Path, kind: str, cache: dict, dirty: list) -> dict:
    try: stt = p.stat()
    except OSError: return {"exists": False}
    key = f"{p}|{stt.st_size}|{stt.st_mtime_ns}"
    meta = cache.get(key)
    if meta is None:
        meta = _measure(p, kind) if kind != "lut" else {}
        cache[key] = meta; dirty.append(1)
    return {"exists": True, "size": stt.st_size, "mtime": stt.st_mtime, **meta}


def scan(project, current_source: str | None) -> dict:
    f, cache = _meta_cache(project)
    dirty: list = []
    assets = []
    if project.assets_dir.is_dir():
        for p in sorted(project.assets_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            k = kind_of(p) if p.is_file() else None
            if k: assets.append({"path": f"assets/{p.name}", "name": p.name, "kind": k, **_describe(p, k, cache, dirty)})
    srcs, seen = [], set()
    order = ([current_source] if current_source else []) + list(project.data.get("sources_history", []))
    for s in order:
        if not s: continue
        key = os.path.normcase(os.path.abspath(s))
        if key in seen: continue
        seen.add(key)
        p = Path(s)
        shown = re.sub(r"_[0-9a-f]{8}\.mkv$", "", p.name) + " (photo)" if p.parent.name == "photo" and re.search(r"_[0-9a-f]{8}\.mkv$", p.name) else p.name
        srcs.append({"path": str(s), "name": shown, "kind": "video", "current": bool(current_source) and os.path.normcase(os.path.abspath(current_source)) == key,
                     **_describe(p, "video", cache, dirty)})
    if dirty:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return {"sources": srcs, "assets": assets}


def thumb_path(project, file: Path, width: int) -> Path:
    st = file.stat()
    h = hashlib.sha1(f"{file}|{st.st_size}|{st.st_mtime_ns}|{width}".encode()).hexdigest()[:20]
    return project.cache_dir / "thumbs" / f"{h}.png"


def make_thumb(project, file: Path, width: int = 192) -> Path | None:
    out = thumb_path(project, file, width)
    if out.is_file(): return out
    from . import proc
    from .media import ffmpeg_bin
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + f".{os.getpid()}.{int(time.time() * 1000) % 100000}.tmp.png")
    seek = ["-ss", "0.5"] if kind_of(file) == "video" else []
    for ss in (seek, []):                                  # a clip shorter than 0.5 s has no frame there: retry from the start
        r = proc.run([ffmpeg_bin(), "-v", "error", "-y", *ss, "-i", str(file), "-frames:v", "1", "-vf", f"scale={width}:-2:flags=bilinear", str(tmp)],
                     capture_output=True, timeout=30)
        if r.returncode == 0 and tmp.is_file(): break
    else:
        return None
    os.replace(tmp, out)
    return out
