"""Smart HDR tone curve: how much each pixel is multiplied by, from the brightness of its REGION (b, 0..1).

    gain(b) = (1 + lift(b)) x (1 - hold(b))
    lift: dark regions are brightened; it fades out by the middle tones (so mid-grey regions are left alone)
    hold: bright regions are pulled down a little (sky, a sunlit wall get their detail back)
Because b is the brightness of the region (edge-aware), detail inside a region keeps its contrast and every colour keeps its hue.
"""
from __future__ import annotations


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def gain(b: float, amount: float, shadows: float, highlights: float) -> float:
    lift = 1.4 * shadows * amount * (1 - _smooth(0.04, 0.5, b))
    hold = 0.4 * highlights * amount * _smooth(0.55, 0.97, b)
    return (1 + lift) * (1 - hold)


def gain_expr(amount: float, shadows: float, highlights: float) -> str:
    """the same as gain(), as an FFmpeg expression of x (the pixel's channel value) and y (its region's brightness), both 0..255"""
    b = "(y/255)"
    sm = lambda e0, e1: f"(pow(clip(({b}-{e0})/{e1 - e0:.4f},0,1),2)*(3-2*clip(({b}-{e0})/{e1 - e0:.4f},0,1)))"
    lift = f"({1.4 * shadows * amount:.4f}*(1-{sm(0.04, 0.5)}))"
    hold = f"({0.4 * highlights * amount:.4f}*{sm(0.55, 0.97)})"
    return f"clip(x*(1+{lift})*(1-{hold}),0,255)"
