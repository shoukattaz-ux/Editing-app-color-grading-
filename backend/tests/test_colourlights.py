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
