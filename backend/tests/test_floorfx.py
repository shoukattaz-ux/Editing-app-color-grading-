"""Floor reflection and contact shadow."""
import io
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.engine import render_frame
from evp.floorfx import measure_bottom, placed_y, reflection_gradient, band_mask

KEY = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "spill_suppression": False}}
FLOOR = 135 / 180                                  # the disc (centre y 90, radius 45) ends at row 135 of 180
CX = 196                                           # its centre column at t = 1 s


def comp(prep, media, **rp):
    ops = [KEY, {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#3a5a80", **rp}, "depends_on": ["k"]}]
    p = prep(bp(ops=ops))
    assert p.plan, [e.message for e in p.report.errors]
    img = np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], 1.0, None, str(media["dir"])))).convert("RGB"), dtype=float)
    return img, p


def test_measure_bottom_and_placement_mapping():
    a = np.zeros((100, 400), np.uint8); a[20:70, 10:250] = 255; a[95, 3] = 255        # a single speck does not count
    f, cut = measure_bottom(a)
    assert abs(f - 0.70) < 1e-6 and not cut
    a[:, 10:250] = 255
    assert measure_bottom(a)[1]                                                      # touches the bottom
    assert placed_y(0.7, {"subject_scale": 1.0, "subject_y": 0.0}) == pytest.approx(0.7)
    assert placed_y(0.7, {"subject_scale": 0.5, "subject_y": 0.0, "subject_anchor": "bottom"}) == pytest.approx(0.85)       # bottom-anchored half size
    assert placed_y(0.7, {"subject_scale": 0.5, "subject_y": 0.1, "subject_anchor": "center"}) == pytest.approx(0.25 + 0.35 + 0.1)


def test_gradient_and_band_masks():
    g = reflection_gradient(180, 10, 100, 40, 0.8)
    assert g[:100].max() == 0 and g[100, 0] > 190 and g[140:].max() == 0 and (np.diff(g[100:140, 0].astype(int)) <= 0).all()
    b = band_mask(180, 10, 100, 6)
    assert b[:94].max() == 0 and b[94:101].min() == 255 and b[102:].max() == 0


def test_reflection_appears_below_the_feet_fades_and_follows_the_subject(prep, media):
    base, _ = comp(prep, media)
    refl, p = comp(prep, media, reflection=0.8, reflection_length=0.3, reflection_blur=2.0)
    fl = p.plan.floor["r"]
    assert abs(fl["y"] - FLOOR) < 0.03 and not fl["cut"]                              # the floor was measured from the matte
    near = (slice(138, 146), slice(CX - 15, CX + 15)); far = (slice(165, 178), slice(CX - 15, CX + 15))
    d_near = np.abs(refl[near] - base[near]).mean(); d_far = np.abs(refl[far] - base[far]).mean()
    assert d_near > 15 and d_near > 3 * d_far                                         # strong at the feet, fading away
    tint = refl[near].mean(axis=(0, 1)) - base[near].mean(axis=(0, 1))
    assert tint[0] > tint[2] + 8                                                      # the reflection has the subject's (skin) colour: more red than blue
    assert np.abs(refl[:120, :140] - base[:120, :140]).max() <= 2                     # nothing above the floor / away from the subject changes


def test_contact_shadow_darkens_right_under_the_feet_only(prep, media):
    base, _ = comp(prep, media)
    cs, p = comp(prep, media, contact_shadow=0.8, contact_height=0.04, contact_blur=8.0)
    under = (slice(134, 142), slice(CX - 12, CX + 12))
    assert cs[under].mean() < base[under].mean() - 12                                 # darker under the feet
    assert np.abs(cs[:100] - base[:100]).max() <= 1                                   # no shadow up on the body / background
    assert np.abs(cs[160:, :] - base[160:, :]).max() <= 2                             # nor far below
    wide, _ = comp(prep, media, contact_shadow=0.8, contact_blur=30.0)
    assert (base[under].mean() - wide[under].mean()) < (base[under].mean() - cs[under].mean())        # a wider blur is softer under the feet


def test_a_hand_set_floor_overrides_the_measurement(prep, media):
    refl, p = comp(prep, media, reflection=0.8, floor_y=0.9, reflection_length=0.1)
    assert p.plan.floor == {}                                                         # nothing to measure
    base, _ = comp(prep, media)
    low = (slice(165, 172), slice(CX - 15, CX + 15)); feet = (slice(138, 146), slice(CX - 15, CX + 15))
    # the flip about row 162 shows the subject's lowest part around row 162 +- ...: the area just under the feet is unchanged now
    assert np.abs(refl[feet] - base[feet]).mean() < 6


def test_a_subject_cut_by_the_bottom_of_the_frame_has_no_floor(prep, media):
    refl, p = comp(prep, media, reflection=0.8, contact_shadow=0.8, subject_y=0.3)
    assert p.plan.floor["r"]["cut"] is True
    base, _ = comp(prep, media, subject_y=0.3)
    assert np.abs(refl - base).max() == 0                                             # both effects are skipped: no floor in view


def test_measurement_follows_subject_placement_and_is_cached(prep, media):
    refl, p = comp(prep, media, reflection=0.6, subject_scale=0.7, subject_anchor="bottom", subject_y=-0.1)
    expect = placed_y(FLOOR, {"subject_scale": 0.7, "subject_y": -0.1, "subject_anchor": "bottom"})
    assert abs(p.plan.floor["r"]["y"] - expect) < 0.03
    import glob
    assert glob.glob(str(media["dir"] / "floor" / "floor_*.json"))                     # measured once, kept
    _, p2 = comp(prep, media, reflection=0.6, subject_scale=0.7, subject_anchor="bottom", subject_y=-0.1)
    assert p2.plan.floor["r"] == p.plan.floor["r"]


def test_parameter_validation(prep):
    mk = lambda **kw: prep(bp(ops=[KEY, {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#000000", **kw}, "depends_on": ["k"]}])).report.ok
    assert mk(reflection=0.5, contact_shadow=0.5, floor_y=0.8) and not mk(reflection=1.5) and not mk(floor_y=1.2) and not mk(contact_height=0.5)
