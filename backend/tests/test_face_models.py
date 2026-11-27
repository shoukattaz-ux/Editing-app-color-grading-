"""Face restoration with a choice of face AI: gfpgan (default), gpen512 (strongest detail) and codeformer (fidelity control)."""
import re
import numpy as np
import pytest
from evp.blueprint import _coerce
from evp.ml import models, restore
from evp.registry import OPERATIONS
from conftest import bp

SPEC = OPERATIONS["face_restoration"].params
P = dict(strength=1.0, identity=0.0, min_face=24, detection_confidence=0.6, max_faces=1, model="gfpgan", fidelity=0.6)


@pytest.fixture(autouse=True)
def _fresh_cache():
    restore._NET_CACHE.clear(); yield; restore._NET_CACHE.clear()


class Sess:
    """records how the network is called and returns a picture that depends on the model"""
    def __init__(self, value): self.calls, self.value = [], value
    def get_providers(self): return ["CPUExecutionProvider"]
    def run(self, outputs, feeds):
        self.calls.append((outputs, {k: (np.asarray(v).dtype, np.asarray(v).shape) for k, v in feeds.items()}, {k: float(v) for k, v in feeds.items() if k == "weight"}))
        return [np.full((1, 3, 512, 512), self.value, np.float32)]


def net(model, sess, fidelity=0.6):
    r = restore.FaceRestorer(64, 64, dict(P, model=model, fidelity=fidelity), sess=sess)
    return r, r.net(np.full((512, 512, 3), 0.4, np.float32))


def test_the_model_choice_and_fidelity_are_validated():
    assert SPEC["model"].default == "gfpgan" and set(SPEC["model"].enum) == {"gfpgan", "gpen512", "codeformer"}
    assert _coerce("model", SPEC["model"], "gpen512") == "gpen512"
    for bad in ("remini", "", 3): 
        with pytest.raises(ValueError): _coerce("model", SPEC["model"], bad)
    assert _coerce("fidelity", SPEC["fidelity"], 0.2) == 0.2
    with pytest.raises(ValueError): _coerce("fidelity", SPEC["fidelity"], 1.5)


def test_codeformer_gets_its_fidelity_weight_and_the_others_do_not():
    s = Sess(0.5); net("codeformer", s, 0.3)
    outs, feeds, w = s.calls[0]
    assert outs == ["output"] and feeds["weight"][0] == np.dtype("float64") and w["weight"] == pytest.approx(0.3)          # the model wants a float64 scalar
    for m in ("gfpgan", "gpen512"):
        s = Sess(0.5); net(m, s)
        assert s.calls[0][0] is None and "weight" not in s.calls[0][1]


def test_each_model_keeps_its_own_cached_answer():
    a = net("gfpgan", Sess(0.2))[1]
    b = net("gpen512", Sess(0.9))[1]
    c = net("codeformer", Sess(0.6), 0.1)[1]
    d = net("codeformer", Sess(0.7), 0.9)[1]
    assert not np.allclose(a, b) and not np.allclose(b, c) and not np.allclose(c, d)               # the same face picture with another model or fidelity is not reused


def test_the_model_store_entries_are_well_formed():
    for k in ("gpen512", "codeformer"):
        m = models.MODELS[k]
        assert m.url.startswith("https://github.com/") and re.fullmatch(r"[0-9a-f]{64}", m.sha256) and m.size > 1e8 and m.file.endswith(".onnx")
    assert "non-commercial" in models.MODELS["codeformer"].license and "non-commercial" in models.MODELS["gpen512"].license
    assert "Apache" in models.MODELS["gfpgan"].license


def test_validation_names_the_chosen_model_and_its_size(prep, monkeypatch):
    monkeypatch.setattr(models, "is_installed", lambda n: n != "gpen512")
    ops = [{"id": "f", "type": "face_restoration", "start": 0, "parameters": {"model": "gpen512"}}]
    r = prep(bp(ops=ops))
    note = [w for w in r.report.warnings if w.code == "MODEL_DOWNLOAD" and w.op_id == "f"]
    assert note and "284 MB" in note[0].message and "non-commercial" in note[0].message


def test_estimates_follow_the_model():
    e = {m: restore.estimate_seconds_per_frame(False, 1, m) for m in restore.ONNX_MODELS}
    assert e["gfpgan"] < e["gpen512"] < e["codeformer"] and restore.estimate_seconds_per_frame(True, 1, "gpen512") < e["gpen512"]


@pytest.mark.skipif(not (models.is_installed("gpen512") and models.is_installed("codeformer")), reason="the face models are not downloaded here")
def test_the_real_models_run_and_return_a_face_of_the_same_size():
    x = np.random.default_rng(1).random((1, 3, 512, 512)).astype(np.float32) * 2 - 1
    for m in ("gpen512", "codeformer"):
        r = restore.FaceRestorer(64, 64, dict(P, model=m), sess=restore.session(m))
        y = r._run(x)
        assert y.shape == (1, 3, 512, 512) and np.isfinite(y).all()
