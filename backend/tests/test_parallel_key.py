"""Full-range green-screen keys run on every core (one piece per core). The result must equal the one-core key frame for frame."""
import subprocess
import numpy as np
from evp.samples import make_greenscreen
from evp.engine import prepare, ensure_mattes
from evp.security import PathPolicy
from evp.media import ffmpeg_bin


def frames_of(path, w, h, pix="gray", ch=1):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", pix, "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, ch).astype(float)


def test_parallel_key_equals_the_single_core_key(tmp_path, monkeypatch):
    v = str(make_greenscreen(tmp_path / "gs.mp4", 5, size="640x360"))
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "edge_soften": 1.0}}]
    refs = {}
    for threads in ("1", "4"):
        monkeypatch.setenv("EVP_KEY_THREADS", threads)
        p = prepare({"blueprint_version": "1.0", "operations": ops}, v, PathPolicy([tmp_path]), tmp_path, strict=True)
        ensure_mattes(p.plan, v, str(tmp_path / f"cache{threads}"))
        refs[threads] = p.plan.mattes["k"]
    one, four = refs["1"], refs["4"]
    assert four.frames == one.frames >= 100 and four.screen == one.screen
    a1, a4 = frames_of(one.path, 640, 360), frames_of(four.path, 640, 360)
    assert a1.shape == a4.shape                                             # same number of frames, nothing lost or doubled at the joins
    per_frame = np.abs(a1 - a4).reshape(len(a1), -1)
    assert per_frame.mean(1).max() < 0.05 and np.percentile(per_frame, 99.9) <= 1     # the same matte, frame for frame (incl. where pieces meet)
    shifted = np.abs(a1[1:] - a4[:-1]).mean()
    assert shifted > 10 * max(0.01, per_frame.mean())                      # the subject moves: a one-frame slip would show
    c1, c4 = frames_of(one.fgr_path, 640, 360, "rgb24", 3), frames_of(four.fgr_path, 640, 360, "rgb24", 3)
    vis = np.abs(c1 - c4) * (a1 / 255.0)
    assert vis.mean() < 0.2 and np.percentile(vis, 99.9) < 4               # the subject colours too (stored with near-lossless compression)
