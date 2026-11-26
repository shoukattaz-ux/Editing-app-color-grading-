"""Color Grading (and its Auto enhance) applied to the subject only, the background only, or both."""
import io
import numpy as np
from PIL import Image
from conftest import bp
from evp.engine import render_frame
from test_lightpattern import KEY, CX, CY, subj, bgbox


def comp(prep, media, grade=None, t=1.0, with_bg=True):
    ops = [KEY]
    if with_bg: ops.append({"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#3a5a80"}, "depends_on": ["k"]})
    if grade is not None: ops.append({"id": "g", "type": "color_grading", "start": 0, "parameters": grade})
    p = prep(bp(ops=ops))
    assert p.plan, [e.message for e in p.report.errors]
    return np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], t, None, str(media["dir"])))).convert("RGB"), dtype=float), p


def core(a): return subj(a, 70, 110)[:, 10:50]          # the opaque middle of the subject


def test_subject_only_never_touches_the_background(prep, media):
    base, _ = comp(prep, media)
    out, _ = comp(prep, media, {"exposure": 1.0, "temperature": -40, "apply_to": "subject"})
    assert core(out).mean() > core(base).mean() + 20                                  # the subject changed
    assert np.abs(bgbox(out) - bgbox(base)).max() <= 1                               # the background did not
    assert np.abs(out - base).max(2)[0:20, :].max() <= 1                              # (anywhere away from the subject)


def test_background_only_never_touches_the_subject(prep, media):
    base, _ = comp(prep, media)
    out, _ = comp(prep, media, {"exposure": 1.0, "apply_to": "background"})
    assert bgbox(out).mean() > bgbox(base).mean() + 20
    assert np.abs(core(out) - core(base)).max() <= 1


def test_both_is_the_whole_picture(prep, media):
    base, _ = comp(prep, media)
    out, _ = comp(prep, media, {"exposure": 1.0})
    assert core(out).mean() > core(base).mean() + 20 and bgbox(out).mean() > bgbox(base).mean() + 20


def test_auto_enhance_measures_only_the_part_it_changes(prep, media):
    _, ps = comp(prep, media, {"auto_enhance": 1.0, "apply_to": "subject"})
    _, pb = comp(prep, media, {"auto_enhance": 1.0, "apply_to": "background"})
    ms, mb = ps.plan.enhance["g"], pb.plan.enhance["g"]
    assert ms["p50"] > mb["p50"] + 0.15                                               # the bright subject (skin, white box) vs the dark background
    assert mb["b"] > mb["r"] + 0.1                                                    # the background measured is the blue-grey one, not the green screen
    base, _ = comp(prep, media)
    out, _ = comp(prep, media, {"auto_enhance": 1.0, "apply_to": "subject"})
    assert np.abs(bgbox(out) - bgbox(base)).max() <= 1 and np.abs(core(out) - core(base)).mean() > 3


def test_without_a_removed_background_it_says_so_and_grades_everything(prep, media):
    base, _ = comp(prep, media, with_bg=False)
    out, p = comp(prep, media, {"exposure": 1.0, "apply_to": "subject"}, with_bg=False)
    assert any(w.code == "GRADE_NEEDS_BACKGROUND" for w in p.report.warnings)
    assert core(out).mean() > core(base).mean() + 20


def test_validation(prep):
    ok = lambda **kw: prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": kw}])).report.ok
    assert ok(apply_to="background") and not ok(apply_to="sky")
