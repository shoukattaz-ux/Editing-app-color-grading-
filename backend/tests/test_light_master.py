"""Master light: one slider for all the lights of a studio look (and the face relighting)."""
import numpy as np
import pytest
from test_lightpattern import comp, CX, CY, bgbox
from test_colourlights import left, right
from conftest import bp
from evp.blueprint import _coerce
from evp.registry import OPERATIONS

LIGHTS = dict(colour_light=1.0, colour_light_reach=0.8, ambient_match=0.8, light_wrap=0.5, glow=0.3,
              colour_zones=[{"x0": 0.46, "y0": 0.2, "x1": 0.76, "y1": 0.8, "color": "#00ff00", "side": "right", "reach": 0.9}])


def diff(a, b): return float(np.abs(a - b).mean())


def test_zero_turns_every_light_off(prep, media):
    off, _ = comp(prep, media, **dict(LIGHTS, light_master=0.0))
    nothing, _ = comp(prep, media, **dict(LIGHTS, colour_light=0.0, ambient_match=0.0, light_wrap=0.0, glow=0.0, colour_zones=[]))
    assert diff(off, nothing) < 0.5


def test_the_master_scales_all_the_lights_together(prep, media):
    base, _ = comp(prep, media, **dict(LIGHTS, light_master=0.0))
    amounts = []
    for lm in (0.3, 0.6, 1.0, 1.4):
        lit, _ = comp(prep, media, **dict(LIGHTS, light_master=lm))
        amounts.append(diff(lit, base))
    assert amounts == sorted(amounts) and amounts[0] > 0.2 and amounts[-1] > amounts[0] * 2          # steadily stronger, nothing jumps or breaks


def test_one_is_the_same_as_not_setting_it(prep, media):
    a, _ = comp(prep, media, **LIGHTS)
    b, _ = comp(prep, media, **dict(LIGHTS, light_master=1.0))
    assert diff(a, b) == 0


def test_the_background_is_never_lit_whatever_the_master(prep, media):
    base, _ = comp(prep, media, **dict(LIGHTS, light_master=0.0))
    lit, _ = comp(prep, media, **dict(LIGHTS, light_master=1.5))
    assert np.abs(bgbox(lit) - bgbox(base)).max() <= 3                                 # (ambient/wrap act on the subject only)


def test_the_range_is_checked():
    spec = OPERATIONS["studio_look"].params["light_master"]
    assert _coerce("light_master", spec, 1.2) == 1.2
    for bad in (-0.1, 1.6, "x"):
        with pytest.raises(ValueError): _coerce("light_master", spec, bad)


def test_relighting_follows_the_master(prep, media):
    ops = lambda lm: [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "spill_suppression": False}},
                      {"id": "f", "type": "face_relighting", "start": 0, "parameters": {"key_intensity": 0.8, "fill": 0.5, "light_master": lm}}]
    steps = lambda lm: [o for o in prep(bp(ops=ops(lm))).plan.steps if o.type == "face_relighting"][0].params
    assert steps(0.5)["key_intensity"] == pytest.approx(0.4) and steps(0.5)["fill"] == pytest.approx(0.25)
    assert steps(0.0)["key_intensity"] == 0 and steps(1.0)["key_intensity"] == pytest.approx(0.8)
    assert steps(1.5)["key_intensity"] == pytest.approx(1.0)                          # capped at its own maximum
