"""AI face restoration (GFPGAN v1.4): sharp eyes, hair, beard and skin detail for small, soft or noisy faces.

Per frame: faces are found and tracked (YuNet, as for the other face tools), each face is aligned to the standard 512 px template from its
5 landmarks, restored by the network (CPU / GPU via ONNX Runtime), combined with the original according to `identity`, and warped back.
Two cached lossless-ish files are written, both at the output geometry:
   layer  RGB  - the frame with the restored faces pasted in
   mask   gray - where (and how strongly: x strength) the layer replaces the frame; a soft face-shaped feather, 0 elsewhere
The compiler then does  out = frame + (layer - frame) x mask  inside the op's range, so everything outside the faces is exactly as before.
"""
from __future__ import annotations
from .. import fsutil
from .. import proc as _proc
import hashlib, json, os, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .. import edl as _edl
from ..animate import op_params_at, keyframes_key
from ..media import ffmpeg_bin
from .face import FaceTracker
from .frames import decode
from .models import ensure_model, models_dir, make_session, make_session_cpu

SIZE = 512
TEMPLATE = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935], [201.26117, 371.41043], [313.08905, 371.15118]], np.float32)
BORDER = (135, 133, 132)
DETAIL_SIGMA = 5.0            # px at 512: what counts as "fine detail" when identity keeps the original's colours and shape
REUSE_MAX = 6                 # a nearly still face reuses the previous result for at most this many frames in a row


class Cancelled(Exception):
    pass


@dataclass
class RestoreRef:
    path: str                 # RGB layer
    mask_path: str            # gray mask (strength baked in)
    start_frame: int
    frames: int
    faces_found: int = 0      # frames where at least one face was restored
    gpu_fallback: str | None = None


_SESS: dict = {}
_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
from collections import OrderedDict as _OD
_NET_CACHE: "_OD[bytes, np.ndarray]" = _OD()
_NET_LOCK = threading.Lock()


ONNX_MODELS = ("gfpgan", "gpen512", "codeformer")


def onnx_path(model: str = "gfpgan") -> Path:
    if model in ("gpen512", "codeformer"): return ensure_model(model)          # these ship as ONNX files already
    p = models_dir() / "gfpgan_v1.4.onnx"
    if not p.is_file():
        with _BUILD_LOCK:
            if p.is_file(): return p
            from .legacy_pth import load_state_dict
            from .gfpgan import build_onnx
            build_onnx(load_state_dict(ensure_model("gfpgan")), p)
    return p


def session(model: str = "gfpgan"):
    p = onnx_path(model)
    with _LOCK:
        if str(p) not in _SESS: _SESS[str(p)] = make_session(p)
        return _SESS[str(p)]


def align(lm: np.ndarray) -> np.ndarray:
    """2x3 similarity transform from the frame to the 512 px template (5 landmarks: eyes, nose, mouth corners)"""
    M, _ = cv2.estimateAffinePartial2D(np.asarray(lm, np.float32), TEMPLATE, method=cv2.LMEDS)
    return M


def face_mask() -> np.ndarray:
    """soft face-shaped feather in the aligned 512 space: forehead / hair to chin, not the shirt below"""
    m = np.zeros((SIZE, SIZE), np.float32)
    cv2.ellipse(m, (258, 272), (190, 215), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    return cv2.GaussianBlur(m, (0, 0), 22)


_FACE_MASK = face_mask()


def mix_identity(orig: np.ndarray, rest: np.ndarray, identity: float) -> np.ndarray:
    """identity 0 = the network's face as it is; 1 = only its FINE DETAIL added to the original's own colours and shape (float rgb 0..1, 512x512)"""
    if identity <= 0.001: return rest
    lp = lambda a: cv2.GaussianBlur(a, (0, 0), DETAIL_SIGMA)
    detail_only = lp(orig) + (rest - lp(rest))
    return (1 - identity) * rest + identity * detail_only


class FaceRestorer:
    def __init__(self, W: int, H: int, p: dict, sess=None, runner: Callable[[np.ndarray], np.ndarray] | None = None):
        self.W, self.H = W, H
        self.tracker = FaceTracker(W, H, float(p["detection_confidence"]), hold=2, smooth=0.5)
        self.sess = sess
        self.runner = runner or self._run                  # tests give their own network
        self.fell_back: str | None = None
        self.frames_with_faces = 0
        self.model_path: Path | None = None
        self.model = str(p.get("model", "gfpgan"))
        self.fidelity = float(p.get("fidelity", 0.6))

    def _feed(self, x: np.ndarray, sess) -> np.ndarray:
        if self.model == "codeformer":                        # CodeFormer also takes its fidelity weight (a float64 scalar) and returns three tensors
            return sess.run(["output"], {"input": x, "weight": np.array(self.fidelity, dtype=np.float64)})[0]
        return sess.run(None, {"input": x})[0]

    def _run(self, x: np.ndarray) -> np.ndarray:
        sess = self.sess
        try:
            return self._feed(x, sess)
        except Exception as e:                              # GPU out of memory / driver error -> carry on with the processor
            if sess.get_providers()[0] == "CPUExecutionProvider": raise
            self.sess = make_session_cpu(self.model_path or onnx_path(self.model))
            self.fell_back = str(e)[:200]
            return self._feed(x, self.sess)

    def net(self, crop_rgb01: np.ndarray) -> np.ndarray:
        # the network's answer depends only on the aligned face crop, not on the sliders: dragging Strength / Identity at the same playhead reuses it
        key = hashlib.blake2b(np.ascontiguousarray(crop_rgb01).tobytes() + f"|{self.model}|{self.fidelity:.3f}".encode(), digest_size=16).digest()
        with _NET_LOCK:
            if key in _NET_CACHE:
                _NET_CACHE.move_to_end(key); return _NET_CACHE[key]
        x = ((crop_rgb01 - 0.5) / 0.5).transpose(2, 0, 1)[None].astype(np.float32)
        y = self.runner(np.ascontiguousarray(x))[0]
        out = np.clip(y.transpose(1, 2, 0) * 0.5 + 0.5, 0, 1)
        with _NET_LOCK:
            _NET_CACHE[key] = out
            while len(_NET_CACHE) > 12: _NET_CACHE.popitem(last=False)             # 12 faces x 3 MB
        return out

    def __call__(self, rgb: np.ndarray, p: dict, prime_only: bool = False) -> tuple[np.ndarray, np.ndarray]:
        """rgb uint8 HxWx3 -> (layer rgb uint8, mask uint8): both at the frame size.
        prime_only: a warm-up frame of a live preview - only the face tracker is updated (no network run), the result is not used."""
        H, W = rgb.shape[:2]
        strength, identity = float(p["strength"]), float(p["identity"])
        layer = rgb.copy()
        mask = np.zeros((H, W), np.float32)
        faces = self.tracker.update(rgb[:, :, ::-1].copy())
        if prime_only: return layer, np.zeros((H, W), np.uint8)
        faces = [(t, v) for t, v in faces if t.missed == 0 and t.box[2] >= float(p["min_face"]) and t.score >= float(p["detection_confidence"])]
        faces.sort(key=lambda tv: -tv[0].box[2] * tv[0].box[3])
        base = rgb.astype(np.float32) / 255.0
        out = base.copy()
        done = 0
        for t, _ in faces[:int(p["max_faces"])]:
            M = align(t.lm)
            if M is None: continue
            crop = cv2.warpAffine(rgb, M, (SIZE, SIZE), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=BORDER)
            small = cv2.cvtColor(cv2.resize(crop, (48, 48), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
            prev = getattr(t, "_prev", None)
            if prev is not None and prev[3] < REUSE_MAX and np.abs(prev[0] - small).mean() < 1.2 and np.abs(prev[1] - M).max() < 0.6:
                restored = prev[2]; reused = prev[3] + 1                    # a nearly still face: the same result again (saves time, and cannot shimmer)
            else:
                restored = self.net(crop.astype(np.float32) / 255.0); reused = 0
            t._prev = (small, M, restored, reused)
            mixed = mix_identity(crop.astype(np.float32) / 255.0, restored, identity)
            inv = cv2.invertAffineTransform(M)
            warped = cv2.warpAffine(mixed, inv, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            m = cv2.warpAffine(_FACE_MASK, inv, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            m = np.clip(m, 0, 1)
            out = out * (1 - m[..., None]) + warped * m[..., None]          # (several faces: later ones go on top; their masks hardly overlap)
            mask = np.maximum(mask, m)
            done += 1
        if done: self.frames_with_faces += 1
        layer = (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
        return layer, (np.clip(mask * strength, 0, 1) * 255 + 0.5).astype(np.uint8)


def restore_key(source: str, op, W, H, fps, start_frame, frames, edl=None) -> str:
    st = os.stat(source)
    d = [os.path.abspath(source), st.st_size, st.st_mtime_ns, W, H, round(fps, 4), start_frame, frames, sorted(op.params.items()), keyframes_key(op), "rs1", _edl.key(edl)]
    return hashlib.sha1(json.dumps(d, default=str).encode()).hexdigest()[:20]


def _paths(cache_dir: str, key: str) -> tuple[Path, Path]:
    d = Path(cache_dir) / "restore"
    return d / f"rs_{key}.mkv", d / f"rs_{key}_mask.mkv"


def generate_restore(source: str, op, W: int, H: int, fps: float, start_frame: int, frames: int, cache_dir: str,
                     progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
                     warmup: int = 0, edl: list | None = None) -> RestoreRef:
    out, _ = _paths(cache_dir, restore_key(source, op, W, H, fps, start_frame, frames, edl))
    with fsutil.exclusive(out):                                   # the same result requested twice at once: the second waits and reuses it
        return _generate(source, op, W, H, fps, start_frame, frames, cache_dir, progress, cancel, warmup, edl)


def _generate(source, op, W, H, fps, start_frame, frames, cache_dir, progress, cancel, warmup, edl) -> RestoreRef:
    import subprocess
    out, mout = _paths(cache_dir, restore_key(source, op, W, H, fps, start_frame, frames, edl))
    if out.is_file() and mout.is_file():
        sd = json.loads(out.with_suffix(".json").read_text(encoding="utf-8")) if out.with_suffix(".json").is_file() else {}
        return RestoreRef(str(out), str(mout), start_frame, frames, sd.get("faces", 0), sd.get("gpu_fallback"))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp, mtmp = out.with_suffix(".part.mkv"), mout.with_suffix(".part.mkv")
    model = str(op.params.get("model", "gfpgan"))
    sess = session(model)
    rs = FaceRestorer(W, H, op.params, sess)
    rs.model_path = onnx_path(model)
    enc = _proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps:g}", "-i", "-",
                       "-c:v", "libx264rgb", "-crf", "8", "-preset", "veryfast", str(tmp)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    menc = _proc.Popen([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", f"{fps:g}", "-i", "-",
                        "-c:v", "ffv1", "-pix_fmt", "gray", str(mtmp)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    first = max(0, start_frame - warmup)
    warm = start_frame - first
    n = 0
    try:
        for rgb in decode(source, W, H, fps, first, frames + warm, edl):
            if cancel is not None and cancel.is_set(): raise Cancelled()
            layer, mask = rs(rgb, op_params_at(op, fps, first + n) if op.keyframes else op.params, prime_only=n < warm)
            if n < warm: n += 1; continue                    # priming frames for the face tracker (previews only): no network run
            enc.stdin.write(layer.tobytes()); menc.stdin.write(mask.tobytes())
            n += 1
            if progress: progress(n - warm, frames)
        for e in (enc, menc):
            e.stdin.close()
            if e.wait() != 0: raise RuntimeError("face restoration encode failed: " + e.stderr.read().decode(errors="replace")[-300:])
        if n - warm <= 0: raise RuntimeError("face restoration decoded no frames in this range")
        fsutil.replace(tmp, out); fsutil.replace(mtmp, mout)
        out.with_suffix(".json").write_text(json.dumps({"faces": rs.frames_with_faces, "gpu_fallback": rs.fell_back}), encoding="utf-8")
        return RestoreRef(str(out), str(mout), start_frame, n - warm, rs.frames_with_faces, rs.fell_back)
    except BaseException:
        for e in (enc, menc):
            try: e.kill()
            except Exception: pass
        fsutil.unlink(tmp); fsutil.unlink(mtmp)
        raise


CPU_SECONDS = {"gfpgan": 1.3, "gpen512": 2.2, "codeformer": 2.7}      # per face on a 4-thread machine (measured)


def estimate_seconds_per_frame(gpu: bool, faces: int = 1, model: str = "gfpgan") -> float:
    """CPU: measured per face on a 4-thread machine; a graphics card is assumed ~10x faster (not measured here)"""
    return (CPU_SECONDS.get(model, 1.3) * (0.1 if gpu else 1.0)) * max(1, faces)
