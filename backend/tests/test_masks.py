"""Garbage / keep masks and the key QC views."""
import io
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.engine import render_frame

KEY = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma"}}
SUBJECT = (196, 90)       # the skin-toned disc at t = 1 s
GREEN = (5, 5)            # pure screen


def png(prep, media, ops, t=1.0, view=None, w=None):
    p = prep(bp(ops=ops))
    assert p.plan, [e.message for e in p.report.errors]
    return render_frame(p.plan, media["video"], t, w, str(media["dir"]), view=view), p


def rgb(prep, media, ops, t=1.0, view=None):
    b, _ = png(prep, media, ops, t, view)
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=int)


def mask(mode="garbage", **kw):
    params = {"mode": mode, "shape": "rect", "x": 0.0, "y": 0.0, "width": 0.15, "height": 1.0, "feather": 0, **kw}
    return {"id": "m" + str(abs(hash(str(sorted(params.items()))))), "type": "matte_mask", "start": 0, "parameters": params}


def test_garbage_mask_removes_a_part_of_the_subject(prep, media):
    base = rgb(prep, media, [KEY])
    assert base[SUBJECT[1], SUBJECT[0]].sum() > 150                                   # the subject is there
    m = rgb(prep, media, [KEY, mask(x=0.5, y=0.35, width=0.25, height=0.3)])
    assert m[SUBJECT[1], SUBJECT[0]].sum() < 20                                        # inside the garbage shape: removed (black background)
    assert np.abs(m[:, :140] - base[:, :140]).max() <= 2                               # nothing outside the shape changed


def test_keep_mask_keeps_a_part_of_the_screen(prep, media):
    base = rgb(prep, media, [KEY], view="alpha"); assert base[GREEN[1], GREEN[0]].max() < 20      # the screen is removed (matte black)
    m = rgb(prep, media, [KEY, mask("keep", x=0.0, y=0.0, width=0.1, height=0.2)], view="alpha")
    assert m[GREEN[1], GREEN[0]].min() > 235                                                      # kept: matte white
    assert m[170, 300].max() < 20                                                                  # elsewhere the screen is still removed


def test_ellipse_polygon_invert_and_feather(prep, media):
    e = rgb(prep, media, [KEY, mask(shape="ellipse", x=0.55, y=0.4, width=0.2, height=0.2)])
    assert e[90, 208].sum() < 20 and e[60, 196].sum() > 100                               # inside the ellipse removed, the rest of the subject kept
    tri = [[0.5, 0.3], [0.8, 0.3], [0.65, 0.7]]
    p = rgb(prep, media, [KEY, mask(shape="polygon", polygon=tri)])
    assert p[SUBJECT[1], SUBJECT[0]].sum() < 20
    inv = rgb(prep, media, [KEY, mask(x=0.0, y=0.0, width=0.5, height=1.0, invert=True)])          # garbage everything EXCEPT the left half
    assert inv[SUBJECT[1], SUBJECT[0]].sum() < 20
    hard = rgb(prep, media, [KEY, mask(x=0.5, y=0.0, width=0.5, height=1.0, feather=0)])
    soft = rgb(prep, media, [KEY, mask(x=0.5, y=0.0, width=0.5, height=1.0, feather=40)])
    row = lambda a: a[90, 150:175].sum(axis=1)
    assert np.abs(np.diff(row(soft))).max() < np.abs(np.diff(row(hard))).max() or row(hard).max() == row(soft).max()


def test_mask_follows_its_own_time_range(prep, media):
    m = mask(x=0.5, y=0.35, width=0.25, height=0.3); m["start"] = 1.5; m["end"] = 2.5
    assert rgb(prep, media, [KEY, m], t=1.0)[SUBJECT[1], SUBJECT[0]].sum() > 150      # before its range: no effect
    inside = rgb(prep, media, [KEY, m], t=2.0)
    base2 = rgb(prep, media, [KEY], t=2.0)
    assert np.abs(inside - base2).max() > 50 and np.abs(rgb(prep, media, [KEY, m], t=2.8) - rgb(prep, media, [KEY], t=2.8)).max() <= 2


def test_masks_bind_to_a_key_and_warn_when_there_is_none(prep):
    p = prep(bp(ops=[mask()]))
    assert p.report.ok and any(w.code == "MASK_NO_KEY" for w in p.report.warnings)
    q = prep(bp(ops=[KEY, mask(shape="polygon")]))
    assert any(w.code == "MASK_EMPTY" for w in q.report.warnings)
    bad = prep(bp(ops=[KEY, {**mask(), "parameters": {"mode": "garbage", "shape": "polygon", "polygon": [[0, 0], [2, 0], [1, 1]]}}]))
    assert not bad.report.ok
    assert not prep(bp(ops=[KEY, {**mask(), "parameters": {"mode": "garbage", "shape": "polygon", "polygon": [[0, 0], [1, 1]]}}])).report.ok


def test_mask_corrects_the_matte_before_the_background_is_placed(prep, media):
    ops = [KEY, mask(x=0.5, y=0.35, width=0.25, height=0.3),
           {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#0000ff"}, "depends_on": ["k"]}]
    out = rgb(prep, media, ops)
    assert out[SUBJECT[1], SUBJECT[0]][2] > 200 and out[SUBJECT[1], SUBJECT[0]][0] < 40        # blue background shows where the garbage mask cut the subject


def test_key_qc_views(prep, media):
    a = rgb(prep, media, [KEY], view="alpha")
    assert a[SUBJECT[1], SUBJECT[0]].min() > 235 and a[GREEN[1], GREEN[0]].max() < 20            # matte: subject white, screen black
    e = rgb(prep, media, [KEY], view="edge")
    assert e[SUBJECT[1], SUBJECT[0]].max() < 20 and e[GREEN[1], GREEN[0]].max() < 20 and (e.max(axis=2) > 200).any()      # only the edge band is lit
    red = rgb(prep, media, [KEY], view="red")
    assert red[GREEN[1], GREEN[0]][0] > 200 and red[GREEN[1], GREEN[0]][1] < 60                  # subject on red: the removed screen shows red
    for v in ("white", "grey", "checker"): rgb(prep, media, [KEY], view=v)
    # the view ignores grading and looks: it shows the key, not the finished picture
    graded = [KEY, {"id": "g", "type": "color_grading", "start": 0, "parameters": {"exposure": -3.0}}]
    assert np.abs(rgb(prep, media, graded, view="alpha") - a).max() <= 1
    # masks are visible in the matte view
    ma = rgb(prep, media, [KEY, mask(x=0.5, y=0.35, width=0.25, height=0.3)], view="alpha")
    assert ma[SUBJECT[1], SUBJECT[0]].max() < 20
    with pytest.raises(ValueError):
        png(prep, media, [], view="alpha")


def test_preview_api_view(tmp_path, media):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    H = {"x-evp-token": "tok"}
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Q", "source": media["video"]})
    r = c.post("/api/preview/frame", headers=H, json={"blueprint": bp(ops=[KEY]), "t": 1.0, "width": 320, "view": "alpha"})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    n = c.post("/api/preview/frame", headers=H, json={"blueprint": bp(), "t": 1.0, "width": 320, "view": "alpha"})
    assert n.status_code == 422 and "no keying" in str(n.json()).lower()
    assert c.post("/api/preview/frame", headers=H, json={"blueprint": bp(ops=[KEY]), "t": 1.0, "view": "nope"}).status_code == 422
