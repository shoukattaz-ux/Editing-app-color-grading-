"""Colour lights: coloured lights from the sides of the subject, never on the background."""
import numpy as np
from test_lightpattern import comp, CX, CY, bgbox
from conftest import bp


def left(a): return a[CY - 10:CY + 10, CX - 40:CX - 20]
def right(a): return a[CY - 10:CY + 10, CX + 20:CX + 40]
def mid(a): return a[CY - 10:CY + 10, CX - 5:CX + 5]


def test_two_coloured_lights_tint_each_side_of_the_subject(prep, media):
    base, _ = comp(prep, media)
    lit, _ = comp(prep, media, colour_light=1.0, colour_light_reach=0.8)
    dl, dr = left(lit).mean((0, 1)) - left(base).mean((0, 1)), right(lit).mean((0, 1)) - right(base).mean((0, 1))
    assert dl[2] > 20 and dl[2] > dl[0] + 10                          # left: cyan-blue (blue up, red much less)
    assert dr[0] > 4 and dr[2] > 15 and dr[1] < dr[2] / 2   # right: magenta (red + blue, little green)


def test_the_colours_and_sides_are_yours_to_choose(prep, media):
    base, _ = comp(prep, media)
    lit, _ = comp(prep, media, colour_light=1.0, colour_light_color_a="#0000ff", colour_light_side_a="right", colour_light_side_b="off", colour_light_reach=0.8)
    assert (right(lit) - right(base)).mean((0, 1))[2] > 20
    assert np.abs(left(lit) - left(base)).max() < 6                    # light B is off, A is on the other side


def test_the_light_never_lands_on_the_background(prep, media):
    base, _ = comp(prep, media)
    lit, _ = comp(prep, media, colour_light=1.0, colour_light_reach=1.0, colour_light_edge=1.0)
    assert np.abs(bgbox(lit) - bgbox(base)).max() <= 1
    changed = np.abs(lit - base).max(2) > 3
    ys, xs = np.where(changed)
    assert ys.min() >= CY - 50 and ys.max() <= CY + 50 and xs.min() >= CX - 50 and xs.max() <= CX + 50


def test_the_edge_glow_hugs_the_outline(prep, media):
    off, _ = comp(prep, media, colour_light=0.6, colour_light_edge=0.0)
    on, _ = comp(prep, media, colour_light=0.6, colour_light_edge=1.0)
    rim = on[CY - 5:CY + 5, CX - 44:CX - 36].mean() - off[CY - 5:CY + 5, CX - 44:CX - 36].mean()
    core = on[CY - 5:CY + 5, CX - 5:CX + 5].mean() - off[CY - 5:CY + 5, CX - 5:CX + 5].mean()
    assert rim > 4 and abs(core) < 4                                    # brighter at the edge facing the light, the centre unchanged


def test_off_changes_nothing_and_amount_scales_it(prep, media):
    base, _ = comp(prep, media)
    zero, _ = comp(prep, media, colour_light=0.0)
    assert np.abs(zero - base).max() == 0
    full, _ = comp(prep, media, colour_light=1.0)
    half, _ = comp(prep, media, colour_light=1.0, amount=0.5)
    assert 0.2 < np.abs(half - base).mean() / np.abs(full - base).mean() < 0.8     # the Master amount scales it


def test_parameters_are_validated(prep):
    ok = lambda **kw: prep(bp(ops=[{"id": "l", "type": "studio_look", "start": 0, "parameters": {"colour_light": 0.5, **kw}}])).report.ok
    assert ok(colour_light_color_a="#00ffff", colour_light_side_a="top", colour_light_color_b="#ff00ff", colour_light_side_b="off", colour_light_reach=0.3, colour_light_edge=1)
    assert not ok(colour_light_side_a="middle") and not ok(colour_light=2) and not ok(colour_light_reach=3) and not ok(colour_light_color_a="nope")


# ------------------------------------------------------------------------------------------------ where the lights sit: follow / zone
XR = {0.2: (131, 219), 1.0: (151, 239), 1.4: (129, 217), 2.0: (85, 173), 2.8: (89, 177)}        # the disc's horizontal extent at each time (it drifts left and right)
ONE = dict(colour_light=1.0, colour_light_color_a="#0000ff", colour_light_side_a="left", colour_light_side_b="off", colour_light_reach=0.5, colour_light_edge=0.0)


def blue_gain(prep, media, t, x0, x1, **look):
    base, _ = comp(prep, media, t=t)
    lit, _ = comp(prep, media, t=t, **ONE, **look)
    sl = (slice(CY - 8, CY + 8), slice(x0, x1))
    return float((lit[sl] - base[sl])[..., 2].mean())


def test_follow_keeps_the_light_on_the_subject_as_it_moves(prep, media):
    gains = []
    for t in (0.2, 1.0, 2.0):
        lo, _ = XR[t]
        gains.append(blue_gain(prep, media, t, lo + 6, lo + 16, colour_light_place="follow"))
    assert min(gains) > 8 and max(gains) - min(gains) < 0.35 * max(gains)           # the same light at the subject's left edge wherever it is
    assert blue_gain(prep, media, 1.0, XR[1.0][1] - 16, XR[1.0][1] - 6, colour_light_place="follow") < 0.4 * min(gains)     # and it has not reached the far side


def test_fixed_stays_where_it_is_while_the_subject_moves(prep, media):
    far = [blue_gain(prep, media, t, XR[t][1] - 16, XR[t][1] - 6, colour_light_place="fixed") for t in (1.0, 2.0)]
    assert far[1] > far[0] + 5          # the subject drifted left, into the light that stays put: its right side is lit now


def test_your_own_zone_lights_only_inside_the_rectangle(prep, media):
    z = dict(colour_light_place="zone", colour_light_zone_x0=0.40, colour_light_zone_x1=0.70, colour_light_zone_y0=0.2, colour_light_zone_y1=0.8, colour_light_zone_soft=0.1)
    inside = blue_gain(prep, media, 1.0, 152, 172, **z)               # t=1.0: the disc spans 151..239; the zone spans 128..224
    outside = blue_gain(prep, media, 1.0, 228, 238, **z)
    assert inside > 10 and outside < 2
    moved_out = blue_gain(prep, media, 2.0, 88, 120, **z)             # t=2.0 the disc is at 85..173: its left part is outside the zone, so it is back to normal
    moved_in = blue_gain(prep, media, 2.0, 132, 160, **z)             # ... and the part that is inside the zone is lit again
    assert moved_out < 2 and moved_in > 4


def test_zone_never_lights_the_background(prep, media):
    base, _ = comp(prep, media, t=1.0)
    lit, _ = comp(prep, media, t=1.0, colour_light=1.0, colour_light_place="zone", colour_light_zone_x0=0.0, colour_light_zone_x1=1.0, colour_light_zone_y0=0.0, colour_light_zone_y1=1.0, colour_light_reach=1.0, colour_light_edge=1.0)
    assert np.abs(bgbox(lit) - bgbox(base)).max() <= 1


def test_place_parameters_are_validated(prep):
    ok = lambda **kw: prep(bp(ops=[{"id": "l", "type": "studio_look", "start": 0, "parameters": {"colour_light": 0.5, **kw}}])).report.ok
    assert ok(colour_light_place="zone", colour_light_zone_x0=0.1, colour_light_zone_x1=0.6, colour_light_zone_y0=0.2, colour_light_zone_y1=0.9, colour_light_zone_soft=0.5) and ok(colour_light_place="follow")
    assert not ok(colour_light_place="everywhere") and not ok(colour_light_zone_x0=2) and not ok(colour_light_zone_soft=-1)
