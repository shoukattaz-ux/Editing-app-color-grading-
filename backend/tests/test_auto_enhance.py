"""Auto enhance: one master slider; the clip is measured and the matching standard grading corrections are applied."""
import subprocess
import numpy as np
import cv2
import pytest
from evp.engine import prepare, render_frame
from evp.security import PathPolicy
from evp.media import ffmpeg_bin
from evp.enhance import corrections, apply


def clip(tmp_path, name, vf):
    clean = tmp_path / "clean.mp4"
    if not clean.exists():
        subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3", "-pix_fmt", "yuv420p", str(clean)], check=True)
    out = tmp_path / name
    subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(clean), "-vf", vf + ",format=yuv420p", str(out)], check=True)
    return clean, out


def frame(tmp_path, v, params=None):
    ops = [{"id": "g", "type": "color_grading", "start": 0, "parameters": params}] if params is not None else []
    p = prepare({"blueprint_version": "1.0", "operations": ops}, str(v), PathPolicy([tmp_path]), tmp_path, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    return cv2.imdecode(np.frombuffer(render_frame(p.plan, str(v), 1.0, None, str(tmp_path)), np.uint8), 1)[:, :, ::-1].astype(float), p.plan


def test_a_dim_warm_clip_comes_back_brighter_cleaner_and_closer_to_the_original(tmp_path):
    clean, dull = clip(tmp_path, "dull.mp4", "colorchannelmixer=rr=0.8:gg=0.74:bb=0.66")       # darker and warm, like an overcast phone clip
    ref, _ = frame(tmp_path, clean)
    before, _ = frame(tmp_path, dull)
    after, plan = frame(tmp_path, dull, {"auto_enhance": 1.0, "enhance_style": "crisp"})
    assert "g" in plan.enhance                                                         # it measured the clip
    rb = lambda x: x[..., 0].mean() / x[..., 2].mean()
    assert after.mean() > before.mean() + 8                                           # brighter
    assert abs(rb(after) - rb(ref)) < abs(rb(before) - rb(ref)) * 0.6                 # the warm cast is mostly gone
    natural, _ = frame(tmp_path, dull, {"auto_enhance": 1.0, "enhance_style": "natural"})
    assert np.abs(natural - ref).mean() < np.abs(before - ref).mean() * 0.75          # 'natural' brings it closer to the clean original


def test_a_milky_clip_gets_deeper_blacks(tmp_path):
    clean, hazy = clip(tmp_path, "hazy.mp4", "lutrgb=r='val*0.7+60':g='val*0.7+60':b='val*0.7+60'")       # a veil: no real blacks
    before, _ = frame(tmp_path, hazy)
    after, _ = frame(tmp_path, hazy, {"auto_enhance": 1.0, "enhance_style": "crisp"})
    lo = lambda x: np.percentile(x.mean(2), 2)
    assert lo(after) < lo(before) - 15                                                # the veil is lifted off the darks
    spread = lambda x: np.percentile(x.mean(2), 95) - np.percentile(x.mean(2), 5)
    assert spread(after) > spread(before) * 1.15                                      # more contrast


def test_the_master_slider_scales_it_and_zero_is_untouched(tmp_path):
    _, dull = clip(tmp_path, "dull.mp4", "colorchannelmixer=rr=0.8:gg=0.74:bb=0.66")
    base, _ = frame(tmp_path, dull)
    zero, _ = frame(tmp_path, dull, {"auto_enhance": 0.0})
    half, _ = frame(tmp_path, dull, {"auto_enhance": 0.5})
    full, _ = frame(tmp_path, dull, {"auto_enhance": 1.0})
    more, _ = frame(tmp_path, dull, {"auto_enhance": 1.5})
    assert np.abs(zero - base).max() == 0
    rb = [x[..., 0].mean() / x[..., 2].mean() for x in (base, half, full, more)]
    assert rb[0] > rb[1] > rb[2] > rb[3]                                              # the warm cast is removed more and more
    mid = [np.percentile(x.mean(2), 50) for x in (base, half, full)]
    assert mid[0] < mid[1] < mid[2]                                                   # and the dim picture brightened more and more


def test_your_own_sliders_add_on_top_and_styles_differ():
    m = {"p1": 0.01, "p5": 0.05, "p50": 0.5, "p95": 0.9, "p99": 0.95, "clip": 0.0, "haze": 0.1, "r": 0.73, "g": 0.72, "b": 0.7, "sat": 0.2, "neutral_share": 0.4, "white": 0.8}
    full = corrections(m, "crisp")
    mine = apply({"auto_enhance": 1.0, "enhance_style": "crisp", "exposure": 0.3, "contrast": 1.1, "temperature": 0.0}, m)
    assert abs(mine["exposure"] - (0.3 + full["exposure"])) < 1e-9 and abs(mine["contrast"] - 1.1 * full["contrast"]) < 1e-9
    assert corrections(m, "crisp")["temperature"] < corrections(m, "natural")["temperature"]       # crisp is the cool, clear-day look
    assert apply({"auto_enhance": 0.0, "exposure": 0.3}, m) == {"auto_enhance": 0.0, "exposure": 0.3}


def test_validation(prep):
    from conftest import bp
    ok = lambda **kw: prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": kw}])).report.ok
    assert ok(auto_enhance=0.8, enhance_style="vivid") and not ok(auto_enhance=2) and not ok(enhance_style="magic")
