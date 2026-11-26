"""Exports and preview videos render the timeline in parallel time segments, joined without re-encoding, with the sound rendered once.
The result must equal a one-piece render: every frame, in order, and continuous sound - also on a cut timeline."""
import subprocess
import numpy as np
import pytest
from evp.samples import make_greenscreen
from evp.engine import prepare, Job
from evp.compiler import RenderOptions
from evp.security import PathPolicy
from evp.media import ffmpeg_bin


def frames(path, w=320, h=180):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(float)


def sound(path):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(float)


@pytest.mark.parametrize("cut", [False, True])
def test_parallel_render_equals_one_piece(tmp_path, monkeypatch, cut):
    v = str(make_greenscreen(tmp_path / "gs.mp4", 9, size="640x360"))
    blue = {"blueprint_version": "1.0", "output": {"crf": 0}, "operations": [
        {"id": "g", "type": "color_grading", "start": 0, "parameters": {"saturation": 0.3, "contrast": 0.2}},
        {"id": "t", "type": "text_overlay", "start": 1, "end": 7, "parameters": {"text": "Hello"}},
        {"id": "a", "type": "audio_gain", "start": 0, "parameters": {"gain_db": -3, "fade_in": 0.5, "fade_out": 0.5}}]}
    if cut: blue["clips"] = [{"id": "c1", "start": 0.5, "end": 4.0}, {"id": "c2", "start": 5.0, "end": 8.8}]
    outs = {}
    for segs in ("1", "3"):
        monkeypatch.setenv("EVP_RENDER_SEGMENTS", segs)
        p = prepare(blue, v, PathPolicy([tmp_path]), tmp_path, strict=True)
        assert p.plan, [e.message for e in p.report.errors]
        out = str(tmp_path / f"out{segs}.mp4")
        (tmp_path / "w").mkdir(exist_ok=True)
        j = Job(p.plan, v, out, RenderOptions(encoder="cpu"), str(tmp_path / "w")); j.run()
        assert j.state == "done", (j.error, j.log[-5:])
        assert ("parallel segments" in " ".join(j.log)) == (segs == "3")
        outs[segs] = out
    a, b = frames(outs["1"]), frames(outs["3"])
    assert a.shape == b.shape                                                      # the same number of frames: none lost or doubled at the joins
    per = np.abs(a - b).reshape(len(a), -1).mean(1)
    assert per.max() < 0.05                                                        # lossless output: every frame identical
    assert np.abs(a[1:] - b[:-1]).mean() > 0.08 > 2 * per.max()                    # the subject moves: a one-frame slip would show (0.14 here)
    sa, sb = sound(outs["1"]), sound(outs["3"])
    assert abs(len(sa) - len(sb)) <= 80                                           # the same length (within 10 ms)
    n = min(len(sa), len(sb))
    assert np.abs(sa[:n] - sb[:n]).mean() < 0.02 * max(1.0, np.abs(sa).mean())     # the same, continuous sound
