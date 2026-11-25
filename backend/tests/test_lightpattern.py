"""Light pattern (caustics) projected onto the subject only."""
import io
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.engine import render_frame

KEY = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "spill_suppression": False}}
CX, CY = 196, 90                                                      # the flat skin-toned disc (radius 45) at t = 1 s


def comp(prep, media, t=1.0, assets=None, amount=None, with_look=True, **look):
    ops = [KEY, {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#3a5a80"}, "depends_on": ["k"]}]
    if with_look: ops.append({"id": "l", "type": "studio_look", "start": 0, "depends_on": ["r"], "parameters": {"preset": "none", **look, **({"amount": amount} if amount is not None else {})}})
    p = prep(bp(ops=ops, assets=assets or []))
    assert p.plan, [e.message for e in p.report.errors]
    return np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], t, None, str(media["dir"])))).convert("RGB"), dtype=float), p


def subj(a, y0=60, y1=120): return a[y0:y1, CX - 30:CX + 30]
def sd(a): return float(a.std(axis=(0, 1)).mean())                          # spatial variation (per channel, averaged)
def bgbox(a): return a[140:175, 20:120]


def test_the_pattern_lights_the_subject_only_and_keeps_its_average_brightness(prep, media):
    base, _ = comp(prep, media)
    lit, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0)
    assert sd(subj(base)) < 2.0 and sd(subj(lit)) > 8.0                          # a flat subject becomes rippled
    assert abs(subj(lit).mean() - subj(base).mean()) < 10                            # the light redistributes, it does not just brighten
    assert np.abs(bgbox(lit) - bgbox(base)).max() <= 1                               # the background is untouched
    mask_edge = np.abs(lit - base).max(2) > 3
    ys, xs = np.where(mask_edge)
    assert ys.min() >= CY - 50 and ys.max() <= CY + 50 and xs.min() >= CX - 50 and xs.max() <= CX + 50     # only inside the subject (+ the edge pixel)


def test_it_moves_and_speed_zero_holds_still(prep, media):
    a, _ = comp(prep, media, t=1.0, light_pattern=0.8, light_pattern_falloff=0.0)
    b, _ = comp(prep, media, t=1.4, light_pattern=0.8, light_pattern_falloff=0.0)
    still1, _ = comp(prep, media, t=1.0, light_pattern=0.8, light_pattern_speed=0.0)
    still2, _ = comp(prep, media, t=1.4, light_pattern=0.8, light_pattern_speed=0.0)
    # the subject moves between t=1 and t=1.4, so compare the pattern on a fixed patch fully inside the disc at both times
    patch = (slice(85, 95), slice(180, 195))
    assert np.abs(a[patch] - b[patch]).mean() > 3                                    # the ripples moved
    assert np.abs(still1[patch] - still2[patch]).mean() < 1.0                        # a still pattern does not


def test_falloff_makes_the_light_weaker_towards_the_bottom(prep, media):
    top = lambda a: sd(subj(a, 50, 90)); low = lambda a: sd(subj(a, 100, 130))
    even, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0)
    fall, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=1.0)
    assert low(fall) < 0.75 * low(even) and top(fall) > 0.55 * top(even) and low(fall) < top(fall)     # the bottom is much weaker, the top keeps most of it


def test_scale_changes_the_size_of_the_ripples(prep, media):
    def cells(a):                                                                      # how many bright/dark swaps across the disc's middle row
        row = a[CY, CX - 40:CX + 40].mean(1); m = row.mean()
        return int((np.diff((row > m).astype(int)) != 0).sum())
    small, _ = comp(prep, media, light_pattern=0.9, light_pattern_scale=0.5, light_pattern_falloff=0.0)
    big, _ = comp(prep, media, light_pattern=0.9, light_pattern_scale=2.5, light_pattern_falloff=0.0)
    assert cells(small) > cells(big)


def test_your_own_pattern_picture_is_used(prep, media):
    stripes = np.zeros((180, 320), np.uint8); stripes[:, ::2] = 255; stripes[:, 1::4] = 0
    Image.fromarray(np.stack([stripes] * 3, -1)).save(media["dir"] / "lp_stripes.png")
    lit, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_asset_id="lp", assets=[{"id": "lp", "type": "image", "path": "lp_stripes.png"}])
    col = subj(lit)[10:50, :, 0].mean(0)
    assert col.std() > 5                                                              # the vertical stripes of the picture show on the subject
    gen, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0)
    assert np.abs(lit - gen).mean() > 1


def test_master_amount_scales_it_and_zero_is_off(prep, media):
    base, _ = comp(prep, media)
    full, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0)
    half, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0, amount=0.5)
    zero, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0, amount=0.0)
    none, _ = comp(prep, media, with_look=False)
    assert np.abs(zero - none).max() == 0                                            # 0 % = as if there were no look at all
    assert 0.35 < sd(subj(half)) / sd(subj(full)) < 0.75


def test_validation_and_warning_without_a_background(prep):
    ok = lambda **kw: prep(bp(ops=[{"id": "l", "type": "studio_look", "start": 0, "parameters": kw}])).report
    assert any(w.code == "CAMERA_MATCH_NO_BACKGROUND" for w in ok(light_pattern=0.5).warnings)
    assert ok(light_pattern=0.5, light_pattern_scale=2, light_pattern_speed=0.5, light_pattern_falloff=0.3).ok
    assert not ok(light_pattern=1.5).ok and not ok(light_pattern_scale=0.1).ok and not ok(light_pattern_falloff=2).ok
