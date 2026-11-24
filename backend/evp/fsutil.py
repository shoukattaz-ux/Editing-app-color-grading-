"""File-system helpers that survive Windows' stricter rules (open handles, virus scanners, search indexers)."""
from __future__ import annotations
import os, sys, time
from pathlib import Path


def replace(src, dst, tries: int = 25, delay: float = 0.2) -> None:
    """os.replace that waits and retries when Windows briefly refuses (WinError 5/32: the file is locked by a scanner or another process)."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or i == tries - 1: raise
            time.sleep(delay)


def unlink(path, tries: int = 10, delay: float = 0.2) -> None:
    """Path.unlink(missing_ok=True) with the same patience; gives up quietly (a leftover temp file is harmless)."""
    p = Path(path)
    for i in range(tries):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            if sys.platform != "win32": raise
            time.sleep(delay)
