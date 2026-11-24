"""Which device runs the AI models.

Windows: DirectML drives ANY DirectX-12 graphics chip (AMD, Intel, NVIDIA). Computers often have two - a weak integrated one and a real
card - and DirectML's default is "adapter 0", which is frequently the weak one. The adapters are therefore listed (DXGI, via ctypes: no
extra package) and the one with the most dedicated video memory is used unless the user chose otherwise (Settings > AI device).
Other systems: CUDA (NVIDIA) or CoreML (Mac) when the installed ONNX Runtime has it, otherwise the CPU.
"""
from __future__ import annotations
import json, os, sys, threading
from pathlib import Path

_CFG: dict = {"device": "auto"}          # "auto" | "cpu" | adapter index as a string
_LOCK = threading.Lock()
_CFG_FILE: Path | None = None
_ACTUAL: dict = {"provider": None}        # what the most recent session really got (the GPU provider can refuse and silently fall back)


def note_session(sess) -> None:
    try: _ACTUAL["provider"] = sess.get_providers()[0].replace("ExecutionProvider", "")
    except Exception: pass


# ---------------------------------------------------------------- Windows adapters (DXGI)
def adapters() -> list[dict]:
    """[{index, name, vram_mb, vendor, software}] in DXGI order (= DirectML device_id). Empty off Windows or on any failure."""
    if sys.platform != "win32": return []
    try:
        import ctypes
        from ctypes import POINTER, Structure, byref, c_void_p, c_uint, c_size_t, c_wchar, c_long, WINFUNCTYPE

        class GUID(Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

        class LUID(Structure):
            _fields_ = [("lo", ctypes.c_uint32), ("hi", c_long)]

        class DESC1(Structure):
            _fields_ = [("Description", c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint), ("SubSysId", c_uint), ("Revision", c_uint),
                        ("DedicatedVideoMemory", c_size_t), ("DedicatedSystemMemory", c_size_t), ("SharedSystemMemory", c_size_t),
                        ("Luid", LUID), ("Flags", c_uint)]

        def guid(s: str) -> GUID:
            import uuid
            u = uuid.UUID(s); g = GUID()
            g.a, g.b, g.c = u.time_low, u.time_mid, u.time_hi_version
            for i, v in enumerate(u.bytes[8:]): g.d[i] = v
            return g

        def vcall(obj: c_void_p, index: int, restype, argtypes, *args):
            vtbl = ctypes.cast(ctypes.cast(obj, POINTER(c_void_p))[0], POINTER(c_void_p))
            fn = WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
            return fn(obj, *args)

        factory = c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(byref(guid("770aae78-f26f-4dba-a829-253c83d1b387")), byref(factory)) != 0: return []
        out = []
        for i in range(16):
            ad = c_void_p()
            if vcall(factory, 12, c_long, [c_uint, POINTER(c_void_p)], i, byref(ad)) != 0: break          # IDXGIFactory1::EnumAdapters1
            d = DESC1()
            if vcall(ad, 10, c_long, [POINTER(DESC1)], byref(d)) == 0:                                       # IDXGIAdapter1::GetDesc1
                out.append({"index": i, "name": d.Description.strip(), "vram_mb": int(d.DedicatedVideoMemory // (1024 * 1024)),
                            "vendor": d.VendorId, "software": bool(d.Flags & 2) or d.VendorId == 0x1414})   # 0x1414 = Microsoft (WARP / basic render)
            vcall(ad, 2, c_long, [])                                                                          # Release
        vcall(factory, 2, c_long, [])
        return out
    except Exception:
        return []


def best_adapter(ads: list[dict]) -> dict | None:
    real = [a for a in ads if not a["software"]]
    return max(real, key=lambda a: (a["vram_mb"], -a["index"])) if real else None


# ---------------------------------------------------------------- setting
def configure(data_dir: Path) -> None:
    """remember the user's choice in <data dir>/ai_device.json; EVP_AI_DEVICE overrides it"""
    global _CFG_FILE
    _CFG_FILE = Path(data_dir) / "ai_device.json"
    try: _CFG.update(json.loads(_CFG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError): pass


def get_choice() -> str:
    return str(os.environ.get("EVP_AI_DEVICE") or _CFG.get("device") or "auto").lower()


def set_choice(choice: str) -> None:
    choice = str(choice).lower()
    if choice not in ("auto", "cpu") and not choice.isdigit(): raise ValueError("device must be 'auto', 'cpu' or an adapter number")
    with _LOCK:
        _CFG["device"] = choice
        _ACTUAL["provider"] = None
        if _CFG_FILE:
            try: _CFG_FILE.parent.mkdir(parents=True, exist_ok=True); _CFG_FILE.write_text(json.dumps(_CFG), encoding="utf-8")
            except OSError: pass
    try:                                                    # sessions are built lazily: drop the cached ones so the new choice takes effect
        from . import matting, upscale
        matting.drop_model()
        upscale._SESS.clear()
    except Exception: pass


# ---------------------------------------------------------------- providers
def _available() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return []


def provider_chain() -> list:
    """ONNX Runtime provider list in preference order; entries are names or (name, options). Always ends with the CPU."""
    avail, choice = _available(), get_choice()
    chain: list = []
    if choice != "cpu":
        if "CUDAExecutionProvider" in avail: chain.append("CUDAExecutionProvider")
        if "DmlExecutionProvider" in avail:
            ads = adapters()
            if choice.isdigit(): idx = int(choice)
            else:
                b = best_adapter(ads); idx = b["index"] if b else 0
            chain.append(("DmlExecutionProvider", {"device_id": idx}))
        if "CoreMLExecutionProvider" in avail: chain.append("CoreMLExecutionProvider")
    return chain + ["CPUExecutionProvider"]


def provider_names() -> list[str]:
    return [c if isinstance(c, str) else c[0] for c in provider_chain()]


def session_options(threads: int | None = None):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads or os.cpu_count() or 4
    if "DmlExecutionProvider" in provider_names()[:1]:      # required by the DirectML provider
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return so


def describe() -> dict:
    """for the UI: what will run the AI, and what the user can choose from"""
    ads = adapters()
    names = provider_names()
    first = names[0].replace("ExecutionProvider", "")
    used = None
    if first == "Dml":
        idx = provider_chain()[0][1]["device_id"]
        used = next((a["name"] for a in ads if a["index"] == idx), f"adapter {idx}")
    actual = _ACTUAL["provider"]
    fell_back = bool(actual) and first != "CPU" and actual == "CPU"          # wanted a GPU, the last session ended up on the processor
    return {"choice": get_choice(), "running_on": ("the processor" if fell_back else (used or first)), "provider": "CPU" if fell_back else first,
            "wanted": used or first, "actual": actual, "fell_back": fell_back,
            "options": [{"value": "auto", "label": "Automatic (the strongest graphics card)"}]
                       + [{"value": str(a["index"]), "label": f"{a['name']} ({a['vram_mb']} MB)" + (" - software" if a["software"] else "")} for a in ads]
                       + [{"value": "cpu", "label": "Processor only (slowest, always works)"}]}
