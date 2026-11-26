"""Cinematic look: a film grade in one control, colour by hue (skin stays warm), split tone, vignette / grain, cinema bars."""
import subprocess
import numpy as np
import cv2
from evp.engine import prepare, render_frame
from evp.security import PathPolicy
from evp.media import ffmpeg_bin


def clip(tmp_path, size="640x360"):
    w, h = (int(x) for x in size.split("x"))
    out = tmp_path / f"scene_{size}.mp4"
    if out.exists(): return out
    pw, ph = w // 5, h * 3 // 10
    # skin (left), green (right), a dark grey and a bright grey patch in the middle (away from the vignette) on mid grey
    g = (f"color=c=0x808080:s={size}:d=2:r=25[bg];color=c=0xd29a78:s={pw}x{ph}:d=2:r=25[skin];color=c=0x3c9a46:s={pw}x{ph}:d=2:r=25[grn];"
         f"color=c=0x303030:s={pw}x{ph}:d=2:r=25[dk];color=c=0xd8d8d8:s={pw}x{ph}:d=2:r=25[br];"
         f"[bg][skin]overlay={w // 10}:{h * 35 // 100}[a];[a][grn]overlay={w * 7 // 10}:{h * 35 // 100}[b];"
         f"[b][dk]overlay={w * 4 // 10}:{h // 10}[c];[c][br]overlay={w * 4 // 10}:{h * 6 // 10},format=yuv420p")
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", g, "-t", "2", str(out)], check=True)
    return out


def frame(tmp_path, v, params=None):
    ops = [{"id": "g", "type": "color_grading", "start": 0, "parameters": params}] if params is not None else []
    p = prepare({"blueprint_version": "1.0", "operations": ops}, str(v), PathPolicy([tmp_path]), tmp_path, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    return cv2.imdecode(np.frombuffer(render_frame(p.plan, str(v), 1.0, None, str(tmp_path)), np.uint8), 1)[:, :, ::-1].astype(float), p


def regions(x):
    h, w = x.shape[:2]
    pw, ph = w // 5, h * 3 // 10
    box = lambda x0, y0: x[y0 + 8:y0 + ph - 8, x0 + 8:x0 + pw - 8].mean((0, 1))
    return box(w // 10, h * 35 // 100), box(w * 7 // 10, h * 35 // 100), box(w * 4 // 10, h // 10), box(w * 4 // 10, h * 6 // 10)


def test_teal_and_orange_keeps_skin_warm_and_pushes_the_rest_toward_teal(tmp_path):
    v = clip(tmp_path)
    s0, g0, d0, b0 = regions(frame(tmp_path, v)[0])
    s1, g1, d1, b1 = regions(frame(tmp_path, v, {"cinematic": 1.0, "cinematic_style": "teal_orange"})[0])
    warmth = lambda c: c[0] - c[2]
    assert warmth(s1) > 0.8 * warmth(s0)                                   # skin keeps its warmth (not greyed out)
    import colorsys
    hue = lambda c: colorsys.rgb_to_hsv(*(c / 255))[0] * 360
    assert hue(g1) > hue(g0) + 12                                          # greens turn toward teal (hue toward cyan)
    assert d1[2] - d1[0] > d0[2] - d0[0] + 3                               # dark tones get teal
    assert b1[0] - b1[2] > b0[0] - b0[2] + 3                               # bright tones get warm


def test_off_is_untouched_and_styles_differ(tmp_path):
    v = clip(tmp_path)
    base = frame(tmp_path, v)[0]
    assert np.abs(frame(tmp_path, v, {"cinematic": 0.0})[0] - base).max() == 0
    moody, bleach, golden = (frame(tmp_path, v, {"cinematic": 1.0, "cinematic_style": s})[0] for s in ("moody", "bleach", "golden"))
    assert moody.mean() < base.mean() - 5                                    # moody is darker
    sat = lambda x: sum(c.max() - c.min() for c in regions(x)[:2])             # colourfulness of the skin and green patches
    assert sat(bleach) < sat(base) * 0.8                                      # bleach bypass is low in colour
    grey = lambda x: (x.max(2) - x.min(2))[x.shape[0] // 2 - 20:x.shape[0] // 2 + 20, x.shape[1] // 2 - 20:x.shape[1] // 2 + 20]
    assert grey(bleach).mean() < 4                                             # the film grain adds no colour speckles to grey
    assert regions(golden)[3][0] - regions(golden)[3][2] > regions(base)[3][0] - regions(base)[3][2] + 6     # golden: warm highlights


def test_cinema_bars_on_landscape_video_and_skipped_on_portrait(tmp_path):
    v = clip(tmp_path, "640x360")
    x, _ = frame(tmp_path, v, {"letterbox": "2.39"})
    bar = (360 - round(640 / 2.39) // 2 * 2) // 2
    assert x[: bar - 2].max() < 8 and x[-(bar - 2):].max() < 8                # black bars top and bottom
    assert x[bar + 4: -bar - 4].mean() > 40                                   # the picture in between
    vp = clip(tmp_path, "360x640")
    y, p = frame(tmp_path, vp, {"letterbox": "2.39"})
    assert y[:20].mean() > 40 and any(w.code == "LETTERBOX_SKIPPED" for w in p.report.warnings)   # a tall video keeps its picture


def test_validation(prep):
    from conftest import bp
    ok = lambda **kw: prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": kw}])).report.ok
    assert ok(cinematic=1.2, cinematic_style="moody", letterbox="1.85") and not ok(cinematic=3) and not ok(cinematic_style="marvel") and not ok(letterbox="4:3")
