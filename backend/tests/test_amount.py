"""Master Amount: one slider fades a whole effect between the original footage (0) and the full effect (1), and pushes it further (> 1)."""
import io
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.engine import render_frame

ASSETS = [{"id": "bg", "type": "image", "path": "bg.png"}]
KEY = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma"}}


def px(prep, media, ops, t=1.0):
    p = prep(bp(ops=ops, assets=ASSETS))
    assert p.plan, [e.message for e in p.report.errors]
    return np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], t, None, str(media["dir"])))).convert("RGB"), dtype=float)


def grade(amount=None, **kw):
    params = {"saturation": 1.3, "contrast": 1.15, "exposure": 0.2, "temperature": 20, **kw}
    if amount is not None: params["amount"] = amount
    return [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": params}]


def test_color_grading_amount_zero_is_the_original_one_is_unchanged_in_between_is_between(prep, media):
    orig, full = px(prep, media, []), px(prep, media, grade())
    assert np.abs(px(prep, media, grade(1.0)) - full).max() == 0                   # 100 % is exactly the effect as set (same pipeline)
    assert np.abs(px(prep, media, grade(0.0)) - orig).max() == 0                   # 0 % is exactly the original
    half = px(prep, media, grade(0.5))
    expect = orig + 0.5 * (full - orig)
    assert np.abs(half - expect).mean() < 1.5 and np.percentile(np.abs(half - expect), 99.5) < 14   # linear mix (rounding; a few out-of-range colours clip differently)
    d = lambda a: np.abs(a - orig).mean()
    assert d(px(prep, media, grade(0.25))) < d(half) < d(full) < d(px(prep, media, grade(1.5)))     # monotone: fades out, intensifies up to 150 %


def test_amount_respects_the_time_range_and_keeps_sub_sliders_independent(prep, media):
    ops = [{"id": "g", "type": "color_grading", "start": 1, "end": 2, "parameters": {"saturation": 2.0, "amount": 0.5}}]
    outside, inside = px(prep, media, ops, 0.5), px(prep, media, ops, 1.5)
    assert np.abs(outside - px(prep, media, [], 0.5)).max() == 0
    assert np.abs(inside - px(prep, media, [], 1.5)).mean() > 1
    a, b = px(prep, media, grade(0.5)), px(prep, media, grade(0.5, saturation=1.0))
    assert np.abs(a - b).mean() > 1                                                   # changing a sub-slider still changes the result at any amount


def test_partial_lut_and_amount_work_together(prep, media):
    assets = ASSETS + [{"id": "lut", "type": "lut", "path": "warm.cube"}]
    ops = lambda a: [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"lut_asset_id": "lut", "lut_intensity": 0.6, "amount": a}}]
    p = lambda a: np.asarray(Image.open(io.BytesIO(render_frame(prep(bp(ops=ops(a), assets=assets)).plan, media["video"], 1.0, None, str(media["dir"])))).convert("RGB"), dtype=float)
    orig = px(prep, media, [])
    assert np.abs(p(0.0) - orig).max() == 0 and np.abs(p(1.0) - orig).mean() > 1 and np.abs(p(0.5) - orig).mean() < np.abs(p(1.0) - orig).mean()


def test_studio_look_amount_scales_the_grade_and_the_subject_integration(prep, media):
    look = lambda a: [{"id": "l", "type": "studio_look", "start": 0, "end": 3,
                       "parameters": {"preset": "none", "contrast": 0.9, "tint_amount": 0.8, "shadow_tint": "#0000ff", "highlight_tint": "#ffaa00", "saturation": 1.4, "amount": a}}]
    orig = px(prep, media, [])
    assert np.abs(px(prep, media, look(0.0)) - orig).max() == 0
    d = lambda a: np.abs(px(prep, media, look(a)) - orig).mean()
    assert d(0.3) < d(1.0) < d(1.5)
    # with a background the integration strength follows the amount: at 0 the composite equals the plain composite (no look at all)
    comp = lambda a: [KEY, {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg", "shadow": 0.0}, "depends_on": ["k"]},
                      {"id": "l", "type": "studio_look", "start": 0, "end": 3, "depends_on": ["r"],
                       "parameters": {"preset": "none", "ambient_match": 1.0, "light_wrap": 1.0, "depth": 1.0, "amount": a}}]
    plain = px(prep, media, comp(0.0))
    plain_no_look = px(prep, media, comp(0.0)[:2])
    assert np.abs(plain - plain_no_look).max() == 0
    assert np.abs(px(prep, media, comp(1.0)) - plain_no_look).mean() > 0.3
    assert np.abs(px(prep, media, comp(0.5)) - plain_no_look).mean() < np.abs(px(prep, media, comp(1.0)) - plain_no_look).mean()


def test_background_removal_amount_fades_the_removal_back_to_the_original(prep, media):
    ops = lambda a: [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "amount": a}},
                     {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg"}, "depends_on": ["k"]}]
    orig, full = px(prep, media, []), px(prep, media, ops(1.0))
    zero, half = px(prep, media, ops(0.0)), px(prep, media, ops(0.5))
    sx, sy = 5, 5                                                                       # a pure screen pixel: green in the original, the new background after removal
    assert orig[sy, sx, 1] > 200 and abs(zero[sy, sx] - orig[sy, sx]).max() < 3         # 0 % = the original background shows
    mixed = full[sy, sx] + 0.5 * (orig[sy, sx] - full[sy, sx])                           # 50 % = halfway between the two
    assert np.abs(half[sy, sx] - mixed).max() < 8 and np.abs(half[sy, sx] - full[sy, sx]).max() > 20
    assert np.abs(full - px(prep, media, [{**ops(1.0)[0], "parameters": {"mode": "chroma"}}, ops(1.0)[1]])).max() == 0      # 100 % = unchanged


def test_amount_is_validated_and_not_keyframable(prep):
    assert not prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {"amount": 1.6}}])).report.ok        # > 150 %
    assert not prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"amount": 1.2}}])).report.ok   # a key goes to 100 %
    bad = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {}, "keyframes": {"amount": [{"time": 0, "value": 1}]}}]))
    assert not bad.report.ok and any(e.code == "PARAM_NOT_ANIMATABLE" for e in bad.report.errors)
