"""Reader for the OLD (pre-1.6, non-zip) PyTorch checkpoint format, e.g. GFPGANv1.4.pth - without PyTorch.

Layout: pickle(magic) pickle(protocol) pickle(sys_info) pickle(<object, tensors stored as persistent ids>) pickle(<storage keys>)
        then, for each storage key in order: int64 element count + raw little-endian data.
Like srvgg.load_state_dict, it uses a RESTRICTED unpickler that can only build tensors and ordered dicts: a model file cannot run code.
"""
from __future__ import annotations
import pickle, struct
from collections import OrderedDict
from pathlib import Path

import numpy as np

_DTYPES = {"FloatStorage": np.float32, "HalfStorage": np.float16, "DoubleStorage": np.float64, "LongStorage": np.int64, "IntStorage": np.int32,
           "ByteStorage": np.uint8, "BoolStorage": np.bool_, "ShortStorage": np.int16}
MAGIC = 0x1950a86a20f9469cfc6c


class _Storage:
    def __init__(self, dtype, numel):
        self.dtype, self.numel, self.data = dtype, numel, None


def _rebuild_tensor(storage, offset, size, stride, *_):
    return ("tensor", storage, offset, tuple(size), tuple(stride))


class _Unpickler(pickle.Unpickler):
    def __init__(self, f):
        super().__init__(f)
        self.storages: dict[str, _Storage] = {}

    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"): return OrderedDict
        if module == "torch._utils" and name in ("_rebuild_tensor", "_rebuild_tensor_v2"): return _rebuild_tensor
        if module == "torch" and name in _DTYPES: return name
        raise pickle.UnpicklingError(f"refusing to load {module}.{name} from a model file")

    def persistent_load(self, pid):
        if pid[0] != "storage": raise pickle.UnpicklingError("unexpected persistent id in model file")
        _, stype, key, _loc, numel = pid[:5]
        if stype not in _DTYPES: raise pickle.UnpicklingError(f"unsupported storage type {stype}")
        return self.storages.setdefault(str(key), _Storage(_DTYPES[stype], int(numel)))


def is_legacy(path: str | Path) -> bool:
    with open(path, "rb") as f:
        try: return pickle.load(f) == MAGIC
        except Exception: return False


def load_state_dict(path: str | Path) -> dict[str, np.ndarray]:
    with open(path, "rb") as f:
        if pickle.load(f) != MAGIC: raise ValueError("not a legacy PyTorch checkpoint")
        pickle.load(f)                                           # protocol version
        pickle.load(f)                                           # system info
        up = _Unpickler(f)
        obj = up.load()
        keys = pickle.load(f)
        for k in keys:
            (numel,) = struct.unpack("<q", f.read(8))
            st = up.storages[str(k)]
            raw = f.read(numel * np.dtype(st.dtype).itemsize)
            if len(raw) != numel * np.dtype(st.dtype).itemsize: raise ValueError("truncated model file")
            st.data = np.frombuffer(raw, dtype=st.dtype)
    if isinstance(obj, dict) and "params_ema" in obj: obj = obj["params_ema"]
    elif isinstance(obj, dict) and "params" in obj: obj = obj["params"]
    out = {}
    for name, t in obj.items():
        _, st, off, size, stride = t
        if not size: arr = st.data[off:off + 1].reshape(())
        else:
            it = st.data.itemsize
            arr = np.lib.stride_tricks.as_strided(st.data[off:], shape=size, strides=tuple(s * it for s in stride))
        out[name] = np.array(arr, dtype=np.float32)
    return out
