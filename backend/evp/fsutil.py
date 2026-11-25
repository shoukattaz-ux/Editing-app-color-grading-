"""File-system helpers that survive Windows' stricter rules (open handles, virus scanners, search indexers)."""
from __future__ import annotations
import contextlib, os, sys, threading, time, uuid
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


def temp_part(dst) -> Path:
    """a temp name next to `dst` that no other thread / process can also be using (a shared 'x.part' made the second writer's rename fail with
    WinError 2 'file not found' once the first one had already renamed it)"""
    return Path(f"{dst}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part")


def replace_or_existing(src, dst) -> None:
    """replace(); if the source vanished because another writer already put the finished file in place, that is success"""
    try: replace(src, dst)
    except FileNotFoundError:
        if not Path(dst).is_file(): raise


_KEYED: dict[str, threading.Lock] = {}
_KEYED_GUARD = threading.Lock()


@contextlib.contextmanager
def exclusive(path):
    """one writer per output file inside this process: a second request for the same cached result (the preview frame and the preview video
    ask together) waits, then finds the finished file instead of racing the first one on the same temp file"""
    key = os.path.normcase(os.path.abspath(str(path)))
    with _KEYED_GUARD:
        lk = _KEYED.setdefault(key, threading.Lock())
    with lk:
        yield
