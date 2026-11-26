"""Cinematic look: a real film grade in one control, as a colourist builds it.

  * a filmic tone curve: slightly lifted, soft blacks and highlights that roll off instead of glaring, a gentle S in the middle;
  * colour by HUE (not only by brightness): skin (reds / yellows) stays warm, greens move toward teal, magentas / blues are calmed;
  * split tone with the brightness preserved: teal shadows, warm highlights (teal & orange), or the style's own pair;
  * restrained overall saturation, a vignette, fine moving film grain;
  * optional widescreen bars (letterbox) for the cinema frame.
`cinematic` (0 .. 1.5) scales everything; `cinematic_style` picks the look.
"""
from __future__ import annotations

STYLES = ("teal_orange", "moody", "golden", "bleach")

# per style at strength 1: exposure, black lift, highlight roll-off, mid S, shadow (r,g,b), highlight (r,g,b), saturation, skin sat,
# green hue shift / sat, magenta+blue sat, vignette, grain
_S = {
    "teal_orange": dict(ev=-0.15, lift=0.045, roll=0.06, s=0.035, sh=(-0.14, 0.02, 0.14), hi=(0.10, 0.03, -0.10), sat=0.9, skin=0.08, gh=40, gs=-0.2, ms=-0.3, vig=0.5, grain=5.0),
    "moody":       dict(ev=-0.4, lift=0.06, roll=0.1, s=0.05, sh=(-0.1, 0.03, 0.12), hi=(0.03, 0.01, -0.03), sat=0.72, skin=0.0, gh=30, gs=-0.45, ms=-0.45, vig=0.75, grain=6.5),
    "golden":      dict(ev=-0.1, lift=0.05, roll=0.06, s=0.025, sh=(-0.04, 0.02, 0.08), hi=(0.14, 0.06, -0.12), sat=0.95, skin=0.1, gh=10, gs=-0.2, ms=-0.2, vig=0.45, grain=5.0),
    "bleach":      dict(ev=-0.1, lift=0.02, roll=0.04, s=0.07, sh=(-0.03, 0.0, 0.04), hi=(0.0, 0.0, 0.0), sat=0.55, skin=-0.1, gh=0, gs=-0.3, ms=-0.3, vig=0.55, grain=6.0),
}


def _curve(k: float, d: dict) -> str:
    lift, roll, s = d["lift"] * k, d["roll"] * k, d["s"] * k
    pts = [(0.0, lift), (0.25, 0.25 - s + lift * 0.4), (0.5, 0.5), (0.75, 0.75 + s - roll * 0.3), (1.0, 1.0 - roll)]
    return " ".join(f"{x:.3f}/{min(1, max(0, y)):.4f}" for x, y in pts)


def filters(k: float, style: str, have_hue: bool = True) -> list[str]:
    """RGB-domain filters for the look at strength k (0 = nothing)"""
    if k <= 0: return []
    d = _S.get(style, _S["teal_orange"])
    f: list[str] = []
    if d["ev"]: f.append(f"exposure=exposure={d['ev'] * k:.4f}")
    f.append(f"curves=master='{_curve(k, d)}'")
    rs, gs_, bs = (x * k for x in d["sh"]); rh, gh_, bh = (x * k for x in d["hi"])
    f.append(f"colorbalance=rs={rs:.4f}:gs={gs_:.4f}:bs={bs:.4f}:rh={rh:.4f}:gh={gh_:.4f}:bh={bh:.4f}:pl=1")
    if have_hue:                                                                   # colour by hue (FFmpeg 5.1+)
        if d["gh"] or d["gs"]: f.append(f"huesaturation=colors=g:hue={d['gh'] * k:.2f}:saturation={max(-1, d['gs'] * k):.3f}:strength=4")
        if d["ms"]: f.append(f"huesaturation=colors=m+b:saturation={max(-1, d['ms'] * k):.3f}:strength=4")
        if d["skin"]: f.append(f"huesaturation=colors=r+y:saturation={max(-1, min(1, d['skin'] * k)):.3f}:strength=3")
    sat = 1 + (d["sat"] - 1) * min(k, 1.5)
    if abs(sat - 1) > 1e-3: f.append(f"eq=saturation={sat:.4f}")
    return f


def finish(k: float, style: str, scale_px: float) -> list[str]:
    """vignette + grain (in the picture's own format, after the colour work)"""
    if k <= 0: return []
    d = _S.get(style, _S["teal_orange"])
    import math
    out = []
    if d["vig"]: out.append(f"vignette=angle={min(math.pi / 2.2, d['vig'] * k * 0.9):.4f}:mode=forward:eval=init")
    if d["grain"]: out.append(f"noise=c0s={d['grain'] * k:.2f}:c0f=t+u")         # brightness only, like film grain (no colour speckles)
    return out


def letterbox(aspect: str | None, W: int, H: int) -> str | None:
    """black bars top and bottom so the visible picture has the cinema aspect (e.g. 2.39:1); None if it already is that wide"""
    if not aspect or aspect == "none": return None
    a = float(aspect)
    inner = int(round(W / a)) // 2 * 2
    bar = (H - inner) // 2
    if bar <= 0 or 2 * bar > 0.35 * H: return None                 # a tall (portrait) video would lose most of the picture: no bars
    return f"crop={W}:{H - 2 * bar}:0:{bar},pad={W}:{H}:0:{bar}:black"


def letterbox_skipped(aspect: str | None, W: int, H: int) -> bool:
    """bars were asked for but would cover more than a third of the picture (portrait / square video)"""
    if not aspect or aspect == "none": return False
    bar = (H - int(round(W / float(aspect))) // 2 * 2) // 2
    return bar > 0 and 2 * bar > 0.35 * H

