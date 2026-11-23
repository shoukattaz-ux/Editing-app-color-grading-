"""Real-ESRGAN 'realesr-general-x4v3' (SRVGGNetCompact, BSD-3-Clause) -> ONNX, without PyTorch.

The official .pth is a PyTorch zip archive. It is read with a *restricted* unpickler that can only
rebuild float tensors (any other global -> error), so a tampered file cannot execute code.
Optional denoise: Real-ESRGAN's "DNI" interpolation between the general and the -wdn (with
denoise) weights:  w = d * wdn + (1 - d) * general.
"""
from __future__ import annotations
import io, pickle, zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np

_DTYPES = {"FloatStorage": np.float32, "HalfStorage": np.float16, "DoubleStorage": np.float64}


def _rebuild_tensor_v2(storage, offset, size, stride, requires_grad=False, hooks=None, metadata=None):
    if not size:
        return storage[offset:offset + 1].reshape(())
    it = storage.itemsize
    a = np.lib.stride_tricks.as_strided(storage[offset:], shape=tuple(size), strides=tuple(s * it for s in stride))
    return np.array(a, dtype=np.float32)


class _SafeUnpickler(pickle.Unpickler):
    def __init__(self, f, zf: zipfile.ZipFile, prefix: str):
        super().__init__(f)
        self.zf, self.prefix = zf, prefix

    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"): return OrderedDict
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"): return _rebuild_tensor_v2
        if module == "torch" and name in _DTYPES: return name
        raise pickle.UnpicklingError(f"refusing to load {module}.{name} from a model file")

    def persistent_load(self, pid):
        kind, storage_type, key, _loc, numel = pid
        if kind != "storage" or storage_type not in _DTYPES:
            raise pickle.UnpicklingError("unexpected storage in model file")
        return np.frombuffer(self.zf.read(f"{self.prefix}data/{key}"), dtype=_DTYPES[storage_type], count=numel)


def load_state_dict(path: str | Path) -> dict[str, np.ndarray]:
    with zipfile.ZipFile(path) as zf:
        pkl = next(n for n in zf.namelist() if n.endswith("data.pkl"))
        obj = _SafeUnpickler(io.BytesIO(zf.read(pkl)), zf, pkl[: -len("data.pkl")]).load()
    if isinstance(obj, dict) and "params" in obj: obj = obj["params"]
    if isinstance(obj, dict) and "params_ema" in obj: obj = obj["params_ema"]
    return {k: np.asarray(v, np.float32) for k, v in obj.items()}


def build_onnx(sd: dict[str, np.ndarray], out: str | Path, upscale: int = 4) -> None:
    """SRVGGNetCompact: conv -> PReLU -> N x (conv -> PReLU) -> conv(3*s*s) -> PixelShuffle(s) + nearest-upsampled input."""
    import onnx
    from onnx import helper, numpy_helper, TensorProto
    idx = sorted({int(k.split(".")[1]) for k in sd})
    nodes, inits = [], []
    cur = "input"
    for i in idx:
        w = sd[f"body.{i}.weight"]
        if w.ndim == 4:      # conv
            inits += [numpy_helper.from_array(w, f"w{i}"), numpy_helper.from_array(sd[f"body.{i}.bias"], f"b{i}")]
            nodes.append(helper.make_node("Conv", [cur, f"w{i}", f"b{i}"], [f"c{i}"], kernel_shape=list(w.shape[2:]), pads=[1, 1, 1, 1]))
            cur = f"c{i}"
        else:                # PReLU, per-channel slope
            inits.append(numpy_helper.from_array(w.reshape(-1, 1, 1), f"p{i}"))
            nodes.append(helper.make_node("PRelu", [cur, f"p{i}"], [f"a{i}"]))
            cur = f"a{i}"
    nodes.append(helper.make_node("DepthToSpace", [cur], ["ps"], blocksize=upscale, mode="CRD"))   # == torch pixel_shuffle
    inits.append(numpy_helper.from_array(np.array([1, 1, upscale, upscale], np.float32), "scales"))
    nodes.append(helper.make_node("Resize", ["input", "", "scales"], ["up"], mode="nearest"))
    nodes.append(helper.make_node("Add", ["ps", "up"], ["output"]))
    g = helper.make_graph(nodes, "srvgg", [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, None, None])],
                          [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3, None, None])], inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.checker.check_model(m)
    tmp = Path(str(out) + ".part")
    onnx.save(m, str(tmp))
    tmp.replace(out)


def dni(a: dict, b: dict, t: float) -> dict:
    return {k: (t * b[k] + (1 - t) * a[k]).astype(np.float32) for k in a}
