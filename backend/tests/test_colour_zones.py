"""Many coloured light boxes: each rectangle has its own colour and side; only the subject is lit."""
import numpy as np
from test_lightpattern import comp, CX, CY, bgbox
from test_colourlights import left, right
from evp.blueprint import _coerce
from evp.registry import OPERATIONS
import pytest

SPEC = OPERATIONS["studio_look"].params["colour_zones"]
RED = {"x0": 0.46, "y0": 0.2, "x1": 0.61, "y1": 0.8, "color": "#ff0000", "side": "left", "reach": 0.9}
BLUE = {"x0": 0.61, "y0": 0.2, "x1": 0.76, "y1": 0.8, "color": "#0000ff", "side": "right", "reach": 0.9}


def test_two_boxes_each_light_their_own_side_in_their_own_colour(prep, media):
    base, _ = comp(prep, media)
    lit, _ = comp(prep, media, colour_light=1.0, colour_light_side_a="off", colour_light_side_b="off", colour_zones=[RED, BLUE])       # boxes only
    dl, dr = left(lit).mean((0, 1)) - left(base).mean((0, 1)), right(lit).mean((0, 1)) - right(base).mean((0, 1))
    assert dl[0] > 15 and dl[0] > dl[2] + 10                                     # left: red
    assert dr[2] > 15 and dr[2] > dr[0] + 10                                     # right: blue
    assert np.abs(bgbox(lit) - bgbox(base)).max() <= 1                           # never on the background


def test_a_box_lights_only_inside_itself(prep, media):
    base, _ = comp(prep, media)
    only_right = dict(BLUE, x0=0.66)                                             # starts right of the subject's left half
    lit, _ = comp(prep, media, colour_light=1.0, colour_light_side_a="off", colour_light_side_b="off", colour_zones=[only_right])
    assert np.abs(left(lit) - left(base)).max() < 6                              # the left half is outside the box: untouched
    assert (right(lit) - right(base)).mean((0, 1))[2] > 10


def test_old_projects_without_boxes_render_the_same(prep, media):
    a, _ = comp(prep, media, colour_light=1.0)
    b, _ = comp(prep, media, colour_light=1.0, colour_zones=[])
    assert np.abs(a - b).max() == 0


def test_boxes_add_to_the_two_main_lights(prep, media):
    base, _ = comp(prep, media)
    two, _ = comp(prep, media, colour_light=1.0, colour_light_reach=0.8)
    plus, _ = comp(prep, media, colour_light=1.0, colour_light_reach=0.8, colour_zones=[dict(RED, color="#00ff00")])
    assert (plus - two).max() > 8 and np.abs(bgbox(plus) - bgbox(base)).max() <= 1


@pytest.mark.parametrize("bad", [[{"x0": 0.5, "x1": 0.5}], [{"side": "middle"}], [{"color": "red-ish"}], [{"intensity": 9}], ["box"], [dict(RED)] * 7, "x"])
def test_bad_boxes_are_refused(bad):
    with pytest.raises(ValueError):
        _coerce("colour_zones", SPEC, bad)


def test_good_boxes_are_normalised():
    out = _coerce("colour_zones", SPEC, [{"x0": 0.1, "x1": 0.4}])
    assert out[0]["side"] == "top" and out[0]["color"].startswith("#") and 0 <= out[0]["intensity"] <= 1.5
