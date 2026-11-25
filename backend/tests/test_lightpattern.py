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


# ------------------------------------------------------------------------------------------------ colour, style, spread, bend, where it falls
def tint(a): m = subj(a).mean(axis=(0, 1)); return m                              # mean R, G, B of the subject patch


def test_light_colour_tints_the_lit_parts_only_inside_the_subject(prep, media):
    white, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0)
    gold, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_color="#ffb000")
    cyan, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_color="#00e5ff")
    tw, tg, tc = tint(white), tint(gold), tint(cyan)
    assert tg[2] < tw[2] - 8 and tg[0] >= tw[0] - 3                                   # gold: less blue, red stays
    assert tc[0] < tw[0] - 8 and tc[2] >= tw[2] - 3                                   # cyan: less red
    base, _ = comp(prep, media)
    assert np.abs(bgbox(gold) - bgbox(base)).max() <= 1                               # the background does not take the colour
    assert np.array_equal(white, comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_color="#ffffff")[0])      # white = the plain pattern


def test_a_second_colour_tints_the_ground_between_the_lit_parts(prep, media):
    plain, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0, light_pattern_speed=0.0)
    two, _ = comp(prep, media, light_pattern=0.8, light_pattern_falloff=0.0, light_pattern_speed=0.0, light_pattern_color2="#0030ff")
    p, t = subj(plain), subj(two)
    dark = p.mean(2) < np.percentile(p.mean(2), 30)                                    # the darkest part of the pattern
    assert t[dark][:, 2].mean() > p[dark][:, 2].mean() + 8 and t[dark][:, 0].mean() < p[dark][:, 0].mean() - 5     # bluer there


def test_solid_patches_have_harder_edges_than_a_wide_spread(prep, media):
    hard, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_style="solid", light_pattern_spread=0.0, light_pattern_speed=0.0)
    soft, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_style="solid", light_pattern_spread=1.0, light_pattern_speed=0.0)
    g1 = lambda a: np.abs(np.diff(subj(a).mean(2), axis=1)).max()
    assert g1(hard) > 1.8 * g1(soft)
    lum = subj(hard).mean(2)
    assert (np.abs(lum - np.median(lum)) > 6).mean() > 0.2                              # patches: two clear levels, lit and unlit
    ripples, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_spread=0.0, light_pattern_speed=0.0)
    assert np.abs(ripples - hard).mean() > 1                                            # a different look from the web


def test_particles_are_sparse_glints_that_drift(prep, media):
    base, _ = comp(prep, media)
    a, _ = comp(prep, media, t=1.0, light_pattern=1.0, light_pattern_falloff=0.0, light_pattern_style="particles", light_pattern_spread=0.0)
    d = np.abs(subj(a, 50, 130) - subj(base, 50, 130)).max(2)
    assert 0.002 < (d > 25).mean() < 0.25 and d.max() > 60                            # few, bright glints; the rest of the subject is not darkened
    assert subj(a, 50, 130).mean() > subj(base, 50, 130).mean() - 3
    b, _ = comp(prep, media, t=1.4, light_pattern=1.0, light_pattern_falloff=0.0, light_pattern_style="particles", light_pattern_spread=0.0)
    patch = (slice(70, 110), slice(175, 215))
    assert np.abs(a[patch] - b[patch]).mean() > 0.4                                     # they move


def test_bend_changes_the_pattern_and_zero_bend_is_a_regular_pattern(prep, media):
    straight, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_warp=0.0, light_pattern_speed=0.0)
    bent, _ = comp(prep, media, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_warp=2.0, light_pattern_speed=0.0)
    assert np.abs(straight - bent).mean() > 1.5
    # without bend the light is a fixed grid of straight lines: the same picture at two times (speed does not matter, nothing animates)
    later, _ = comp(prep, media, t=1.0, light_pattern=0.9, light_pattern_falloff=0.0, light_pattern_warp=0.0, light_pattern_speed=2.0)
    assert sd(subj(later)) > 6


def test_the_light_can_never_fall_outside_the_subject_unless_you_ask(prep, media):
    base, _ = comp(prep, media)
    wild, _ = comp(prep, media, light_pattern=1.0, light_pattern_spread=1.0, light_pattern_falloff=0.0, light_pattern_color="#00ffff", light_pattern_color2="#ff00ff", light_pattern_on="subject")
    changed = np.abs(wild - base).max(2) > 2
    _, p = comp(prep, media, light_pattern=1.0)
    matte = np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], 1.0, None, str(media["dir"]), view="alpha"))).convert("L"), dtype=float) / 255.0
    assert changed.sum() > 500 and (matte > 0.5).sum() > 4000                           # the light is really there ...
    diff = np.abs(wild - base).max(2)
    assert (diff <= matte * 260 + 2.5).all()                                            # ... and a wide, coloured, strong one never changes a pixel by more than the matte covers it (nothing where the matte is 0)
    assert not (changed & (matte < 0.005)).any()
    on_bg, _ = comp(prep, media, light_pattern=1.0, light_pattern_falloff=0.0, light_pattern_on="background")
    assert sd(bgbox(on_bg)) > 4 and np.abs(subj(on_bg, 70, 110)[:, 10:50] - subj(base, 70, 110)[:, 10:50]).max() <= 2           # background only: the picture is lit, the subject is not
    both, _ = comp(prep, media, light_pattern=1.0, light_pattern_falloff=0.0, light_pattern_on="both")
    assert sd(bgbox(both)) > 4 and sd(subj(both)) > 6


def test_new_parameter_validation(prep):
    ok = lambda **kw: prep(bp(ops=[{"id": "l", "type": "studio_look", "start": 0, "parameters": {"light_pattern": 0.5, **kw}}])).report.ok
    assert ok(light_pattern_style="particles", light_pattern_color="#ffcc00", light_pattern_color2="#001133", light_pattern_spread=0.5, light_pattern_warp=1.5, light_pattern_on="both")
    assert not ok(light_pattern_style="lasers") and not ok(light_pattern_on="everywhere") and not ok(light_pattern_spread=2) and not ok(light_pattern_warp=3) and not ok(light_pattern_color="notacolour")
