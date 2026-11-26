"""GFPGAN v1.4 (TencentARC, Apache-2.0) face restoration -> ONNX, built from the official weights without PyTorch.

Architecture (GFPGANv1Clean: out_size 512, narrow 1, channel_multiplier 2, sft_half, different_w, input_is_latent):
  encoder  conv_body_first (1x1) -> 7 x ResBlock(down) -> final_conv -> Linear -> 16 style codes (16 x 512)
  decoder  7 x [ + U-Net skip -> ResBlock(up) ] gives spatial conditions (scale, shift) per level
  StyleGAN2 generator (constant 4x4 input, 15 modulated convs + 8 ToRGB, upsample x2 bilinear), where after each upsampling conv the SECOND
  HALF of the channels is modulated by the spatial condition (out * scale + shift)  [SFT];  image = the accumulated ToRGB skip.
Differences from the PyTorch reference, on purpose:
  * the per-pixel noise of the StyleGAN2 layers is fixed at zero (the reference draws new random noise for every call, which would make a video
    shimmer from frame to frame);
  * modulated convolution is computed as conv(x * style) * demod, which equals conv(weight * style) with demodulation for one image at a time.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np

SIZE = 512
SQRT2 = 1.4142135623730951


class _G:
    """tiny ONNX graph builder"""

    def __init__(self):
        from onnx import helper, numpy_helper
        self.h, self.nh = helper, numpy_helper
        self.nodes, self.inits, self.n = [], [], 0

    def name(self, hint="t") -> str:
        self.n += 1
        return f"{hint}{self.n}"

    def const(self, arr, hint="c") -> str:
        nm = self.name(hint)
        self.inits.append(self.nh.from_array(np.asarray(arr), nm))
        return nm

    def op(self, typ, ins, hint="t", **attrs) -> str:
        out = self.name(hint)
        self.nodes.append(self.h.make_node(typ, ins, [out], **attrs))
        return out

    def conv(self, x, w, b=None, pad=1):
        ins = [x, self.const(w.astype(np.float32), "w")] + ([self.const(b.astype(np.float32), "b")] if b is not None else [])
        return self.op("Conv", ins, "cv", kernel_shape=list(w.shape[2:]), pads=[pad] * 4)

    def lrelu(self, x): return self.op("LeakyRelu", [x], "a", alpha=0.2)

    def resize(self, x, scale: float):
        roi = self.const(np.zeros(0, np.float32), "roi")
        sc = self.const(np.array([1, 1, scale, scale], np.float32), "sc")
        return self.op("Resize", [x, roi, sc], "rs", mode="linear", coordinate_transformation_mode="half_pixel")

    def add(self, a, b): return self.op("Add", [a, b], "ad")
    def mul(self, a, b): return self.op("Mul", [a, b], "ml")


def _resblock(g: _G, x, sd, pfx, mode):
    s = 0.5 if mode == "down" else 2.0
    out = g.lrelu(g.conv(x, sd[f"{pfx}.conv1.weight"], sd[f"{pfx}.conv1.bias"]))
    out = g.resize(out, s)
    out = g.lrelu(g.conv(out, sd[f"{pfx}.conv2.weight"], sd[f"{pfx}.conv2.bias"]))
    skip = g.conv(g.resize(x, s), sd[f"{pfx}.skip.weight"], None, pad=0)
    return g.add(out, skip)


def _modulated(g: _G, x, style, sd, pfx, demod: bool, upsample: bool, pad: int):
    """style: (1, 512) tensor name"""
    ws = sd[f"{pfx}.modulation.weight"]                                       # (in_c, 512)
    s = g.op("Gemm", [style, g.const(ws.astype(np.float32), "mw"), g.const(sd[f"{pfx}.modulation.bias"].astype(np.float32), "mb")], "st", transB=1)   # (1, in_c)
    w = sd[f"{pfx}.weight"][0]                                                # (out_c, in_c, k, k)
    in_c = w.shape[1]
    if upsample: x = g.resize(x, 2.0)
    xs = g.mul(x, g.op("Reshape", [s, g.const(np.array([1, in_c, 1, 1], np.int64), "shp")], "sr"))
    out = g.conv(xs, w, None, pad=pad)
    if demod:
        wsq = (w.astype(np.float64) ** 2).sum((2, 3)).astype(np.float32)      # (out_c, in_c)
        s2 = g.mul(s, s)
        d = g.op("MatMul", [s2, g.const(np.ascontiguousarray(wsq.T), "wsq")], "dm")            # (1, out_c)
        d = g.op("Add", [d, g.const(np.array(1e-8, np.float32), "eps")], "de")
        d = g.op("Reciprocal", [g.op("Sqrt", [d], "sq")], "rq")
        out = g.mul(out, g.op("Reshape", [d, g.const(np.array([1, w.shape[0], 1, 1], np.int64), "shp")], "dr"))
    return out


def _style_conv(g, x, style, sd, pfx, upsample):
    out = _modulated(g, x, style, sd, f"{pfx}.modulated_conv", True, upsample, 1)
    out = g.mul(out, g.const(np.array(SQRT2, np.float32), "r2"))
    out = g.add(out, g.const(sd[f"{pfx}.bias"].astype(np.float32), "bias"))
    return g.lrelu(out)


def _to_rgb(g, x, style, sd, pfx, skip):
    out = _modulated(g, x, style, sd, f"{pfx}.modulated_conv", False, False, 0)
    out = g.add(out, g.const(sd[f"{pfx}.bias"].astype(np.float32), "bias"))
    if skip is not None: out = g.add(out, g.resize(skip, 2.0))
    return out


def build_onnx(sd: dict[str, np.ndarray], out: str | Path) -> None:
    import onnx
    from onnx import helper, TensorProto
    from .. import fsutil
    g = _G()
    # ---- encoder
    feat = g.lrelu(g.conv("input", sd["conv_body_first.weight"], sd["conv_body_first.bias"], pad=0))
    skips = []
    for i in range(7):
        feat = _resblock(g, feat, sd, f"conv_body_down.{i}", "down")
        skips.insert(0, feat)
    feat = g.lrelu(g.conv(feat, sd["final_conv.weight"], sd["final_conv.bias"]))
    flat = g.op("Reshape", [feat, g.const(np.array([1, -1], np.int64), "shp")], "fl")
    codes = g.op("Gemm", [flat, g.const(sd["final_linear.weight"].astype(np.float32), "lw"), g.const(sd["final_linear.bias"].astype(np.float32), "lb")], "lc", transB=1)
    latent = g.op("Reshape", [codes, g.const(np.array([1, 16, 512], np.int64), "shp")], "lat")
    lat = [g.op("Gather", [latent, g.const(np.array(i, np.int64), "ix")], "L", axis=1) for i in range(16)]       # 16 x (1, 512)
    # ---- U-Net decoder -> spatial conditions
    cond = []
    for i in range(7):
        feat = g.add(feat, skips[i])
        feat = _resblock(g, feat, sd, f"conv_body_up.{i}", "up")
        for kind in ("scale", "shift"):
            c = g.lrelu(g.conv(feat, sd[f"condition_{kind}.{i}.0.weight"], sd[f"condition_{kind}.{i}.0.bias"]))
            cond.append(g.conv(c, sd[f"condition_{kind}.{i}.2.weight"], sd[f"condition_{kind}.{i}.2.bias"]))
    # ---- StyleGAN2 decoder with SFT
    D = "stylegan_decoder"
    out_t = g.const(sd[f"{D}.constant_input.weight"].astype(np.float32), "const")                       # (1, 512, 4, 4)
    out_t = _style_conv(g, out_t, lat[0], sd, f"{D}.style_conv1", False)
    skip = _to_rgb(g, out_t, lat[1], sd, f"{D}.to_rgb1", None)
    i = 1
    for j in range(7):
        out_t = _style_conv(g, out_t, lat[i], sd, f"{D}.style_convs.{2 * j}", True)
        ch = sd[f"{D}.style_convs.{2 * j}.bias"].shape[1]
        a = g.op("Slice", [out_t, g.const(np.array([0], np.int64), "s0"), g.const(np.array([ch // 2], np.int64), "s1"), g.const(np.array([1], np.int64), "ax")], "sa")
        b = g.op("Slice", [out_t, g.const(np.array([ch // 2], np.int64), "s0"), g.const(np.array([ch], np.int64), "s1"), g.const(np.array([1], np.int64), "ax")], "sb")
        b = g.add(g.mul(b, cond[2 * j]), cond[2 * j + 1])                                                 # SFT on the second half of the channels
        out_t = g.op("Concat", [a, b], "cc", axis=1)
        out_t = _style_conv(g, out_t, lat[i + 1], sd, f"{D}.style_convs.{2 * j + 1}", False)
        skip = _to_rgb(g, out_t, lat[i + 2], sd, f"{D}.to_rgbs.{j}", skip)
        i += 2
    image = g.op("Clip", [skip, g.const(np.array(-1.0, np.float32), "lo"), g.const(np.array(1.0, np.float32), "hi")], "img")
    inp = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, SIZE, SIZE])
    outp = helper.make_tensor_value_info(image, TensorProto.FLOAT, [1, 3, SIZE, SIZE])
    graph = helper.make_graph(g.nodes, "gfpgan_v1_4", [inp], [outp], g.inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model, full_check=False)
    tmp = Path(str(out) + ".part")
    onnx.save_model(model, str(tmp), save_as_external_data=False)
    fsutil.replace(tmp, Path(out))
