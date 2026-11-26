"""Model catalogue + verified, atomic download into the local model store."""
from __future__ import annotations
import hashlib, os, shutil, tempfile, threading, urllib.request
from dataclasses import dataclass

from .. import fsutil
from pathlib import Path


@dataclass(frozen=True)
class ModelInfo:
    name: str
    file: str
    url: str
    sha256: str
    size: int
    license: str
    description: str


MODELS = {
    "rvm": ModelInfo("rvm", "rvm_mobilenetv3_fp32.onnx",
                     "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx",
                     "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828", 14975696, "GPL-3.0",
                     "Robust Video Matting (MobileNetV3) - temporally consistent human matting with hair detail"),
    "esrgan_general": ModelInfo("esrgan_general", "realesr-general-x4v3.pth",
                                "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
                                "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292", 4885111, "BSD-3-Clause",
                                "Real-ESRGAN general x4 v3 (SRVGGNetCompact) - fast real-world super-resolution"),
    "esrgan_wdn": ModelInfo("esrgan_wdn", "realesr-general-wdn-x4v3.pth",
                            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
                            "1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca", 4885111, "BSD-3-Clause",
                            "Real-ESRGAN general x4 v3, with-denoise variant (used for the denoise slider)"),
    "esrgan_x4plus": ModelInfo("esrgan_x4plus", "RealESRGAN_x4plus.pth",
                               "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
                               "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1", 67040989, "BSD-3-Clause",
                               "Real-ESRGAN x4plus (RRDBNet) - highest quality, ~12x slower than the compact model"),
    "esrgan_x2plus": ModelInfo("esrgan_x2plus", "RealESRGAN_x2plus.pth",
                               "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
                               "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb", 67061725, "BSD-3-Clause",
                               "Real-ESRGAN x2plus (RRDBNet) - native 2x model, high quality"),
    "esrgan_anime": ModelInfo("esrgan_anime", "realesr-animevideov3.pth",
                              "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
                              "b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d", 2504012, "BSD-3-Clause",
                              "Real-ESRGAN anime video v3 (compact) - cartoons, anime, flat-shaded graphics"),
    "gfpgan": ModelInfo("gfpgan", "GFPGANv1.4.pth",
                        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth",
                        "e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad", 348632874, "Apache-2.0",
                        "GFPGAN v1.4 (TencentARC) - blind face restoration: rebuilds sharp eyes, hair, beard and skin detail of small or soft faces"),
    "yunet": ModelInfo("yunet", "face_detection_yunet_2023mar.onnx",
                       "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                       "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232589, "MIT",
                       "YuNet face detector (OpenCV Zoo) - face boxes + 5 landmarks, ~10 ms/frame on CPU"),
}


class ModelError(RuntimeError):
    pass


def models_dir() -> Path:
    d = Path(os.environ.get("EVP_MODELS_DIR") or Path.home() / ".evp" / "models")
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_path(name: str) -> Path:
    return models_dir() / MODELS[name].file


def is_installed(name: str) -> bool:
    p = model_path(name)
    return p.is_file() and p.stat().st_size == MODELS[name].size


def runtime_available() -> tuple[bool, str]:
    try:
        import onnxruntime  # noqa: F401
        return True, ""
    except Exception as e:  # ImportError or broken install
        return False, f"onnxruntime is not installed ({e}). Run: pip install onnxruntime  (or onnxruntime-gpu)"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def ensure_model(name: str, allow_download: bool = True) -> Path:
    info = MODELS[name]
    p = model_path(name)
    if p.is_file() and p.stat().st_size == info.size:
        return p
    if not allow_download:
        raise ModelError(f"Model '{name}' is not installed")
    with _DOWNLOAD_LOCK:                                   # two requests (preview + proxy) must not download the same file twice
        if p.is_file() and p.stat().st_size == info.size:
            return p
        return _download(name, info, p)


_DOWNLOAD_LOCK = threading.Lock()


def _download(name: str, info: "ModelInfo", p: Path) -> Path:
    fd, tmp_name = tempfile.mkstemp(dir=models_dir(), suffix=".part")
    os.close(fd)                                           # mkstemp hands back an OPEN descriptor: left open, Windows cannot rename or delete the file
    tmp = Path(tmp_name)
    try:
        with urllib.request.urlopen(info.url, timeout=120) as r, open(tmp, "wb") as f:
            _PROGRESS[name] = {"done": 0, "total": int(r.headers.get("Content-Length") or info.size)}
            while True:
                chunk = r.read(1 << 20)
                if not chunk: break
                f.write(chunk)
                _PROGRESS[name]["done"] += len(chunk)
        if _sha256(tmp) != info.sha256:
            raise ModelError(f"Downloaded model '{name}' failed its SHA-256 check; not used")
        fsutil.replace(tmp, p)
    except ModelError:
        raise
    except Exception as e:
        raise ModelError(f"Could not download model '{name}' from {info.url}: {e}. "
                         f"Download it manually into {models_dir()}") from e
    finally:
        fsutil.unlink(tmp)
        _PROGRESS.pop(name, None)
    return p


# ---- installing a big model in the background, with progress (the UI polls status())
_PROGRESS: dict[str, dict] = {}
_INSTALLS: dict[str, dict] = {}


def status(name: str) -> dict:
    """{installed, downloading, done, total, error} for the UI"""
    info = MODELS[name]
    inst = _INSTALLS.get(name, {})
    prog = _PROGRESS.get(name, {})
    return {"name": name, "installed": is_installed(name), "size": info.size, "license": info.license,
            "downloading": bool(inst.get("running")), "done": prog.get("done", 0), "total": prog.get("total", info.size), "error": inst.get("error")}


def install_async(name: str) -> dict:
    """start downloading `name` in the background (does nothing if it is installed or already downloading)"""
    if name not in MODELS: raise ModelError(f"unknown model '{name}'")
    st = _INSTALLS.setdefault(name, {})
    if is_installed(name) or st.get("running"): return status(name)
    st.update(running=True, error=None)
    def work():
        try: ensure_model(name)
        except Exception as e: st["error"] = str(e)
        finally: st["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return status(name)


def providers() -> list[str]:
    """ONNX Runtime execution providers in preference order (the strongest device first), by name."""
    from . import device
    return device.provider_names()


def make_session(model_path: str, threads: int | None = None):
    """ONNX session on the chosen device; falls back to the CPU if the GPU provider fails to initialise."""
    import onnxruntime as ort
    from . import device
    try:
        sess = ort.InferenceSession(str(model_path), device.session_options(threads), providers=device.provider_chain())
    except Exception:
        sess = make_session_cpu(model_path, threads)
    device.note_session(sess)
    return sess


def make_session_cpu(model_path: str, threads: int | None = None):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads or os.cpu_count() or 4
    return ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
