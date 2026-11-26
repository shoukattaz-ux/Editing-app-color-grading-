"""Portrait mode: the person stays sharp, the REAL background is blurred - with no halo of the person's colours around them.
Synthetic clips contain no person the AI could find, so these tests give the effect a known person shape (the matte file) and
check the picture it makes; the AI part is the same matting the background removal uses (tested there)."""
import subprocess
import numpy as np
import pytest
from evp.engine import prepare
from evp.compiler import compile_plan, RenderOptions
from evp.security import PathPolicy
from evp.media import ffmpeg_bin
from evp.ml.matting import MatteRef

W, H = 640, 360
X0, X1, Y0, Y1 = 240, 400, 60, 360           # the "person": a bright block standing in the middle, down to the bottom edge


@pytest.fixture
def scene(tmp_path):
    src = tmp_path / "scene.mp4"
    # a detailed dark background (test pattern, darkened) + a bright flat "person"
    g = (f"nullsrc=s={W}x{H}:d=1:r=25,geq=lum='if(mod(floor(X/6)+floor(Y/6)\\,2)\\,70\\,25)':cb=128:cr=128[bg];color=c=0xf0e0d0:s={X1 - X0}x{Y1 - Y0}:d=1:r=25[p];"
         f"[bg][p]overlay={X0}:{Y0},format=yuv420p")
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", g, "-t", "1", str(src)], check=True)
    matte = tmp_path / "matte.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d=1:r=25[b];color=c=white:s={X1 - X0}x{Y1 - Y0}:d=1:r=25[w];[b][w]overlay={X0}:{Y0},format=gray",
                    "-c:v", "ffv1", "-pix_fmt", "gray", str(matte)], check=True)
    return tmp_path, src, matte


def render(scene, params=None):
    d, src, matte = scene
    ops = [{"id": "p", "type": "portrait_blur", "start": 0, "parameters": params}] if params is not None else []
    p = prepare({"blueprint_version": "1.0", "operations": ops}, str(src), PathPolicy([d]), d, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    if params is not None: p.plan.mattes["p"] = MatteRef(str(matte), None, 0, 25)
    c = compile_plan(p.plan, str(src), "-", RenderOptions(t_start=0.4, frame_only=True), str(d))
    r = subprocess.run(c.args, capture_output=True); assert r.returncode == 0, r.stderr[-400:]
    import cv2
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), 1).astype(float)


def detail(a): return float(np.abs(np.diff(a.mean(2), axis=1)).mean())


def test_person_stays_sharp_and_background_is_blurred(scene):
    base, out = render(scene), render(scene, {"blur": 0.7, "falloff": 0.0, "edge": 0.2})
    inside = (slice(Y0 + 20, Y1 - 10), slice(X0 + 20, X1 - 20))
    assert np.abs(out[inside] - base[inside]).max() <= 2                        # the person is untouched
    far = (slice(10, 120), slice(20, 180))
    assert detail(out[far]) < detail(base[far]) * 0.4                            # the background lost its fine detail (blurred)


def test_no_halo_of_the_person_around_them(scene):
    import cv2
    base, out = render(scene), render(scene, {"blur": 1.0, "falloff": 0.0, "edge": 0.2})
    ring = (slice(Y0 + 40, Y1 - 20), slice(X0 - 14, X0 - 4))                  # background just left of the bright person
    sig = 1.5 + 26.0                                                            # the effect's blur at blur = 1 (at 360p: x 1/3)
    naive = cv2.GaussianBlur(base, (0, 0), sig * H / 1080)                      # what a plain blur of the whole picture would give
    assert naive[ring].mean() > base[ring].mean() + 15                         # (a plain blur DOES glow there: the test can see a halo)
    assert out[ring].mean() < base[ring].mean() + 6                            # the portrait blur does not


def test_depth_falloff_keeps_the_background_near_the_person_sharper(scene):
    flat, deep = render(scene, {"blur": 0.8, "falloff": 0.0}), render(scene, {"blur": 0.8, "falloff": 1.0})
    near = (slice(Y0 + 40, Y1 - 20), slice(X1 + 12, X1 + 50))
    assert detail(deep[near]) > detail(flat[near]) * 1.3


def test_validation(prep):
    from conftest import bp
    ok = lambda **kw: prep(bp(ops=[{"id": "p", "type": "portrait_blur", "start": 0, "parameters": kw}])).report.ok
    assert ok(blur=0.5, falloff=0.2, edge=0.8) and not ok(blur=2) and not ok(edge=-1)
