"""AI face restoration (GFPGAN): weight loading without PyTorch, alignment, identity mixing, the mask guarantee, the real network."""
import io
import pickle
import struct
import subprocess
import sys
import types
from collections import OrderedDict

import cv2
import numpy as np
import pytest

from conftest import bp
from evp.compiler import compile_plan, RenderOptions
from evp.engine import prepare
from evp.media import ffmpeg_bin
from evp.ml import legacy_pth, restore, gfpgan
from evp.ml.face import Track
from evp.ml.models import is_installed
from evp.security import PathPolicy


@pytest.fixture(autouse=True)
def fresh_network_cache():
    restore._NET_CACHE.clear()
    yield
    restore._NET_CACHE.clear()


# ---------------------------------------------------------------------------------------------- the old PyTorch checkpoint format
def write_legacy(path, tensors: dict):
    """a minimal legacy (pre-zip) torch checkpoint: header pickles, the object with persistent storage ids, the storage keys, the raw data"""
    tu = types.ModuleType("torch._utils"); tch = types.ModuleType("torch")
    def _rebuild_tensor_v2(*a): raise RuntimeError
    _rebuild_tensor_v2.__module__, _rebuild_tensor_v2.__qualname__ = "torch._utils", "_rebuild_tensor_v2"
    tu._rebuild_tensor_v2 = _rebuild_tensor_v2
    class FloatStorage: pass
    FloatStorage.__module__, FloatStorage.__qualname__ = "torch", "FloatStorage"
    tch.FloatStorage = FloatStorage
    saved = {k: sys.modules.get(k) for k in ("torch", "torch._utils")}
    sys.modules["torch"], sys.modules["torch._utils"] = tch, tu
    try:
        class T:
            def __init__(self, key, arr): self.key, self.arr = key, arr
            def __reduce__(self): return (_rebuild_tensor_v2, (("S", self.key, self.arr.size), 0, tuple(self.arr.shape), tuple(s // 4 for s in self.arr.strides), False, OrderedDict()))
        class P(pickle.Pickler):
            def persistent_id(self, obj):
                if isinstance(obj, tuple) and obj and obj[0] == "S": return ("storage", FloatStorage, str(obj[1]), "cpu", obj[2])
        sd = OrderedDict((k, T(i, np.ascontiguousarray(v, np.float32))) for i, (k, v) in enumerate(tensors.items()))
        with open(path, "wb") as f:
            pickle.dump(legacy_pth.MAGIC, f, 2); pickle.dump(1001, f, 2); pickle.dump({"protocol_version": 1001, "little_endian": True}, f, 2)
            P(f, 2).dump({"params_ema": sd})
            pickle.dump([str(i) for i in range(len(tensors))], f, 2)
            for v in tensors.values():
                v = np.ascontiguousarray(v, np.float32); f.write(struct.pack("<q", v.size)); f.write(v.tobytes())
    finally:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v


def test_legacy_checkpoint_is_read_without_torch(tmp_path):
    w = {"a.weight": np.arange(24, dtype=np.float32).reshape(2, 3, 2, 2), "a.bias": np.array([1.5, -2.0], np.float32), "b": np.random.rand(5, 4).astype(np.float32)}
    p = tmp_path / "m.pth"; write_legacy(p, w)
    assert legacy_pth.is_legacy(p)
    sd = legacy_pth.load_state_dict(p)
    assert set(sd) == set(w) and all(np.array_equal(sd[k], w[k]) for k in w)


def test_a_model_file_cannot_run_code(tmp_path):
    class Evil:
        def __reduce__(self): return (__import__("os").system, ("echo pwned > " + str(tmp_path / "pwned"),))
    p = tmp_path / "evil.pth"
    with open(p, "wb") as f:
        pickle.dump(legacy_pth.MAGIC, f, 2); pickle.dump(1001, f, 2); pickle.dump({}, f, 2); pickle.dump({"params": Evil()}, f, 2)
    with pytest.raises(pickle.UnpicklingError):
        legacy_pth.load_state_dict(p)
    assert not (tmp_path / "pwned").exists()


# ---------------------------------------------------------------------------------------------- alignment and identity
def test_alignment_maps_the_landmarks_onto_the_template():
    ang, sc, tx, ty = np.radians(12), 0.8, 300, 200                         # a face that is smaller, turned and somewhere else in the frame
    R = sc * np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    lm = (restore.TEMPLATE @ R.T + [tx, ty]).astype(np.float32)
    M = restore.align(lm)
    back = lm @ M[:, :2].T + M[:, 2]
    assert np.abs(back - restore.TEMPLATE).max() < 0.5


def test_identity_keeps_the_originals_colours_and_takes_only_fine_detail():
    rng = np.random.default_rng(1)
    orig = np.full((512, 512, 3), (0.8, 0.6, 0.5), np.float32)
    rest = np.full((512, 512, 3), (0.7, 0.7, 0.7), np.float32) + (rng.random((512, 512, 1)) - 0.5).astype(np.float32) * 0.1     # other colour + fine texture
    full, detail = restore.mix_identity(orig, rest, 0.0), restore.mix_identity(orig, rest, 1.0)
    assert np.allclose(full, rest)
    assert np.abs(detail.mean((0, 1)) - orig.mean((0, 1))).max() < 0.01              # colours: the original's
    assert detail.std() > 0.5 * rest.std()                                           # texture: the network's


# ---------------------------------------------------------------------------------------------- the maths of the rebuilt network
def test_modulated_convolution_equals_the_reference_formula():
    """conv(x * style) * demod must equal the StyleGAN2 definition conv(weight * style, then demodulated) - the graph relies on it"""
    import onnxruntime as ort
    from onnx import helper, TensorProto
    rng = np.random.default_rng(3)
    cin, cout, k = 4, 3, 3
    sd = {"m.modulation.weight": rng.normal(size=(cin, 8)).astype(np.float32), "m.modulation.bias": rng.normal(size=cin).astype(np.float32) + 1,
          "m.weight": rng.normal(size=(1, cout, cin, k, k)).astype(np.float32)}
    g = gfpgan._G()
    out = gfpgan._modulated(g, "x", "style", sd, "m", True, False, 1)
    graph = helper.make_graph(g.nodes, "t", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, cin, 6, 6]), helper.make_tensor_value_info("style", TensorProto.FLOAT, [1, 8])],
                              [helper.make_tensor_value_info(out, TensorProto.FLOAT, [1, cout, 6, 6])], g.inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)]); model.ir_version = 8
    x, st = rng.normal(size=(1, cin, 6, 6)).astype(np.float32), rng.normal(size=(1, 8)).astype(np.float32)
    got = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"]).run(None, {"x": x, "style": st})[0]
    s = st @ sd["m.modulation.weight"].T + sd["m.modulation.bias"]                       # reference: per-sample weights, demodulated, then convolved
    w = sd["m.weight"][0] * s.reshape(1, cin, 1, 1)
    w = w * (1.0 / np.sqrt((w ** 2).sum((1, 2, 3)) + 1e-8)).reshape(cout, 1, 1, 1)
    xp = np.pad(x[0], ((0, 0), (1, 1), (1, 1)))
    ref = np.zeros((cout, 6, 6), np.float32)
    for o in range(cout):
        for i in range(6):
            for j in range(6): ref[o, i, j] = (xp[:, i:i + k, j:j + k] * w[o]).sum()
    assert np.abs(got[0] - ref).max() < 1e-4


# ---------------------------------------------------------------------------------------------- the per-frame restorer with a stand-in network
class StubTracker:
    """like the real tracker: the same Track object lives on from frame to frame (its landmarks follow the detections)"""
    def __init__(self, boxes_lms): self.items, self.i, self.tracks = boxes_lms, 0, {}
    def update(self, bgr):
        res = []
        for k, (box, lm) in enumerate(self.items(self.i)):
            t = self.tracks.setdefault(k, Track(box=np.array(box, float), lm=np.array(lm, float), score=0.95))
            t.box, t.lm = np.array(box, float), np.array(lm, float)
            res.append((t, 1.0))
        self.i += 1
        return res


def face_at(cx, cy, w):
    """a face of width w centred at (cx, cy): landmarks placed like the template"""
    k = w / 250.0
    lm = (restore.TEMPLATE - [256, 290]) * k + [cx, cy]
    return [cx - w / 2, cy - w * 0.6, w, w * 1.2], lm


P = {"strength": 1.0, "identity": 0.0, "max_faces": 3, "min_face": 48, "detection_confidence": 0.6}


def restorer(items, runner):
    r = restore.FaceRestorer(640, 480, P, sess=object(), runner=runner)
    r.tracker = StubTracker(items)
    return r


def test_only_the_face_changes_and_the_mask_is_zero_elsewhere():
    frame = np.full((480, 640, 3), 100, np.uint8)
    box, lm = face_at(320, 240, 200)
    r = restorer(lambda i: [(box, lm)], lambda x: np.clip(x + 0.4, -1, 1))              # a "network" that brightens
    layer, mask = r(frame, P)
    assert mask[240, 320] > 200 and mask[5, 5] == 0 and mask[470, 630] == 0               # inside the face: on; far away: exactly off
    assert layer[240, 320, 0] > 130                                                      # the restored colour is in the layer
    ys, xs = np.where(mask > 40); assert xs.min() > 130 and xs.max() < 510 and ys.min() > 30 and ys.max() < 450      # (where it is clearly on) it stays around the face


def test_strength_scales_the_mask_and_small_or_extra_faces_are_skipped():
    frame = np.full((480, 640, 3), 100, np.uint8)
    big, small = face_at(200, 240, 200), face_at(520, 240, 30)
    run = lambda x: x
    full = restorer(lambda i: [big], run)(frame, P)[1]
    half = restorer(lambda i: [big], run)(frame, {**P, "strength": 0.5})[1]
    assert abs(half.max() / full.max() - 0.5) < 0.02
    m = restorer(lambda i: [big, small], run)(frame, P)[1]
    assert m[240, 520] == 0 and m[240, 200] > 0                                          # the 30 px face is below min_face: not touched
    two = restorer(lambda i: [face_at(150, 240, 150), face_at(450, 240, 150), face_at(300, 100, 40)], run)(frame, {**P, "max_faces": 1, "min_face": 20})[1]
    assert (two[240, 150] > 0) != (two[240, 450] > 0) and two[100, 300] == 0           # max_faces 1: only the largest


def test_a_still_face_reuses_the_result_but_a_moving_one_is_run_again():
    calls = []
    def net(x): calls.append(1); return x
    textured = np.random.default_rng(5).integers(60, 200, (480, 640, 3), dtype=np.uint8)           # a flat frame would give identical crops wherever the face is
    still = restorer(lambda i: [face_at(320, 240, 200)], net)
    for _ in range(4): still(textured, P)
    assert len(calls) < 4                                                                # saved work (and cannot shimmer)
    calls.clear(); restore._NET_CACHE.clear()
    moving = restorer(lambda i: [face_at(320 + 12 * i, 240, 200)], net)
    for _ in range(4): moving(textured, P)
    assert len(calls) == 4


# ---------------------------------------------------------------------------------------------- the compositing in the render
def test_the_render_replaces_only_where_the_mask_says(tmp_path):
    src = tmp_path / "s.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=0x404040:s=320x240:d=1:r=25,format=yuv420p", "-t", "1", str(src)], check=True)
    layer, mask = tmp_path / "l.mkv", tmp_path / "m.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=0xc08040:s=320x240:d=1:r=25,format=rgb24", "-c:v", "libx264rgb", "-crf", "0", str(layer)], check=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1:r=25[b];color=c=white:s=100x100:d=1:r=25[w];[b][w]overlay=110:70,format=gray",
                    "-c:v", "ffv1", "-pix_fmt", "gray", str(mask)], check=True)
    p = prepare({"blueprint_version": "1.0", "operations": [{"id": "r", "type": "face_restoration", "start": 0, "parameters": {}}]}, str(src), PathPolicy([tmp_path]), tmp_path, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    p.plan.restorations["r"] = restore.RestoreRef(str(layer), str(mask), 0, 25)
    c = compile_plan(p.plan, str(src), "-", RenderOptions(t_start=0.4, frame_only=True), str(tmp_path))
    r = subprocess.run(c.args, capture_output=True); assert r.returncode == 0, r.stderr[-300:]
    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), 1).astype(float)
    assert abs(img[120, 160] - [64, 128, 192]).max() < 6                                # inside the mask: the layer's colour (BGR of 0xc08040)
    assert abs(img[10, 10] - 64).max() <= 2 and abs(img[230, 310] - 64).max() <= 2     # outside: the original


# ---------------------------------------------------------------------------------------------- planning, model handling
def test_validation_and_the_download_note(prep, monkeypatch):
    from evp.ml import models
    ok = lambda **kw: prep(bp(ops=[{"id": "r", "type": "face_restoration", "start": 0, "parameters": kw}])).report
    assert ok(strength=0.5, identity=1, max_faces=2, min_face=120).ok and not ok(strength=2).ok and not ok(max_faces=0).ok
    monkeypatch.setattr(models, "is_installed", lambda n: False if n == "gfpgan" else True)
    assert any(w.code == "MODEL_DOWNLOAD" and "348" in w.message for w in ok().warnings)


def test_a_live_frame_asks_for_the_model_instead_of_freezing_on_a_download(tmp_path, monkeypatch):
    from evp.engine import ensure_mattes
    from evp.ml import models
    from evp.samples import make_greenscreen
    v = str(make_greenscreen(tmp_path / "a.mp4", 1))
    monkeypatch.setattr(models, "is_installed", lambda n: False)
    p = prepare({"blueprint_version": "1.0", "operations": [{"id": "r", "type": "face_restoration", "start": 0, "parameters": {}}]}, v, PathPolicy([tmp_path]), tmp_path, strict=True)
    with pytest.raises(models.ModelError, match="not installed"):
        ensure_mattes(p.plan, v, str(tmp_path), frame=5)


def test_installing_a_model_reports_progress_and_errors(monkeypatch):
    from evp.ml import models
    monkeypatch.setattr(models, "is_installed", lambda n: False)
    def fake(name, allow_download=True):
        models._PROGRESS[name] = {"done": 10, "total": 100}; raise models.ModelError("no network")
    monkeypatch.setattr(models, "ensure_model", fake)
    models._INSTALLS.pop("gfpgan", None)
    models.install_async("gfpgan")
    import time
    for _ in range(50):
        st = models.status("gfpgan")
        if not st["downloading"]: break
        time.sleep(0.05)
    assert st["error"] == "no network" and not st["installed"] and st["size"] == 348632874


# ---------------------------------------------------------------------------------------------- the real network (only when its model is installed)
@pytest.mark.skipif(not is_installed("gfpgan"), reason="GFPGAN model not installed")
def test_the_real_network_runs_is_deterministic_and_adds_detail_to_a_soft_face():
    sess = restore.session()
    rs = restore.FaceRestorer(64, 64, P, sess=sess)
    rng = np.random.default_rng(0)
    x = cv2.GaussianBlur(rng.random((512, 512, 3)).astype(np.float32), (0, 0), 14)                  # a soft, structure-less 'face'
    a, b = rs.net(x), rs.net(x)
    assert a.shape == (512, 512, 3) and np.isfinite(a).all() and a.min() >= 0 and a.max() <= 1
    assert np.array_equal(a, b)                                                                     # fixed noise: the same picture every time (no video shimmer)
    lap = lambda im: cv2.Laplacian(im.mean(2), cv2.CV_32F).var()
    assert lap(a) > 5 * lap(x)                                                                      # it drew fine detail where there was none


def test_model_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    H = {"x-evp-token": "tok"}
    r = c.get("/api/models/gfpgan", headers=H).json()
    assert r["name"] == "gfpgan" and r["size"] == 348632874 and r["license"] == "Apache-2.0" and "installed" in r
    assert c.get("/api/models/nope", headers=H).status_code == 404 and c.post("/api/models/nope/install", headers=H).status_code == 404


def test_warm_up_frames_only_prime_the_tracker_and_skip_the_network():
    calls = []
    def net(x): calls.append(1); return x
    r = restorer(lambda i: [face_at(320, 240, 200)], net)
    frame = np.full((480, 640, 3), 100, np.uint8)
    r(frame, P, prime_only=True); r(frame, P, prime_only=True)
    assert not calls and r.tracker.i == 2                                              # the tracker was updated, the network was not run
    r(frame, P); assert len(calls) == 1


def test_the_network_runs_once_per_face_picture_whatever_the_sliders_say():
    calls = []
    def net(x): calls.append(1); return x
    box, lm = face_at(320, 240, 200)
    frame = np.random.default_rng(2).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    for strength, identity in ((1.0, 0.0), (0.7, 0.5), (0.3, 1.0)):                   # three slider positions at the same playhead
        restorer(lambda i: [(box, lm)], net)(frame, {**P, "strength": strength, "identity": identity})
    assert len(calls) == 1


def test_two_requests_for_the_same_face_analysis_at_once_do_not_crash(tmp_path, monkeypatch):
    """the live preview and a second preview (or a test) ask for the same face analysis together: the second must wait and reuse it.
    It used to fail with FileNotFoundError when both renamed the same temporary file - the browser then saw 'Failed to fetch'."""
    import threading, time
    from evp.ml import face
    from evp.samples import make_greenscreen
    v = str(make_greenscreen(tmp_path / "a.mp4", 1))
    p = prepare({"blueprint_version": "1.0", "operations": [{"id": "s", "type": "skin_retouch", "start": 0, "parameters": {}}]}, v, PathPolicy([tmp_path]), tmp_path, strict=True)
    op = p.plan.steps[0]
    real = face.decode
    def slow(*a, **k):
        for f in real(*a, **k):
            time.sleep(0.15); yield f                                   # keep both requests inside the analysis at the same time
    monkeypatch.setattr(face, "decode", slow)
    results, errors = [], []
    def go():
        try: results.append(face.generate_control(v, op, 320, 180, 30, 5, 1, str(tmp_path), warmup=2))
        except BaseException as e: errors.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors
    assert len(results) == 3 and len({r.path for r in results}) == 1
