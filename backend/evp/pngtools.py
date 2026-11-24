"""Tiny PNG helpers (no image library needed)."""
from __future__ import annotations
import struct

SIG = b"\x89PNG\r\n\x1a\n"
# chunks that tell a viewer to colour-manage the picture; without them browsers show the stored values as-is (sRGB)
COLOUR_CHUNKS = {b"cICP", b"cHRM", b"gAMA", b"iCCP", b"sRGB", b"sBIT"}


def strip_colour_tags(png: bytes) -> bytes:
    """Remove colour-management chunks so the file is displayed with exactly the pixel values it stores.
    FFmpeg tags frames from BT.709 video with cICP/cHRM/gAMA, and browsers then convert them - the picture (and anything
    measured from it) no longer matches the real RGB values. Pixel data is untouched."""
    if not png.startswith(SIG): return png
    out, i = [SIG], len(SIG)
    while i + 8 <= len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        kind, end = png[i + 4:i + 8], i + 12 + n
        if kind not in COLOUR_CHUNKS: out.append(png[i:end])
        i = end
    return b"".join(out)


def chunk_names(png: bytes) -> list[str]:
    names, i = [], len(SIG)
    while i + 8 <= len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        names.append(png[i + 4:i + 8].decode("ascii", "replace")); i += 12 + n
    return names
