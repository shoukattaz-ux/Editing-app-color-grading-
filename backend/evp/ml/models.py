"""Model catalogue + verified, atomic download into the local model store."""
from __future__ import annotations
import hashlib, os, shutil, tempfile, urllib.request
from dataclasses import dataclass
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
    tmp = Path(tempfile.mkstemp(dir=models_dir(), suffix=".part")[1])
    try:
        with urllib.request.urlopen(info.url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        if _sha256(tmp) != info.sha256:
            raise ModelError(f"Downloaded model '{name}' failed its SHA-256 check; not used")
        os.replace(tmp, p)
    except ModelError:
        raise
    except Exception as e:
        raise ModelError(f"Could not download model '{name}' from {info.url}: {e}. "
                         f"Download it manually into {models_dir()}") from e
    finally:
        tmp.unlink(missing_ok=True)
    return p
