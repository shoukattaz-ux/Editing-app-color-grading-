"""Skin retouch: smooth ONLY skin - eyes, brows, lips, beard, hair and everything outside the face stay exactly as they were."""
import subprocess
import numpy as np
import cv2
import pytest
from evp.engine import prepare
from evp.compiler import compile_plan, RenderOptions
from evp.security import PathPolicy
from evp.media import ffmpeg_bin
from evp.ml.face import Track, _weights_retouch, ControlRef

P = {"feather": 0.5}


def drawn_face():
    """a 320x320 picture: skin-coloured face, a dark beard on the lower face, eye / mouth landmarks"""
    img = np.full((320, 320, 3), (60, 90, 140), np.uint8)                      # blue background
    cv2.ellipse(img, (160, 160), (80, 110), 0, 0, 360, (205, 150, 120), -1)   # skin
    cv2.ellipse(img, (160, 235), (70, 35), 0, 0, 360, (45, 30, 25), -1)      # beard (dark)
    t = Track(box=np.array([80.0, 50.0, 160.0, 220.0]), lm=np.array([[125.0, 135.0], [195.0, 135.0], [160.0, 175.0], [135.0, 215.0], [185.0, 215.0]]), score=0.9)
    return img, t


def test_the_skin_map_has_cheeks_and_forehead_but_not_eyes_lips_beard_or_background():
    img, t = drawn_face()
    R, G, B = _weights_retouch(img, [(t, 1.0)], P, None)
    assert R[155, 110] > 0.5 and R[95, 160] > 0.5                              # cheek, forehead
    assert R[135, 125] < 0.25 and R[135, 195] < 0.25                          # eyes
    assert R[214, 160] < 0.25                                                  # lips
    assert R[250, 160] < 0.1                                                   # beard (dark)
    assert R[20, 20] < 0.01 and R[160, 300] < 0.01                            # background
    assert G.max() == 0 and B.max() == 0


@pytest.fixture
def scene(tmp_path):
    src = tmp_path / "skin.mp4"
    # noisy "skin" texture everywhere; the control map marks the middle square as skin
    g = "nullsrc=s=320x240:d=1:r=25,geq=lum='150+24*(random(1)-0.5)':cb=110:cr=150,format=yuv420p"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", g, "-t", "1", str(src)], check=True)
    ctrl = tmp_path / "ctrl.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1:r=25[b];color=c=red:s=120x120:d=1:r=25[s];[b][s]overlay=100:60,format=gbrp",
                    "-c:v", "ffv1", str(ctrl)], check=True)
    return tmp_path, src, ctrl


def render(scene, params=None):
    d, src, ctrl = scene
    ops = [{"id": "s", "type": "skin_retouch", "start": 0, "parameters": params}] if params is not None else []
    p = prepare({"blueprint_version": "1.0", "operations": ops}, str(src), PathPolicy([d]), d, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    if params is not None: p.plan.face_controls["s"] = ControlRef(str(ctrl), 0, 25, 25)
    c = compile_plan(p.plan, str(src), "-", RenderOptions(t_start=0.4, frame_only=True), str(d))
    r = subprocess.run(c.args, capture_output=True); assert r.returncode == 0, r.stderr[-400:]
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), 1).astype(float).mean(2)


inside, outside = (slice(80, 160), slice(120, 200)), (slice(10, 50), slice(10, 300))


def test_only_the_skin_is_smoothed(scene):
    base, out = render(scene), render(scene, {"amount": 1.0, "texture": 0.0, "size": 0.6})
    assert out[inside].std() < base[inside].std() * 0.6                        # the skin is smoother
    assert np.abs(out[outside] - base[outside]).max() <= 1                    # nothing outside the skin changed
    assert abs(out[inside].mean() - base[inside].mean()) < 4                  # same brightness / colour, only smoother


def test_texture_keeps_some_of_the_real_skin_and_off_is_untouched(scene):
    base = render(scene)
    smooth, natural = render(scene, {"amount": 1.0, "texture": 0.0}), render(scene, {"amount": 1.0, "texture": 0.6})
    assert natural[inside].std() > smooth[inside].std() * 1.3                 # more texture kept = less plastic
    assert np.abs(render(scene, {"amount": 0.0}) - base).max() == 0


def test_validation(prep):
    from conftest import bp
    ok = lambda **kw: prep(bp(ops=[{"id": "s", "type": "skin_retouch", "start": 0, "parameters": kw}])).report.ok
    assert ok(amount=0.8, texture=0.2, size=1.0) and not ok(amount=1.5) and not ok(texture=-0.2)
