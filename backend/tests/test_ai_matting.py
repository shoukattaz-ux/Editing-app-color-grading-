"""AI background removal (Robust Video Matting). Skipped when onnxruntime or the model is unavailable.

Synthetic clips contain no person, so these tests check the *mechanics* (frame alignment, caching,
preview == export, cancel, error paths) rather than segmentation quality. Quality was checked by eye on
real footage; set EVP_TEST_PERSON_VIDEO=/path/clip.mp4 to also run the person test below.
"""
import os, threading
from pathlib import Path
import numpy as np, cv2, pytest
from conftest import bp
from evp.ml import models

ok, why = models.runtime_available()
pytestmark = pytest.mark.skipif(not ok, reason=why)


@pytest.fixture(scope="module", autouse=True)
def model():
    try: models.ensure_model("rvm")
    except models.ModelError as e: pytest.skip(str(e))


@pytest.fixture(scope="module")
def ramp(tmp_path_factory):
    """Each frame has a different brightness, so any frame misalignment is detectable."""
    from evp.media import ffmpeg_bin
    import subprocess
    p = tmp_path_factory.mktemp("ai") / "ramp.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x96:r=25:d=2", "-vf", "geq=lum='mod(N*9\\,255)':cb=128:cr=128",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(p)], check=True)
    return str(p)


AI = {"id": "k", "type": "background_removal", "start": 0, "end": 2, "parameters": {"mode": "ai", "edge_color_fix": False, "despeckle": False, "matte_threshold": 0}}


def test_ai_mode_validates(prep):
    p = prep(bp(ops=[AI]))
    assert p.report.ok, p.report.errors
    assert p.report.blueprint.operations[0].params["detail"] == "fast"


def test_runtime_missing_is_a_clear_error(prep, monkeypatch):
    monkeypatch.setattr(models, "runtime_available", lambda: (False, "onnxruntime is not installed"))
    p = prep(bp(ops=[AI]))
    assert [i.code for i in p.report.errors] == ["MODEL_RUNTIME_MISSING"]


def test_matte_decode_is_frame_aligned_for_late_start(ramp, media, tmp_path):
    """Regression: trimming the decode used to make FFmpeg prepend duplicate frames (matte shifted in time)."""
    from evp.ml import matting
    seen = []
    class Spy:
        def __call__(self, rgb, ds):
            seen.append(int(rgb.mean())); return np.ones(rgb.shape[:2], np.float32), rgb.astype(np.float32) / 255
        def reset(self): pass
    from evp.blueprint import Operation
    op = Operation("k", "background_removal", 0.8, 1.2, 20, 30, {"model": "rvm", "detail": "fast", "matte_threshold": 0.0, "despeckle": False, "edge_color_fix": False})
    orig = matting._model
    matting._model = lambda: Spy()
    try:
        ref = matting.generate_matte(ramp, op, 160, 96, 25.0, 20, 10, str(tmp_path))
    finally:
        matting._model = orig
    assert ref.frames == 10 and len(seen) == 10
    rgb_of = lambda n: max(0.0, (n * 9 % 255 - 16) * 255 / 219)  # limited-range luma -> RGB
    assert abs(seen[0] - rgb_of(20)) <= 3, seen                  # first decoded frame is frame 20, not a duplicate of frame 0
    assert abs(seen[-1] - rgb_of(29)) <= 3, seen


def test_preview_matches_export_and_cache_reused(prep, media, tmp_path):
    from evp.engine import render_frame, Job
    from evp.compiler import RenderOptions
    ops = [dict(AI, parameters={**AI["parameters"], "background_color": "#ff0000"}),
           {"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"saturation": 1.2}}]
    p = prep(bp(ops=[{**ops[0], "end": 3}, ops[1]]))
    out = tmp_path / "ai.mp4"
    j = Job(p.plan, media["video"], str(out), RenderOptions(crf=0, preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    assert len(list((tmp_path / "mattes").glob("matte_*.mkv"))) == 1
    for t in (0.5, 1.7):
        pv = cv2.imdecode(np.frombuffer(render_frame(p.plan, media["video"], t, None, str(tmp_path)), np.uint8), 1)
        cap = cv2.VideoCapture(str(out)); cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * 30)); _, ex = cap.read()
        assert np.abs(pv.astype(int) - ex.astype(int)).mean() < 4, t


def test_ai_matte_cancel(prep, media, tmp_path):
    from evp.ml.matting import generate_matte, Cancelled
    p = prep(bp(ops=[{**AI, "end": 3}]))
    ev = threading.Event(); ev.set()
    with pytest.raises(Cancelled):
        generate_matte(media["video"], p.plan.steps[0], 320, 180, 30.0, 0, 90, str(tmp_path), cancel=ev)
    assert not list((tmp_path / "mattes").glob("*.mkv"))


@pytest.mark.skipif(not os.environ.get("EVP_TEST_PERSON_VIDEO"), reason="set EVP_TEST_PERSON_VIDEO to a clip with a person")
def test_person_is_kept_background_removed(policy, tmp_path):
    from evp.engine import prepare, render_frame
    from evp.security import PathPolicy
    v = os.environ["EVP_TEST_PERSON_VIDEO"]
    p = prepare(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "ai", "background_color": "#00ff00"}}]), v, PathPolicy([tmp_path]))
    im = cv2.imdecode(np.frombuffer(render_frame(p.plan, v, 1.0, 360, str(tmp_path)), np.uint8), 1)
    green = (im[..., 1] > 200) & (im[..., 0] < 60) & (im[..., 2] < 60)
    assert 0.15 < green.mean() < 0.9      # some background replaced, person (not green) remains
