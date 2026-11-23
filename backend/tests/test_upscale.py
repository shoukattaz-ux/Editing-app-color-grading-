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


# ---------- professional post-processing (no network needed: synthetic inputs with known answers)
def _proc(**kw):
    from evp.ml.upscale import DetailProcessor
    p = {"strength": 1.0, "temporal_stability": 0.0, "color_fidelity": True, "face_detail": 1.0, **kw}
    return DetailProcessor(p, 256, 192, 64, 48)


def _texture(seed, shift=0):
    rng = np.random.default_rng(seed)
    t = cv2.GaussianBlur(rng.random((48, 64 + 40)).astype(np.float32), (0, 0), 1.2)
    t = (t - t.min()) / (t.max() - t.min())
    return t[:, 20 - shift:84 - shift]


def test_color_fidelity_removes_tone_shift():
    pr = _proc()
    src = (np.dstack([_texture(1)] * 3) * 200 + 20).astype(np.uint8)
    lz = cv2.resize(src, (256, 192), interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255
    sr = lz * 0.8 + 0.05                                # AI output with a contrast/brightness shift
    out = pr(src, sr, lz)
    lo = lambda a: cv2.GaussianBlur(a, (0, 0), 8)
    assert np.abs(lo(out) - lo(lz)).mean() < 0.01 < np.abs(lo(sr) - lo(lz)).mean()


def test_temporal_stability_reduces_shimmer_on_static_scene():
    src = (np.dstack([_texture(2)] * 3) * 255).astype(np.uint8)
    lz = cv2.resize(src, (256, 192), interpolation=cv2.INTER_LANCZOS4).astype(np.float32) / 255
    rng = np.random.default_rng(3)
    srs = [lz + rng.normal(0, 0.03, lz.shape).astype(np.float32) for _ in range(8)]   # per-frame "re-guessed" detail
    def shimmer(pr):
        outs = [pr(src, s, lz) for s in srs]
        return np.mean([np.abs(a - b).mean() for a, b in zip(outs[3:], outs[4:])])
    assert shimmer(_proc(temporal_stability=1.0)) < 0.5 * shimmer(_proc(temporal_stability=0.0))


def test_temporal_follows_motion_without_ghosting():
    """Texture moves 2 px/frame (source). Output detail must match the *current* position, not trail behind."""
    pr = _proc(temporal_stability=1.0)
    hp = lambda a: a - cv2.GaussianBlur(a, (0, 0), 2)
    for i in range(6):
        src = (np.dstack([_texture(4, shift=2 * i)] * 3) * 255).astype(np.uint8)
        lz = cv2.resize(src, (256, 192), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255
        sr = lz + 0.3 * hp(lz)                           # 'AI' detail consistent with the current frame
        out = pr(src, sr, lz)
    cur, prev_pos = hp(sr)[16:-16, 40:-40, 0], None
    corr = lambda a, b: float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    src_prev = (np.dstack([_texture(4, shift=8)] * 3) * 255).astype(np.uint8)
    lz_prev = cv2.resize(src_prev, (256, 192), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255
    o = hp(out)[16:-16, 40:-40, 0]
    assert corr(o, cur) > 0.9 and corr(o, cur) > corr(o, hp(lz_prev)[16:-16, 40:-40, 0]) + 0.2


def test_face_detail_attenuated_only_inside_face():
    from evp.ml.face import Track
    pr = _proc(face_detail=0.0)

    class FakeTracker:
        def update(self, bgr):
            return [(Track(np.array([30., 20., 40., 50.]), np.array([[42, 38], [58, 38], [50, 48], [43, 60], [57, 60]], float), 0.9), 1.0)]
    pr.tracker, pr.fs = FakeTracker(), 0.5              # analysis at half of 256x192
    src = np.full((48, 64, 3), 128, np.uint8)
    lz = np.full((192, 256, 3), 0.5, np.float32)
    rng = np.random.default_rng(5)
    sr = lz + rng.normal(0, 0.05, lz.shape).astype(np.float32)
    d = np.abs(pr(src, sr, lz) - lz).mean(-1)
    face_c, outside = d[85:105, 90:110].mean(), d[5:30, 5:30].mean()
    assert face_c < 0.25 * outside and pr.faces_seen == 1
