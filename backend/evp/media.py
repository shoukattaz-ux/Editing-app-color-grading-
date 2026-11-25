"""ffprobe wrapper and hardware detection."""
from __future__ import annotations
from . import proc
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
        r = proc.run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
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
                     codec=v.get("codec_name", ""),
                     color_space=v.get("color_space", "") or "",
                     color_range=("pc" if str(v.get("pix_fmt", "")).startswith("yuvj") else (v.get("color_range", "") or "")))


@functools.lru_cache(maxsize=1)
def ffmpeg_filters() -> dict[str, str]:
    """name -> flags (e.g. 'TSC'); 'T' = supports timeline `enable`."""
    out = proc.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
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
    out = proc.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return {l.split()[1] for l in out.splitlines() if len(l.split()) > 2 and l.startswith(" V")}


def hardware_report() -> dict:
    """CPU + usable hardware encoders (a 1-frame test encode proves the device actually works)."""
    hw = []
    for name in ("h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv", "h264_videotoolbox", "hevc_videotoolbox"):
        if name in encoders() and _try_encoder(name):
            hw.append(name)
    return {"cpu_count": os.cpu_count(), "ffmpeg": proc.run([ffmpeg_bin(), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()[0],
            "encoders_available": sorted(e for e in encoders() if e in ("libx264", "libx265")),
            "hardware_encoders_working": hw,
            "ai_devices": _ai_devices(), "ai": _ai_describe()}


def _ai_describe() -> dict:
    try:
        from .ml.device import describe
        return describe()
    except Exception:
        return {}


def _ai_devices() -> list[str]:
    try:
        from .ml.models import providers
        return [p.replace("ExecutionProvider", "") for p in providers()]
    except Exception:
        return []


@functools.lru_cache(maxsize=16)
def _try_encoder(name: str) -> bool:
    r = proc.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "2",
                        "-c:v", name, "-f", "null", "-"], capture_output=True, timeout=30)
    return r.returncode == 0


# ---------------------------------------------------------------- why is (or is not) the graphics card used?
@functools.lru_cache(maxsize=16)
def encoder_error(name: str) -> str:
    """the last line FFmpeg prints when a 1-frame test encode with `name` fails ('' when it works)"""
    r = proc.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "2", "-c:v", name, "-f", "null", "-"],
                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if r.returncode == 0: return ""
    lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip()]
    return lines[-1][:300] if lines else f"exit code {r.returncode}"


@functools.lru_cache(maxsize=1)
def hwaccels() -> tuple[str, ...]:
    """hardware video decoders this FFmpeg build knows (d3d11va, dxva2, cuda, qsv ...)"""
    out = proc.run([ffmpeg_bin(), "-hide_banner", "-hwaccels"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return tuple(l.strip() for l in out.splitlines()[1:] if l.strip())


def hw_decode_usable() -> bool:
    """hardware decoding of the SOURCE for renders: Windows only (d3d11va / dxva2 run on AMD, NVIDIA and Intel); the decoded frames come back to memory, so the rest of the pipeline is unchanged"""
    import sys
    return sys.platform == "win32" and os.environ.get("EVP_HWDECODE", "1") != "0" and any(h in hwaccels() for h in ("d3d11va", "dxva2"))


def gpu_diagnosis(ai: dict | None = None, adapters: list[dict] | None = None, onnx_providers: list[str] | None = None) -> list[dict]:
    """a checklist for the GPU panel: what really runs on the graphics card, what does not, and the exact fix for each problem.
    [{topic, status: ok|warn|info, text, fix}]  (the three arguments are injectable for tests)"""
    import sys
    from .ml import device
    ai = ai if ai is not None else device.describe()
    ads = adapters if adapters is not None else device.adapters()
    prov = onnx_providers if onnx_providers is not None else device._available()
    out: list[dict] = []
    real = [a for a in ads if not a.get("software")]
    if sys.platform == "win32":
        if real:
            out.append({"topic": "Graphics cards", "status": "ok", "text": "; ".join(f"{a['name']} ({a['vram_mb']} MB)" + (" - AMD" if a["vendor"] == 0x1002 else " - NVIDIA" if a["vendor"] == 0x10DE else " - Intel" if a["vendor"] == 0x8086 else "") for a in real)})
        else:
            out.append({"topic": "Graphics cards", "status": "warn", "text": "Windows reports no hardware graphics adapter.", "fix": "Install or update the graphics driver (AMD: Adrenalin from amd.com), then restart."})
    # AI models
    if sys.platform == "win32" and "DmlExecutionProvider" not in prov:
        out.append({"topic": "AI (keying, hair, upscaling)", "status": "warn", "text": "The DirectML build of ONNX Runtime is not installed, so the AI runs on the processor.",
                    "fix": "Run scripts\\dev.ps1 (it installs it), or: pip uninstall -y onnxruntime onnxruntime-directml && pip install onnxruntime-directml"})
    elif ai.get("fell_back"):
        out.append({"topic": "AI (keying, hair, upscaling)", "status": "warn", "text": "The graphics card refused the AI model and it ran on the processor.", "fix": "Update the graphics driver; Settings > AI device lets you pick another card."})
    else:
        out.append({"topic": "AI (keying, hair, upscaling)", "status": "ok" if ai.get("provider") not in (None, "CPU") else "info", "text": f"Runs on {ai.get('running_on', 'the processor')}."})
    # encoder
    enc = encoders()
    amf = [n for n in ("h264_amf", "hevc_amf") if n in enc]
    working = [n for n in ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox") if n in enc and _try_encoder(n)]
    if working:
        out.append({"topic": "Video encoding (export / preview video)", "status": "ok", "text": f"Hardware encoder {working[0]} works and is used automatically."})
    elif amf:
        err = encoder_error(amf[0])
        fix = ("Update the AMD Radeon driver (Adrenalin); the AMF runtime DLL could not be loaded." if "dll" in err.lower() or "amf" in err.lower() else "Update the AMD Radeon driver (Adrenalin).")
        out.append({"topic": "Video encoding (export / preview video)", "status": "warn", "text": f"{amf[0]} exists in FFmpeg but the test encode failed: {err}", "fix": fix})
    else:
        out.append({"topic": "Video encoding (export / preview video)", "status": "warn" if real else "info", "text": "This FFmpeg build has no hardware encoder (no h264_amf / nvenc / qsv), so exports use the processor.",
                    "fix": "Use a full FFmpeg build (for example gyan.dev 'full' or BtbN 'win64-gpl') and put it on PATH or in the app's tools folder."})
    # decoding
    if sys.platform == "win32":
        ok = hw_decode_usable()
        out.append({"topic": "Video decoding (source file)", "status": "ok" if ok else "info",
                    "text": "Exports and preview videos decode the source on the graphics card (d3d11va / dxva2); if that ever fails the render retries on the processor." if ok else "This FFmpeg build has no d3d11va / dxva2 hardware decoding, so the source is decoded on the processor."})
    out.append({"topic": "Keying, colour grading, compositing", "status": "info",
                "text": "These run on the processor (FFmpeg filters). That is why the graphics card can look quiet while they work: only the AI models, encoding and decoding use it. They cannot be moved to a graphics card without losing features."})
    return out
