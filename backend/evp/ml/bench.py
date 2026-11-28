"""Test speed: times the AI models on THIS computer (the graphics card it really uses, or the processor) so every estimate in the editor can use real numbers.

Measured: the general upscale network on a fixed test picture (-> a speed factor against the built-in CPU constants), and each face model that is installed
(seconds per face). The result is stored with the device it was measured on; estimates use it only while the same device is in use."""
from __future__ import annotations
import json, threading, time
from pathlib import Path

import numpy as np

from . import device
from .models import is_installed, models_dir, make_session_cpu

TEST_W, TEST_H = 480, 270                 # the upscale test picture (a 270p frame; the cost is per pixel)
_STATE: dict = {"state": "idle", "step": "", "result": None, "error": None}
_LOCK = threading.Lock()


def _file() -> Path:
    return models_dir().parent / "benchmark.json"


def _picture() -> np.ndarray:
    rng = np.random.default_rng(7)
    base = np.linspace(0, 255, TEST_W, dtype=np.float32)[None, :, None] * np.ones((TEST_H, 1, 3), np.float32)
    return np.clip(base + rng.normal(0, 18, (TEST_H, TEST_W, 3)), 0, 255).astype(np.uint8)          # a gradient with texture: the network does real work on it


def _best(fn, runs: int = 2) -> float:
    fn()                                                                                              # warm-up: model loading, first GPU kernels
    t = []
    for _ in range(runs):
        t0 = time.perf_counter(); fn(); t.append(time.perf_counter() - t0)
    return min(t)


def device_tag() -> str:
    d = device.describe()
    return f"{d['provider']}|{d['wanted']}|{d['choice']}"


def run(on_step=lambda s: None) -> dict:
    from . import upscale, restore
    res: dict = {"device": device_tag(), "running_on": device.describe()["running_on"], "created": time.time(), "faces": {}}
    on_step("Upscaling")
    sess = upscale.session("general", 0.0)
    img = _picture()
    secs = _best(lambda: upscale.run_net(sess, img, 4), 2)
    us = secs / (TEST_W * TEST_H) * 1e6
    res["upscale_us_per_px"] = us
    res["upscale_factor"] = us / upscale.UPSCALE_MODELS["general"][3]                                  # >1 = slower than the built-in CPU reference, <1 = faster
    d = device.describe()
    res["fell_back"] = bool(d["fell_back"])
    if d["provider"] != "CPU":                                                                         # how much the graphics card helps: the same test on the processor
        on_step("Upscaling on the processor (for comparison)")
        csess = make_session_cpu(upscale.onnx_path("general", 0.0))
        cs = _best(lambda: upscale.run_net(csess, img, 4), 1)
        res["cpu_us_per_px"] = cs / (TEST_W * TEST_H) * 1e6
        res["speedup"] = res["cpu_us_per_px"] / us
    x = (np.random.default_rng(3).random((1, 3, 512, 512)).astype(np.float32) * 2 - 1)
    for m in restore.ONNX_MODELS:
        if not is_installed(m): continue                                                               # a test must not trigger a 300 MB download
        on_step(f"Face AI: {m}")
        try:
            s = restore.session(m)
            r = restore.FaceRestorer(64, 64, {"model": m, "fidelity": 0.6, "detection_confidence": 0.6}, sess=s)
            res["faces"][m] = _best(lambda: r._run(x), 2)
        except Exception as e:                                                                         # one broken model must not hide the other numbers
            res["faces"][m] = None; res.setdefault("errors", {})[m] = str(e)[:200]
    _file().write_text(json.dumps(res), encoding="utf-8")
    return res


def start() -> dict:
    with _LOCK:
        if _STATE["state"] == "running": return dict(_STATE)
        _STATE.update(state="running", step="Starting", result=None, error=None)
        snap = dict(_STATE)                                                # what the caller sees: the run has just started (a very fast one may finish at once)
    def work():
        try:
            r = run(lambda s: _STATE.update(step=s))
            _STATE.update(state="done", step="", result=r)
        except Exception as e:
            _STATE.update(state="failed", step="", error=str(e)[:300])
    threading.Thread(target=work, daemon=True).start()
    return snap


def status() -> dict:
    s = dict(_STATE)
    if s["state"] == "idle":
        m = stored()
        if m: s.update(state="done", result=m)
    s["current_device"] = device_tag()
    if s.get("result"): s["valid"] = s["result"].get("device") == device_tag()                        # measured on another device: shown, but not used for estimates
    return s


def stored() -> dict | None:
    try: return json.loads(_file().read_text(encoding="utf-8"))
    except Exception: return None


def measured() -> dict | None:
    """the stored measurement, only if it was made on the device that is in use now"""
    m = stored()
    return m if m and m.get("device") == device_tag() else None
