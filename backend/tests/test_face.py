"""Face tone correction + relighting. Mechanics are tested on a synthetic clip with a drawn face-like
pattern only where the detector is not required; detector-dependent checks need a real person clip
(EVP_TEST_PERSON_VIDEO) because YuNet (correctly) finds no face in synthetic shapes."""
import os
import numpy as np, cv2, pytest
from conftest import bp
from evp.ml import models

if not hasattr(cv2, "FaceDetectorYN"):
    pytest.skip("OpenCV without FaceDetectorYN", allow_module_level=True)


@pytest.fixture(scope="module", autouse=True)
def model():
    try: models.ensure_model("yunet")
    except models.ModelError as e: pytest.skip(str(e))


FT = {"id": "f", "type": "face_tone_correction", "start": 0, "end": 3}
RL = {"id": "r", "type": "face_relighting", "start": 0, "end": 3}


def test_face_ops_validate_with_defaults(prep):
    p = prep(bp(ops=[FT, RL]))
    assert p.report.ok, p.report.errors
    ops = {o.id: o for o in p.plan.steps}
    assert ops["f"].params["auto_exposure"] and ops["r"].params["key_angle"] == -45.0
    # face pass runs after compositing and before the global grade
    p2 = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 3}, FT,
                      {"id": "k", "type": "chroma_key", "start": 0, "end": 3}]))
    assert [o.id for o in p2.plan.steps] == ["k", "f", "g"]


@pytest.mark.parametrize("params", [{"warmth": 500}, {"target_brightness": 0.1}, {"key_angle": 180}, {"bogus": 1}])
def test_face_param_limits(prep, params):
    assert not prep(bp(ops=[{**FT, "type": "face_relighting" if "key_angle" in params else FT["type"], "parameters": params}])).report.ok


def test_no_face_means_no_change_and_is_reported(prep, media, tmp_path):
    """Synthetic clip has no face: output must equal the unprocessed render and the job must say so."""
    from evp.engine import render_frame, Job
    from evp.compiler import RenderOptions
    base = render_frame(prep(bp()).plan, media["video"], 1.0, None, str(tmp_path))
    p = prep(bp(ops=[{**FT, "parameters": {"exposure": 1.5, "warmth": 80}}]))
    out = render_frame(p.plan, media["video"], 1.0, None, str(tmp_path))
    a, b = (cv2.imdecode(np.frombuffer(x, np.uint8), 1).astype(int) for x in (base, out))
    assert np.abs(a - b).max() <= 2
    j = Job(p.plan, media["video"], str(tmp_path / "o.mp4"), RenderOptions(preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done" and any("no face detected" in l for l in j.log)


def test_weight_maths_tone_and_relight():
    """Unit-test the weight generators with a fake tracked face (no detector involved)."""
    from evp.ml.face import Track, _weights_tone, _weights_relight, UP_GAIN, DOWN_GAIN
    from evp.registry import OPERATIONS
    defaults = lambda t: {k: v.default for k, v in OPERATIONS[t].params.items()}
    img = np.full((200, 200, 3), (60, 45, 35), np.uint8)               # dark, skin-ish frame
    mk = lambda: Track(np.array([60., 50., 80., 100.]), np.array([[85, 85], [115, 85], [100, 105], [88, 125], [112, 125]], float), 0.9)
    p = defaults("face_tone_correction")
    R, G, B = _weights_tone(img, [(mk(), 1.0)], p, None)
    assert G[100, 100] > 0.1 and B.max() == 0                         # dark face -> brightened
    assert R[100, 100] > 0.5 and R[5, 5] == 0 and G[5, 5] == 0        # only inside the face
    p.update(auto_exposure=False, exposure=-1.0)
    R, G, B = _weights_tone(img, [(mk(), 1.0)], p, None)
    assert abs(B[100, 100] - 0.5 / (1 - DOWN_GAIN)) < 0.05             # -1 EV -> gain 0.5
    q = defaults("face_relighting"); q.update(key_angle=-90, key_elevation=0, fill=0, shadow_depth=0.5)
    R, G, B = _weights_relight(img, [(mk(), 1.0)], q, None)
    assert G[100, 70] > G[100, 130] and B[100, 130] > B[100, 70]       # light from frame-left


@pytest.mark.skipif(not os.environ.get("EVP_TEST_PERSON_VIDEO"), reason="set EVP_TEST_PERSON_VIDEO to a clip with a person")
def test_real_face_brightened_background_untouched(tmp_path):
    from evp.engine import prepare, render_frame
    from evp.security import PathPolicy
    v = os.environ["EVP_TEST_PERSON_VIDEO"]
    mk = lambda ops: prepare(bp(ops=ops), v, PathPolicy([tmp_path])).plan
    ops = [{"id": "f", "type": "face_tone_correction", "start": 0, "parameters": {"auto_exposure": False, "exposure": 1.0}}]
    a = cv2.imdecode(np.frombuffer(render_frame(mk([]), v, 1.0, 360, str(tmp_path)), np.uint8), 1).astype(float)
    b = cv2.imdecode(np.frombuffer(render_frame(mk(ops), v, 1.0, 360, str(tmp_path)), np.uint8), 1).astype(float)
    d = (b - a).mean(-1)
    det = cv2.FaceDetectorYN.create(str(models.ensure_model("yunet")), "", (a.shape[1], a.shape[0]), 0.6)
    _, f = det.detect(a.astype(np.uint8)); x, y, w, h = f[0][:4].astype(int)
    assert d[y + h // 3:y + 2 * h // 3, x + w // 4:x + 3 * w // 4].mean() > 15      # face brighter
    assert abs(d[:20, :20].mean()) < 2 and abs(d[-20:, :20].mean()) < 2            # corners untouched
