"""Clean plate: a recording (or picture) of the EMPTY screen, same camera, same lighting, same framing.

The keyer normally has to *guess* how the screen would look behind the person (a smooth blurred model). Wrinkles, folds, a lit hot spot or a
shadow band on the screen are then guessed wrong and leave haze or holes next to the person. With a clean plate the keyer KNOWS the screen
pixel by pixel; only the slow brightness drift between the plate and the live footage (exposure, lights changing when the person walks in) is
corrected. If the plate does not fit a frame (camera moved, different framing) the keyer falls back to its own estimate for that frame.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np

PLATE_FRAMES = 48          # a plate video is averaged over its first ~2 s: sensor noise averages out, the screen does not move


def load_plate(path: str, W: int, H: int) -> np.ndarray:
    """RGB float32 (H, W, 3) in 0..1, scaled to the frame size. Pictures are used as they are, videos are averaged over their first frames."""
    import cv2
    p = Path(path)
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        im = cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if im is None: raise RuntimeError(f"The clean plate picture could not be read: {p.name}")
        return cv2.resize(im[:, :, ::-1], (W, H), interpolation=cv2.INTER_AREA if im.shape[1] > W else cv2.INTER_LINEAR).astype(np.float32) / 255.0
    from .frames import decode
    acc, n = None, 0
    for fr in decode(str(p), W, H, 25.0, 0, PLATE_FRAMES, None, flags="area"):
        acc = fr.astype(np.float32) if acc is None else acc + fr
        n += 1
    if not n: raise RuntimeError(f"The clean plate video could not be read: {p.name}")
    return (acc / n) / 255.0


def plate_identity(path: str | None) -> list:
    """what goes into a matte's cache key, so changing the plate file recomputes the matte"""
    if not path: return []
    st = Path(path).stat()
    return ["plate", str(Path(path).resolve()), st.st_size, st.st_mtime_ns]
