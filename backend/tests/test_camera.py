"""Camera match: the subject is made to look as if the same camera shot it and the background."""
import io
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.engine import render_frame
from evp.studio import analyze_camera, resolve

SUBJ = (296, 180)         # centre of the textured disc in the camera-test clip (radius 90, frame 640x360)
W, H = 640, 360


@pytest.fixture(scope="session")
def camshot(tmp_path_factory):
    """green screen + a sharp, noise-free, textured disc: the 'footage' side of a camera match"""
    import subprocess
    from evp.media import ffmpeg_bin
    d = tmp_path_factory.mktemp("cam")
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    tex = 0.8 + 0.15 * np.sin(xx / 6.0) * np.sin(yy / 6.0)
    disc = (np.hypot(xx - SUBJ[0], yy - SUBJ[1]) < 90)[..., None]
    skin = np.array([210, 160, 122], np.float32) / 255.0
    f = np.where(disc, skin * tex[..., None], np.array([0, 1, 0], np.float32))
    raw = u8(f).tobytes() * 25
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p",
                    str(d / "cam.mp4")], input=raw, check=True)
    return {"dir": d, "video": str(d / "cam.mp4")}


@pytest.fixture
def media(camshot): return camshot


@pytest.fixture
def prep(camshot):
    from evp.engine import prepare
    from evp.security import PathPolicy
    return lambda b, video=None: prepare(b, video or camshot["video"], PathPolicy([camshot["dir"]]), camshot["dir"], strict=True)


def u8(f): return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


def bg_png(media, name, sigma_blur=0.0, noise=0.0, base=(0.2, 0.35, 0.6), bar=False, bars=True, seed=0):
    import cv2
    rng = np.random.default_rng(seed)
    im = np.broadcast_to(np.array(base, np.float32), (H, W, 3)).copy()
    if bar: im[:, 330:350] = 1.0; im[:, 590:610] = 1.0
    if bars and not bar:                                  # real edges, so the softness of the picture can be measured
        for x0 in range(40, 620, 110): im[:, x0:x0 + 30] += 0.3
    if sigma_blur: im = cv2.GaussianBlur(im, (0, 0), sigma_blur)
    # a few soft features so there is gradient to measure
    yy, xx = np.mgrid[0:H, 0:W]
    im += 0.08 * np.sin(xx / 18.0)[..., None] * np.sin(yy / 14.0)[..., None]
    if sigma_blur: im = cv2.GaussianBlur(im, (0, 0), sigma_blur)
    if noise: im = im + rng.normal(0, noise, im.shape).astype(np.float32)
    p = media["dir"] / name
    Image.fromarray(u8(im)).save(p)
    return name


def comp(prep, media, bgname, look=None, t=1.0, amount=None):
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "spill_suppression": False}},
           {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "bg", "fit": "cover"}, "depends_on": ["k"]}]
    if look is not None:
        ops.append({"id": "l", "type": "studio_look", "start": 0, "depends_on": ["r"], "parameters": {"preset": "none", **look, **({"amount": amount} if amount is not None else {})}})
    p = prep(bp(ops=ops, assets=[{"id": "bg", "type": "image", "path": bgname}]))
    assert p.plan, [e.message for e in p.report.errors]
    img = np.asarray(Image.open(io.BytesIO(render_frame(p.plan, media["video"], t, None, str(media["dir"])))).convert("RGB"), dtype=float)
    return img, p


def subj_box(a): return a[165:195, 280:310]           # inside the disc
def bg_box(a): return a[285:345, 195:240]             # far from the subject, between two bars


def hf(a):                                            # high-frequency noise of a flat patch (luma, levels)
    import cv2
    g = a.mean(2).astype(np.float32)
    return float((g - cv2.GaussianBlur(g, (0, 0), 1.2)).std())


def test_subject_grain_only_touches_the_subject(prep, media):
    n = bg_png(media, "cm_clean.png")
    base, _ = comp(prep, media, n, {})
    g, _ = comp(prep, media, n, {"subject_grain": 1.0})
    assert hf(subj_box(g)) > hf(subj_box(base)) + 1.5 and hf(bg_box(g)) < hf(bg_box(base)) + 0.4


def test_background_grain_only_touches_the_background(prep, media):
    n = bg_png(media, "cm_clean.png")
    base, _ = comp(prep, media, n, {})
    g, _ = comp(prep, media, n, {"background_grain": 1.0})
    assert hf(bg_box(g)) > hf(bg_box(base)) + 1.5 and hf(subj_box(g)) < hf(subj_box(base)) + 0.4


def test_match_blacks_moves_the_subject_black_point_only(prep, media):
    n = bg_png(media, "cm_clean.png")
    base, _ = comp(prep, media, n, {})
    up, _ = comp(prep, media, n, {"match_blacks": 0.2})
    sb, su = subj_box(base).mean(), subj_box(up).mean()
    assert abs(su - (sb + 0.2 * (255 - sb))) < 4                                  # out = in + 0.2 x (white - in)
    assert np.abs(bg_box(up) - bg_box(base)).max() <= 2
    down, _ = comp(prep, media, n, {"match_blacks": -0.2})
    assert subj_box(down).mean() < sb - 10 and np.abs(bg_box(down) - bg_box(base)).max() <= 2


def test_match_softness_blurs_the_subject_and_its_edge_not_the_background(prep, media):
    n = bg_png(media, "cm_clean.png")
    base, _ = comp(prep, media, n, {})
    soft, _ = comp(prep, media, n, {"match_softness": 6.0})
    row = lambda a: a[180, 196:216].mean(1)                                      # across the disc edge (x ~ 206)
    assert np.abs(np.diff(row(soft))).max() < 0.8 * np.abs(np.diff(row(base))).max()
    assert np.abs(bg_box(soft) - bg_box(base)).max() <= 2
    sharp, _ = comp(prep, media, n, {"match_softness": -3.0})
    assert np.abs(np.diff(row(sharp))).max() >= 0.95 * np.abs(np.diff(row(base))).max()


def test_fringe_separates_red_and_blue_from_green_towards_the_edge_only(prep, media):
    n = bg_png(media, "cm_bar.png", bar=True, bars=False, base=(0.0, 0.0, 0.0))
    def centroid(a, ch, x0, x1):
        col = a[:, x0:x1, ch].mean(0)
        return float((np.arange(x0, x1) * col).sum() / col.sum())
    base, _ = comp(prep, media, n, {})
    fr, _ = comp(prep, media, n, {"fringe": 4.0})
    near = lambda a: centroid(a, 0, 560, 640) - centroid(a, 1, 560, 640)          # a bar near the right edge
    mid = lambda a: centroid(a, 0, 310, 370) - centroid(a, 1, 310, 370)           # a bar close to the centre
    dn, dm = near(fr) - near(base), mid(fr) - mid(base)
    assert dn > 0.6 and dn > 4 * abs(dm)                                          # red lands further out than green, and the shift grows with the distance from the centre
    shift = lambda ch: centroid(fr, ch, 560, 640) - centroid(base, ch, 560, 640)
    assert shift(0) > shift(1) > shift(2) - 0.05 and abs(shift(2)) < 0.5 * shift(0)   # red moves most, green less, blue is the reference


def test_camera_match_measures_and_fixes_a_sharp_clean_subject_on_a_soft_grainy_background(prep, media):
    n = bg_png(media, "cm_soft_noisy.png", sigma_blur=3.0, noise=0.03, base=(0.25, 0.3, 0.4))
    base, p0 = comp(prep, media, n, {"camera_match": 0.0})
    matched, p1 = comp(prep, media, n, {"camera_match": 1.0})
    cam = p1.plan.look_auto["l"]["camera"]
    assert cam and cam["match_softness"] > 0.3 and cam["subject_grain"] > 0.2 and cam["background_grain"] == 0.0
    assert cam["measured"]["background_edge_blur_px"] > cam["measured"]["subject_edge_blur_px"] and cam["measured"]["background_noise_levels"] > cam["measured"]["subject_noise_levels"]
    row = lambda a: a[180, 196:216].mean(1)
    assert np.abs(np.diff(row(matched))).max() < 0.93 * np.abs(np.diff(row(base))).max()            # softer subject edge
    assert hf(subj_box(matched)) > hf(subj_box(base)) + 1.0                                      # subject picked up grain
    # the match is closer: measure again on the matched picture
    from evp.studio import analyze_camera as ac
    mask = np.hypot(np.mgrid[0:H, 0:W][1] - SUBJ[0], np.mgrid[0:H, 0:W][0] - SUBJ[1]) < 90
    after = ac(matched.astype(np.uint8), mask)
    before = ac(base.astype(np.uint8), mask)
    assert abs(after["measured"]["subject_noise_levels"] - after["measured"]["background_noise_levels"]) < abs(before["measured"]["subject_noise_levels"] - before["measured"]["background_noise_levels"])


def test_explicit_controls_beat_the_automatic_values_and_amount_scales_them(prep, media):
    n = bg_png(media, "cm_soft_noisy.png", sigma_blur=3.0, noise=0.03, base=(0.25, 0.3, 0.4))
    _, p = comp(prep, media, n, {"camera_match": 1.0, "subject_grain": 0.0, "match_softness": 0.0})
    from evp.studio import resolve as rs
    L = rs(p.plan.steps[-1].params, p.plan.look_auto["l"])
    assert L["subject_grain"] == 0.0 and L["match_softness"] == 0.0                              # your values win
    half, _ = comp(prep, media, n, {"camera_match": 1.0, "match_softness": 0.0}, amount=0.5)
    full, _ = comp(prep, media, n, {"camera_match": 1.0, "match_softness": 0.0})
    base, _ = comp(prep, media, n, {"camera_match": 0.0, "match_softness": 0.0})
    g = lambda a: hf(subj_box(a))
    assert g(base) < g(half) < g(full)                                                           # master Amount scales the camera match too
    zero, _ = comp(prep, media, n, {"camera_match": 1.0}, amount=0.0)
    off, _ = comp(prep, media, n, None)
    assert np.abs(zero - off).max() == 0


def test_analyze_camera_unit():
    import cv2
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:H, 0:W]
    mask = np.hypot(xx - 296, yy - 180) < 90

    def scene(bg_blur=3.0, subj_blur=0.0, bg_noise=0.0, subj_noise=0.0, bg_black=0.1, subj_black=0.1):
        bg = np.zeros((H, W, 3), np.float32) + bg_black
        for x0 in range(40, 620, 110): bg[:, x0:x0 + 30, :] += 0.35
        bg[250:, :, :] += 0.15
        if bg_blur: bg = cv2.GaussianBlur(bg, (0, 0), bg_blur)
        sub = np.clip(np.broadcast_to(np.array([0.8, 0.6, 0.5], np.float32), (H, W, 3)) * (0.8 + 0.15 * np.sin(xx / 6) * np.sin(yy / 6))[..., None], subj_black, 1)
        sub = np.where((np.hypot(xx - 280, yy - 170) < 55)[..., None], subj_black, sub)          # dark clothing / hair: the subject has real blacks
        m = mask.astype(np.float32)
        if subj_blur: m = cv2.GaussianBlur(m, (0, 0), subj_blur)
        im = m[..., None] * sub + (1 - m[..., None]) * bg
        return u8(im + rng.normal(0, 1, im.shape).astype(np.float32) * np.where(mask, subj_noise, bg_noise)[..., None])
    r = analyze_camera(scene(), mask)
    assert r["match_softness"] > 1.0 and r["subject_grain"] == 0 and r["match_blacks"] == 0               # a hard-edged subject on a soft picture: soften it
    assert analyze_camera(scene(subj_blur=3.0), mask)["match_softness"] == 0                               # equally soft: leave it alone
    assert analyze_camera(scene(bg_blur=0.0, subj_blur=4.0), mask)["match_softness"] < -1.0               # softer than a sharp picture: sharpen
    r = analyze_camera(scene(bg_noise=0.03), mask)
    assert r["subject_grain"] > 0.5 and r["background_grain"] == 0
    r = analyze_camera(scene(subj_noise=0.03), mask)
    assert r["background_grain"] > 0.5 and r["subject_grain"] == 0
    assert analyze_camera(scene(bg_black=0.25, subj_black=0.02), mask)["match_blacks"] > 0.05              # milky background, deep subject blacks: lift them
    assert analyze_camera(scene(bg_black=0.02, subj_black=0.12), mask)["match_blacks"] < -0.05             # deep background, milky subject darks: deepen them
    assert analyze_camera(scene(bg_black=0.02, subj_black=0.4), mask)["match_blacks"] == 0                 # a bright subject has no blacks to move
    assert analyze_camera(scene(), np.zeros((H, W), bool)) is None                                         # no subject: nothing to compare


def test_camera_match_without_a_background_is_reported(prep):
    ops = [{"id": "l", "type": "studio_look", "start": 0, "parameters": {"camera_match": 1.0}}]
    p = prep(bp(ops=ops))
    assert p.report.ok and any(w.code == "CAMERA_MATCH_NO_BACKGROUND" for w in p.report.warnings)
    assert not any(w.code == "CAMERA_MATCH_NO_BACKGROUND" for w in prep(bp(ops=[{"id": "l", "type": "studio_look", "start": 0, "parameters": {"fringe": 2.0}}])).report.warnings)
