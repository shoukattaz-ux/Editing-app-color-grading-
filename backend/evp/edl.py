"""Edit decision list: which pieces of the source (in output-grid frames) make up the timeline, in order.

A *piece* is a half-open source frame range (a, b). The timeline is the pieces back to back (ripple, no gaps).
Everything downstream (FFmpeg graph, AI analysis passes, previews) uses these helpers so that timeline frame N
always means the same source frame everywhere.
"""
from __future__ import annotations
import hashlib

Piece = tuple[int, int]


def total_frames(pieces: list[Piece]) -> int:
    return sum(b - a for a, b in pieces)


def source_frame(pieces: list[Piece], t: int) -> int:
    """source frame shown at timeline frame t"""
    off = 0
    for a, b in pieces:
        if t < off + (b - a):
            return a + (t - off)
        off += b - a
    raise IndexError(f"timeline frame {t} is beyond the end ({off})")


def pieces_for_range(pieces: list[Piece], first: int, count: int | None) -> list[Piece]:
    """The source pieces covering timeline frames [first, first+count) (count None = to the end)."""
    end = None if count is None else first + count
    out: list[Piece] = []
    off = 0
    for a, b in pieces:
        n = b - a
        lo, hi = max(first, off), (off + n if end is None else min(end, off + n))
        if lo < hi:
            out.append((a + (lo - off), a + (hi - off)))
        off += n
        if end is not None and off >= end:
            break
    return out


def is_in_order(pieces: list[Piece]) -> bool:
    """True when pieces are increasing and non-overlapping in the source (one decoder pass is enough)."""
    return all(p[1] <= q[0] for p, q in zip(pieces, pieces[1:]))


def is_identity(pieces: list[Piece], src_frames: int) -> bool:
    return len(pieces) == 1 and pieces[0][0] == 0 and pieces[0][1] >= src_frames


def key(pieces: list[Piece] | None) -> str:
    return "" if not pieces else hashlib.sha1(repr(pieces).encode()).hexdigest()[:10]
