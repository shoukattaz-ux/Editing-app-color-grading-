"""Child processes (ffmpeg / ffprobe) with safe defaults.

Windows: when the engine is started by another program (the desktop app, a service, CI) its own standard input may not be a valid handle,
and starting any child that inherits it fails with `OSError: [Errno 9] Bad file descriptor`. Every child therefore gets an explicit empty
stdin unless the caller asks for a pipe, and on Windows no console window flashes up for each ffmpeg call.
"""
from __future__ import annotations
import subprocess, sys

_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _defaults(kw: dict) -> dict:
    if "stdin" not in kw and "input" not in kw:
        kw["stdin"] = subprocess.DEVNULL
    if _FLAGS and "creationflags" not in kw:
        kw["creationflags"] = _FLAGS
    return kw


def run(*a, **kw):
    return subprocess.run(*a, **_defaults(kw))


def Popen(*a, **kw):  # noqa: N802 - mirrors subprocess.Popen
    return subprocess.Popen(*a, **_defaults(kw))
