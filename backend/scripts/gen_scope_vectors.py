"""Regenerates shared/scope_vectors.json with an INDEPENDENT NumPy implementation of the scope maths.
The TypeScript implementation (frontend/src/scopes.ts) must reproduce these numbers exactly."""
import json
from pathlib import Path
import numpy as np

OUT = Path(__file__).resolve().parents[2] / "shared" / "scope_vectors.json"
W, H = 32, 24
KR, KG, KB, CBD, CRD = 0.2126, 0.7152, 0.0722, 1.8556, 1.5748     # ITU-R BT.709


def test_image():
    y, x = np.mgrid[0:H, 0:W]
    return ((x * 37 + y * 11) % 256, (x * 5 + y * 91) % 256, (x * 17 + y * 3 + x * y) % 256)


def ycbcr(r, g, b):
    r, g, b = (np.asarray(v, np.float64) / 255.0 for v in (r, g, b))
    y = KR * r + KG * g + KB * b
    return y, (b - y) / CBD, (r - y) / CRD


def level(y):
    return np.floor(np.asarray(y) * 255 + 0.5).astype(int)


def build() -> dict:
    r, g, b = test_image()
    y, cb, cr = ycbcr(r, g, b)
    lv = level(y)
    hist = {k: np.bincount(v.ravel(), minlength=256).tolist() for k, v in (("r", r), ("g", g), ("b", b), ("y", lv))}
    cols = 8
    wf = {}
    for name, ch in (("y", lv), ("r", r), ("g", g), ("b", b)):
        counts = np.zeros(cols * 256, int)
        xs = np.broadcast_to((np.arange(W) * cols) // W, (H, W))
        np.add.at(counts, (xs * 256 + ch).ravel(), 1)
        wf[name] = counts.tolist()
    n = 32
    bx = np.clip(np.floor((cb + 0.5) * n).astype(int), 0, n - 1)
    by = np.clip(np.floor((0.5 - cr) * n).astype(int), 0, n - 1)
    vs = np.zeros(n * n, int)
    np.add.at(vs, (by * n + bx).ravel(), 1)
    rgb = np.stack([r, g, b])
    stats = {"clipHighPct": float(100 * np.mean(rgb.max(0) == 255)), "clipLowPct": float(100 * np.mean(rgb.min(0) == 0)), "meanY": float(y.mean())}
    named = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "cyan": (0, 255, 255),
             "magenta": (255, 0, 255), "yellow": (255, 255, 0), "skin": (224, 172, 140), "grey": (128, 128, 128), "red75": (191, 0, 0)}
    colors = []
    for k, (cr_, cg_, cb_) in named.items():
        yy, bb, rr = (float(v) for v in ycbcr(cr_, cg_, cb_))
        colors.append({"name": k, "rgb": [cr_, cg_, cb_], "y": yy, "cb": bb, "cr": rr,
                       "angle": float(np.degrees(np.arctan2(rr, bb)) % 360), "radius": float(np.hypot(bb, rr) / 0.5)})
    return {"image": {"width": W, "height": H, "formula": "r=(x*37+y*11)%256 g=(x*5+y*91)%256 b=(x*17+y*3+x*y)%256"},
            "histogram": hist, "waveform_cols": cols, "waveform": wf, "vectorscope_n": n, "vectorscope": vs.tolist(), "stats": stats, "colors": colors}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), separators=(",", ":")) + "\n")
    print("wrote", OUT)
