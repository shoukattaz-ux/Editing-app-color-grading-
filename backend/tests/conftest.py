import sys, copy
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evp.samples import make_greenscreen, make_background, make_cube_lut
from evp.security import PathPolicy
from evp.engine import prepare


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return {"dir": d, "video": make_greenscreen(d / "gs.mp4", 3), "bg": make_background(d / "bg.png"),
            "lut": make_cube_lut(d / "warm.cube"), "silent": make_greenscreen(d / "silent.mp4", 2, audio=False)}


@pytest.fixture
def policy(media):
    return PathPolicy([media["dir"]])


def bp(ops=None, assets=None, **kw):
    d = {"blueprint_version": "1.0", "project": {"name": "t"}, "output": {"resolution": "source", "fps": "source"},
         "assets": assets if assets is not None else [], "operations": ops or []}
    d.update(kw)
    return d


@pytest.fixture
def prep(media, policy):
    def _p(blueprint, video=None):
        return prepare(blueprint, video or media["video"], policy, media["dir"])
    return _p


def pixel(png: bytes, x: int, y: int):
    import cv2, numpy as np
    im = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    b, g, r = (int(v) for v in im[y, x])
    return r, g, b


# ---------- cut/EDL helpers
def make_ramp(path, seconds=4, fps="25", audio=True, size="64x48"):
    """Frame N has luma 16+2N (unique per frame); audio is a sine whose frequency is 200+100*floor(t) Hz."""
    import subprocess
    from evp.media import ffmpeg_bin
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={size}:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"aevalsrc='sin(2*PI*(200+100*floor(t))*t)':s=16000:d={seconds}"]
    cmd += ["-vf", "geq=lum='16+2*N':cb=128:cr=128", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p"]
    cmd += (["-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]) + [str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def frame_ids(path, w=64, h=48):
    """list of source frame indices shown by each frame of `path` (inverse of the ramp luma)."""
    import subprocess, numpy as np
    from evp.media import ffmpeg_bin
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fs = w * h * 3 // 2
    n = len(raw) // fs
    y = np.frombuffer(raw[:n * fs], np.uint8).reshape(n, fs)[:, :w * h].astype(float).mean(1)
    return [int(round((v - 16) / 2)) for v in y]


def audio_freqs(path, window=0.5, sr=16000):
    """dominant frequency of consecutive `window`-second chunks of the audio track."""
    import subprocess, numpy as np
    from evp.media import ffmpeg_bin
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(float)
    n = int(window * sr)
    out = []
    for i in range(0, len(a) - n + 1, n):
        spec = np.abs(np.fft.rfft(a[i:i + n] * np.hanning(n)))
        out.append(int(round(np.argmax(spec) * sr / n / 10.0) * 10))
    return out, len(a) / sr


@pytest.fixture(scope="session")
def ramp(tmp_path_factory):
    d = tmp_path_factory.mktemp("ramp")
    return {"dir": d, "video": make_ramp(d / "ramp.mp4"), "silent": make_ramp(d / "silent.mp4", audio=False),
            "ntsc": make_ramp(d / "ntsc.mp4", fps="30000/1001", audio=False)}


def export_ids(prep_fn, ramp, blueprint, src=None, name="out.mp4"):
    """render an export (lossless) and return (frame ids, path)."""
    from evp.engine import Job
    from evp.compiler import RenderOptions
    src = src or ramp["video"]
    p = prep_fn(blueprint, src)
    assert p.plan, [e.message for e in p.report.errors]
    out = ramp["dir"] / name
    j = Job(p.plan, src, str(out), RenderOptions(crf=0, preset="ultrafast"), str(ramp["dir"]))
    j.run()
    assert j.state == "done", j.error
    return frame_ids(out), out
