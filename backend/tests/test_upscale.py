"""AI super-resolution. Skipped when onnxruntime/onnx or the weights are unavailable."""
import io, pickle, zipfile
import numpy as np, cv2, pytest
from conftest import bp
from evp.ml import models

ok, why = models.runtime_available()
try:
    import onnx  # noqa
except Exception:
    ok, why = False, "onnx not installed"
pytestmark = pytest.mark.skipif(not ok, reason=why)


@pytest.fixture(scope="module", autouse=True)
def weights():
    try:
        models.ensure_model("esrgan_general"); models.ensure_model("esrgan_wdn")
    except models.ModelError as e:
        pytest.skip(str(e))


def _np_forward(sd, x):
    """Independent NumPy implementation of SRVGGNetCompact (reference for the ONNX conversion)."""
    def conv(x, w, b):
        c, h, wd = x.shape
        p = np.pad(x, ((0, 0), (1, 1), (1, 1)))
        cols = np.stack([p[:, i:i + h, j:j + wd] for i in range(3) for j in range(3)], 1)   # C,9,H,W
        return np.einsum("ockl,cklhw->ohw", w.reshape(w.shape[0], c, 3, 3), cols.reshape(c, 3, 3, h, wd)) + b[:, None, None]
    idx = sorted({int(k.split(".")[1]) for k in sd})
    y = x
    for i in idx:
        w = sd[f"body.{i}.weight"]
        if w.ndim == 4: y = conv(y, w, sd[f"body.{i}.bias"])
        else: y = np.where(y >= 0, y, y * w[:, None, None])
    c, h, wd = y.shape
    r = 4
    ps = y.reshape(c // (r * r), r, r, h, wd).transpose(0, 3, 1, 4, 2).reshape(c // (r * r), h * r, wd * r)   # torch pixel_shuffle
    return ps + x.repeat(r, 1).repeat(r, 2)


def test_onnx_conversion_matches_numpy_reference():
    from evp.ml.srvgg import load_state_dict
    from evp.ml.upscale import session
    sd = load_state_dict(models.ensure_model("esrgan_general"))
    x = np.random.default_rng(0).random((3, 6, 7), np.float32)
    ref = _np_forward(sd, x)
    out = session(0.0).run(None, {"input": x[None]})[0][0]
    assert out.shape == (3, 24, 28) and np.abs(out - ref).max() < 1e-3


def test_restricted_unpickler_refuses_code(tmp_path):
    from evp.ml.srvgg import load_state_dict

    class Evil:
        def __reduce__(self): return (print, ("pwned",))
    p = tmp_path / "evil.pth"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("archive/data.pkl", pickle.dumps(Evil(), protocol=2))
    with pytest.raises(pickle.UnpicklingError):
        load_state_dict(p)


def test_denoise_interpolates_weights():
    from evp.ml.srvgg import load_state_dict, dni
    a = load_state_dict(models.ensure_model("esrgan_general")); b = load_state_dict(models.ensure_model("esrgan_wdn"))
    m = dni(a, b, 0.5)
    k = "body.0.weight"
    assert np.allclose(m[k], (a[k] + b[k]) / 2) and not np.allclose(a[k], b[k])


def test_tiling_matches_whole_frame():
    from evp.ml import upscale as up
    sess = up.session(0.5)
    img = cv2.resize(cv2.imread(cv2.samples.findFile("lena.jpg")) if False else np.random.default_rng(1).integers(0, 255, (48, 64, 3), np.uint8), (100, 90))
    whole = up.run_x4(sess, img)
    old = up.TILE; up.TILE = 32
    try: tiled = up.run_x4(sess, img)
    finally: up.TILE = old
    assert tiled.shape == whole.shape and np.abs(tiled - whole).mean() < 0.01


def test_upscale_render_sharper_than_lanczos_and_preview_matches(prep, media, tmp_path):
    from evp.engine import prepare, render_frame, Job
    from evp.compiler import RenderOptions
    from evp.security import PathPolicy
    from evp.media import ffmpeg_bin
    import subprocess
    low = tmp_path / "low.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-i", media["video"], "-vf", "scale=96:54", "-an", str(low)], check=True)
    mk = lambda ops: prepare(bp(ops=ops, output={"resolution": "384x216", "fps": "source", "preset": "ultrafast"}), str(low), PathPolicy([tmp_path])).plan
    plan = mk([{"id": "u", "type": "ai_upscale", "start": 0, "parameters": {"quality": "best"}}])
    ai = cv2.imdecode(np.frombuffer(render_frame(plan, str(low), 1.0, None, str(tmp_path)), np.uint8), 0)
    lz = cv2.imdecode(np.frombuffer(render_frame(mk([]), str(low), 1.0, None, str(tmp_path)), np.uint8), 0)
    assert ai.shape == (216, 384)
    assert cv2.Laplacian(ai, cv2.CV_64F).var() > 1.5 * cv2.Laplacian(lz, cv2.CV_64F).var()
    j = Job(plan, str(low), str(tmp_path / "o.mp4"), RenderOptions(crf=0), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    cap = cv2.VideoCapture(str(tmp_path / "o.mp4")); cap.set(cv2.CAP_PROP_POS_FRAMES, 30); _, ex = cap.read()
    assert np.abs(cv2.cvtColor(ex, cv2.COLOR_BGR2GRAY).astype(int) - ai.astype(int)).mean() < 3


def test_validation_warnings(prep):
    p = prep(bp(ops=[{"id": "u", "type": "ai_upscale", "start": 0, "end": 3}]))      # output == source size
    assert "UPSCALE_NOT_NEEDED" in [w.code for w in p.report.warnings]
    p = prep(bp(ops=[{"id": "u", "type": "ai_upscale", "start": 0, "end": 2}, {"id": "v", "type": "ai_upscale", "start": 1, "end": 3}]))
    assert "OVERLAP_CONFLICT" in [e.code for e in p.report.errors]


def test_gpu_failure_falls_back_to_cpu(prep, media, tmp_path, monkeypatch):
    from evp.ml import upscale as up
    real = up.session

    class FlakyGPU:
        def get_providers(self): return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        def run(self, *a, **k): raise RuntimeError("CUDA out of memory (simulated)")
    monkeypatch.setattr(up, "session", lambda d: FlakyGPU())
    p = prep(bp(ops=[{"id": "u", "type": "ai_upscale", "start": 0, "end": 0.2}], output={"resolution": "640x360", "fps": 30}))
    ref = up.generate_upscale(media["video"], p.plan.steps[0], 320, 180, 640, 360, 30.0, 0, 3, str(tmp_path))
    assert ref.frames == 3 and "simulated" in ref.gpu_fallback
