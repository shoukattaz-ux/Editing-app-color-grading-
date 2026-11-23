"""Path policy: blueprints and assets are untrusted input."""
from __future__ import annotations
import os
from pathlib import Path


class PathError(ValueError):
    pass


class PathPolicy:
    """Resolves untrusted paths and refuses anything outside approved roots."""

    def __init__(self, roots: list[str | Path] | None = None):
        self.roots: list[Path] = [Path(r).resolve() for r in (roots or [])]

    def add_root(self, p: str | Path) -> None:
        r = Path(p).resolve()
        if r not in self.roots:
            self.roots.append(r)

    def resolve(self, raw: str, base: Path | None = None) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise PathError("empty path")
        if "\x00" in raw:
            raise PathError("NUL byte in path")
        if raw.startswith(("http://", "https://", "file:", "\\\\", "//")):
            raise PathError("URLs and UNC paths are not allowed")
        p = Path(raw)
        if not p.is_absolute():
            if ".." in p.parts:
                raise PathError("directory traversal ('..') is not allowed")
            candidates = ([base / p] if base else []) + [r / p for r in self.roots]
            for c in candidates:
                rc = c.resolve()
                if self._inside(rc) and rc.exists():
                    return rc
            # not found: still return a policy-checked path for 'missing' reporting
            first = (candidates[0] if candidates else Path(raw)).resolve()
            if not self._inside(first):
                raise PathError("path escapes approved directories")
            return first
        rp = p.resolve()  # follows symlinks, so symlink escapes are caught
        if not self._inside(rp):
            raise PathError("absolute path is outside approved directories")
        return rp

    def _inside(self, p: Path) -> bool:
        return any(p == r or r in p.parents for r in self.roots)


def safe_output_path(out: str | Path, source: str | Path, allowed_dir: str | Path) -> Path:
    """Output must live under allowed_dir and may never be (or alias) the source video."""
    o = Path(out).resolve()
    d = Path(allowed_dir).resolve()
    if d != o and d not in o.parents:
        raise PathError("output must be inside the project's exports directory")
    s = Path(source).resolve()
    if o == s or (o.exists() and s.exists() and os.path.samefile(o, s)):
        raise PathError("refusing to overwrite the original video")
    return o
