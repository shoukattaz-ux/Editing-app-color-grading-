"""Turns keyframed operation parameters into FFmpeg runtime commands (sendcmd).

Rules learned by measurement (see tests/test_animation.py):
  * sendcmd drives a filter exactly (bit-identical to a static render at that frame's value) when the target filter is in the
    same LINEAR single-input chain as, and downstream of, the sendcmd. It is NOT exact across multi-input synchronising
    filters (overlay/blend look one frame ahead), so those use per-frame expressions instead (keyframes.to_expr).
  * One command file per chain, containing only options that change from one frame to the next.
"""
from __future__ import annotations
import os
from typing import Callable

from .keyframes import value_at


def _quote(v: str) -> str:
    return v if v.startswith("'") else (f"'{v}'" if any(c in v for c in " ,;[]") else v)


def op_params_at(op, fps: float, frame: int) -> dict:
    """all of an operation's parameters at a TIMELINE frame, with keyframed ones interpolated (integers rounded)"""
    if not op.keyframes: return op.params
    t_rel = (frame - op.start_frame) / fps
    p = dict(op.params)
    for name, kfs in op.keyframes.items():
        v = value_at(kfs, t_rel)
        base = op.params.get(name)
        p[name] = int(round(v)) if isinstance(base, int) and not isinstance(base, bool) else v
    return p


def keyframes_key(op) -> list:
    """hashable description of an operation's animation (analysis caches must change when it changes)"""
    return [op.start_frame, sorted((n, [(k.time, k.value, k.easing) for k in kfs]) for n, kfs in op.keyframes.items())]


class AnimChain:
    """Animation context for ONE linear filter chain of ONE operation."""

    def __init__(self, op, fps: float, first_frame: int, last_frame: int, work_dir: str, work_files: list[str], tag: str = ""):
        self.op, self.fps = op, fps
        self.first = max(first_frame, op.start_frame)               # first timeline frame this chain processes inside the op
        self.last = min(last_frame, op.end_frame)                   # exclusive
        self.wd, self.work_files, self.tag = work_dir, work_files, tag
        self.specs: list[tuple[str, str, Callable[[dict], dict[str, str]]]] = []
        self._n = 0

    # ---- parameter values
    def has(self, *names: str) -> bool:
        return any(n in self.op.keyframes for n in names)

    def params_at(self, frame: int) -> dict:
        """all of the operation's parameters at a timeline frame (animated ones interpolated)"""
        return op_params_at(self.op, self.fps, frame)

    # ---- filters
    def add(self, name: str, uses: tuple[str, ...], opts: Callable[[dict], dict[str, str]],
            include: Callable[[dict], bool] | None = None) -> str | None:
        """Filter `name` whose options are opts(params). If none of `uses` is animated it is a plain static filter (omitted
        when include(params) is false); otherwise it gets an @id and is driven by commands."""
        p0 = self.params_at(self.first)
        if not self.has(*uses):
            if include is not None and not include(p0): return None
            return name + "=" + ":".join(f"{k}={v}" for k, v in opts(p0).items())
        self._n += 1
        fid = f"kf{self.tag}{self._n}"
        self.specs.append((name, fid, opts))
        return f"{name}@{fid}=" + ":".join(f"{k}={v}" for k, v in opts(p0).items())

    def sendcmd(self) -> list[str]:
        """['sendcmd=f=<file>'] (to put in front of the chain's first animated filter) or [] when nothing is animated"""
        if not self.specs: return []
        prev = [opts(self.params_at(self.first)) for _, _, opts in self.specs]
        lines: list[str] = []
        for f in range(self.first + 1, self.last):
            p = self.params_at(f)
            cmds: list[str] = []
            for i, (name, fid, opts) in enumerate(self.specs):
                cur = opts(p)
                for k, v in cur.items():
                    if prev[i].get(k) != v: cmds.append(f"{name}@{fid} {k} {_quote(v)}")
                prev[i] = cur
            if cmds: lines.append(f"{(f - 0.5) / self.fps:.6f} " + ", ".join(cmds) + ";")
        if not lines: return []
        path = os.path.join(self.wd, f"{self.op.id}{self.tag}.cmd")
        with open(path, "w", encoding="utf-8") as fh: fh.write("\n".join(lines) + "\n")
        self.work_files.append(path)
        from .compiler import esc_path
        return [f"sendcmd=f='{esc_path(path)}'"]
