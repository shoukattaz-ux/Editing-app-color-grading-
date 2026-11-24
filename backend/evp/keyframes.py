"""Keyframe animation of numeric operation parameters.

A keyframe is (time, value, easing). `time` is seconds RELATIVE TO THE OPERATION'S START (so moving an operation moves
its animation). `easing` shapes the segment that starts at that keyframe. Before the first / after the last keyframe the
value is held. One definition of the maths lives here (value_at); to_expr() produces the equivalent FFmpeg expression
and is cross-checked against value_at in the tests; the TypeScript editor mirrors value_at and is tested against the
same shared vectors (shared/keyframe_vectors.json).
"""
from __future__ import annotations
from dataclasses import dataclass

EASINGS = ("linear", "hold", "ease_in", "ease_out", "ease_in_out")
MAX_KEYFRAMES = 64


@dataclass(frozen=True)
class Keyframe:
    time: float
    value: float
    easing: str = "linear"


def ease(name: str, u: float) -> float:
    if name == "linear": return u
    if name == "hold": return 0.0
    if name == "ease_in": return u * u
    if name == "ease_out": return u * (2.0 - u)
    if name == "ease_in_out": return u * u * (3.0 - 2.0 * u)
    raise ValueError(f"unknown easing {name!r}")


def value_at(kfs: list[Keyframe], t: float) -> float:
    """Interpolated value at time t (seconds relative to the operation start). kfs must be sorted by time, non-empty."""
    if t <= kfs[0].time: return kfs[0].value
    if t >= kfs[-1].time: return kfs[-1].value
    lo, hi = 0, len(kfs) - 1                      # binary search: kfs[lo].time <= t < kfs[lo+1].time
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if kfs[mid].time <= t: lo = mid
        else: hi = mid
    a, b = kfs[lo], kfs[lo + 1]
    return a.value + (b.value - a.value) * ease(a.easing, (t - a.time) / (b.time - a.time))


def _num(x: float) -> str:
    s = repr(float(x))
    return s[:-2] if s.endswith(".0") else s


def to_expr(kfs: list[Keyframe], t0: float, var: str = "t") -> str:
    """FFmpeg expression of absolute time `var` (seconds) equal to value_at(kfs, var - t0).
    Safe to embed in a single-quoted filter option (contains commas)."""
    if len(kfs) == 1: return _num(kfs[0].value)
    def seg(a: Keyframe, b: Keyframe) -> str:
        ta, tb = t0 + a.time, t0 + b.time
        if a.easing == "hold" or a.value == b.value: return _num(a.value)
        u = f"(({var}-{_num(ta)})*{_num(1.0 / (tb - ta))})"
        e = {"linear": u, "ease_in": f"({u}*{u})", "ease_out": f"({u}*(2-{u}))", "ease_in_out": f"({u}*{u}*(3-2*{u}))"}[a.easing]
        return f"({_num(a.value)}+({_num(b.value - a.value)})*{e})"
    out = _num(kfs[-1].value)
    for i in range(len(kfs) - 2, 0, -1):          # inner segments, innermost = last
        out = f"if(lt({var},{_num(t0 + kfs[i + 1].time)}),{seg(kfs[i], kfs[i + 1])},{out})"
    return f"if(lt({var},{_num(t0 + kfs[1].time)}),if(lte({var},{_num(t0 + kfs[0].time)}),{_num(kfs[0].value)},{seg(kfs[0], kfs[1])}),{out})"


def prune(kfs: list[Keyframe], lo: float, hi: float) -> list[Keyframe]:
    """Drop keyframes that cannot influence values inside [lo, hi] (relative time): keep those inside plus the one
    bracketing keyframe on each side."""
    n = len(kfs)
    first = max([i for i in range(n) if kfs[i].time <= lo], default=0)
    last = min([i for i in range(n) if kfs[i].time >= hi], default=n - 1)
    return kfs[first:last + 1]


def split_at(kfs: list[Keyframe], t: float, length: float) -> tuple[list[Keyframe], list[Keyframe]]:
    """Split an animation of an operation of `length` seconds at relative time t.
    Both halves keep the ORIGINAL curve (so they render exactly like the unsplit operation, whatever the easing): the
    left half keeps its keyframes, the right half's are shifted by -t. Keyframes outside a half's range stay only where
    they bracket it (needed for exactness); times may therefore be negative or beyond the operation's end."""
    left = prune(kfs, 0.0, t)
    shifted = [Keyframe(k.time - t, k.value, k.easing) for k in kfs]
    right = prune(shifted, 0.0, length - t)
    return left, right
