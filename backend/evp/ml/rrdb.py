"""Real-ESRGAN RRDBNet (RealESRGAN_x4plus / _x2plus, BSD-3-Clause) -> ONNX, without PyTorch.

Architecture (basicsr RRDBNet, num_feat=64, num_grow_ch=32, LeakyReLU 0.2):
  [x2 model: pixel_unshuffle(2)] -> conv_first -> 23 x RRDB -> conv_body (+ skip)
  -> 2 x (nearest x2 -> conv -> lrelu) -> conv_hr -> lrelu -> conv_last
  RDB:  x1..x4 densely connected convs, x5 * 0.2 + x;   RRDB: rdb3(rdb2(rdb1(x))) * 0.2 + x
"""
from __future__ import annotations
from .. import fsutil
from pathlib import Path

import numpy as np


def _unshuffle_perm(c: int, r: int) -> np.ndarray:
    """index map so that ONNX SpaceToDepth channel s holds torch pixel_unshuffle channel perm[s]."""
    perm = np.zeros(c * r * r, int)
    for ch in range(c):
        for bh in range(r):
            for bw in range(r):
                perm[(bh * r + bw) * c + ch] = ch * r * r + bh * r + bw
    return perm


def build_onnx(sd: dict[str, np.ndarray], out: str | Path) -> int:
    """Returns the network's overall scale (4 for x4plus, 2 for x2plus)."""
    import onnx
    from onnx import helper, numpy_helper, TensorProto
    nodes, inits = [], []
    n = [0]

    def name(p):
        n[0] += 1
        return f"{p}{n[0]}"

    def const(arr, p="k"):
        k = name(p); inits.append(numpy_helper.from_array(np.asarray(arr, np.float32), k)); return k

    def conv(x, key, w=None):
        w = sd[f"{key}.weight"] if w is None else w
        o = name("c")
        nodes.append(helper.make_node("Conv", [x, const(w), const(sd[f"{key}.bias"])], [o], kernel_shape=[3, 3], pads=[1, 1, 1, 1]))
        return o

    def lrelu(x):
        o = name("l"); nodes.append(helper.make_node("LeakyRelu", [x], [o], alpha=0.2)); return o

    def cat(xs):
        o = name("cat"); nodes.append(helper.make_node("Concat", xs, [o], axis=1)); return o

    def axpy(x, a, y):   # x * a + y
        m = name("m"); nodes.append(helper.make_node("Mul", [x, const(np.array(a))], [m]))
        o = name("a"); nodes.append(helper.make_node("Add", [m, y], [o])); return o

    def add(x, y):
        o = name("a"); nodes.append(helper.make_node("Add", [x, y], [o])); return o

    def up2(x):
        o = name("u"); nodes.append(helper.make_node("Resize", [x, "", const(np.array([1, 1, 2, 2]), "s")], [o], mode="nearest")); return o

    in_ch = sd["conv_first.weight"].shape[1]
    scale = 4 if in_ch == 3 else 2
    x = "input"
    w_first = sd["conv_first.weight"]
    if in_ch == 12:                                     # x2 model: pixel_unshuffle(2) == SpaceToDepth + channel permutation
        s2d = name("s2d"); nodes.append(helper.make_node("SpaceToDepth", [x], [s2d], blocksize=2))
        x, w_first = s2d, w_first[:, _unshuffle_perm(3, 2)]
    feat = conv(x, "conv_first", w_first)
    nblocks = len({k.split(".")[1] for k in sd if k.startswith("body.")})
    h = feat
    for b in range(nblocks):
        r_in = h
        for r in (1, 2, 3):
            p = f"body.{b}.rdb{r}"
            xs = [h]
            for c in range(1, 5):
                xs.append(lrelu(conv(cat(xs) if len(xs) > 1 else xs[0], f"{p}.conv{c}")))
            h = axpy(conv(cat(xs), f"{p}.conv5"), 0.2, h)
        h = axpy(h, 0.2, r_in)
    feat = add(feat, conv(h, "conv_body"))
    feat = lrelu(conv(up2(feat), "conv_up1"))
    feat = lrelu(conv(up2(feat), "conv_up2"))
    o = conv(lrelu(conv(feat, "conv_hr")), "conv_last")
    nodes.append(helper.make_node("Identity", [o], ["output"]))
    g = helper.make_graph(nodes, "rrdbnet", [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, None, None])],
                          [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3, None, None])], inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.checker.check_model(m)
    tmp = Path(str(out) + ".part")
    onnx.save(m, str(tmp))
    fsutil.replace(tmp, out)
    return scale
