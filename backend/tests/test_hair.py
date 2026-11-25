"""Hair detail: the AI refines only a band around the edge; the screen key stays in charge everywhere else."""
import io
import numpy as np
import pytest
import cv2
from PIL import Image
from conftest import bp
from evp.engine import render_frame
from evp.ml import matting, models
from evp.ml.hair import blend_hair, band_mask, agrees

H, W = 360, 640
YY, XX = np.mgrid[0:H, 0:W].astype(np.float32)
R = np.hypot(XX - 320, YY - 180)


def sector(): return np.sin(np.arctan2(YY - 180, XX - 320) * 24) > 0


def key_alpha():
    """a good key: a hard-edged person, strands it only half saw (alpha 0.15) in one zone, crisp strands (0.9) in another, pure screen elsewhere"""
    a = np.clip((52 - R) / 2, 0, 1)
    lost = (R > 52) & (R < 62) & sector() & (XX < 320)                    # left half: strands the key lost (alpha 0.15)
    crisp = (R > 52) & (R < 62) & sector() & (XX >= 320)                  # right half: strands the key got right (alpha 0.9)
    return np.where(lost, 0.15, np.where(crisp, 0.9, a)).astype(np.float32)


def ai_alpha(shift=0):
    a = np.clip((52 - np.hypot(XX - 320 - shift, YY - 180)) / 2, 0, 1)
    strands = (R > 52) & (R < 62) & sector() & (shift == 0)
    halo = (R >= 62) & (R < 70)                                           # the AI's soft halo over pure screen
    crisp_soft = (R > 52) & (R < 62) & sector() & (XX >= 320)             # where the key was already crisp the AI is vaguer
    far = (XX > 560) & (YY < 80)                                          # a false detection far away
    out = np.where(strands, 0.9, a)
    out = np.where(crisp_soft, 0.5, out)
    out = np.where(halo & (shift == 0), 0.6, out)
    return np.where(far, 0.8, out).astype(np.float32)


LOST = (R > 53) & (R < 61) & sector() & (XX < 320)
CRISP = (R > 53) & (R < 61) & sector() & (XX >= 320)
HALO = (R >= 63) & (R < 69)


def test_the_ai_only_restores_what_the_key_lost():
    ak, aa = key_alpha(), ai_alpha()
    a, fg, used = blend_hair(ak, None, aa, None, 1.0, 40)
    assert used
    assert a[LOST].mean() > 0.8                                                     # strands the key lost come back (alpha 0.15 -> ~0.9)
    assert np.array_equal(a[CRISP], ak[CRISP])                                      # strands the key got right are NOT softened by the vaguer AI
    assert a[HALO].max() == 0                                                       # the AI's halo over pure screen never comes in
    assert a[(XX > 560) & (YY < 80)].max() == 0 and np.abs(a - ak)[R > 90].max() == 0   # nothing far from the edge moves
    assert (a >= ak).all()                                                          # it can only add
    half, _, _ = blend_hair(ak, None, aa, None, 0.5, 40)
    assert 0.35 < half[LOST].mean() < 0.55 and (half >= ak).all()                   # strength scales the restoration
    off, _, u0 = blend_hair(ak, None, aa, None, 0.0, 40)
    assert not u0 and np.array_equal(off, ak)


def test_the_band_width_controls_how_far_the_ai_reaches():
    ak, aa = key_alpha(), ai_alpha()
    far_lost = LOST & (R > 58)
    narrow, _, _ = blend_hair(ak, None, aa, None, 1.0, 4)
    wide, _, _ = blend_hair(ak, None, aa, None, 1.0, 40)
    assert wide[far_lost].mean() > narrow[far_lost].mean() + 0.2
    assert band_mask(ak, 40, H).max() <= 1.0 and band_mask(ak, 40, H)[R > 110].max() == 0


def test_a_frame_where_the_ai_found_someone_else_uses_the_plain_key():
    ak = key_alpha()
    elsewhere = ai_alpha(shift=150)
    a, fg, used = blend_hair(ak, None, elsewhere, None, 1.0, 40)
    assert not used and np.array_equal(a, ak) and not agrees(ak, elsewhere, 40, H)
    assert not agrees(ak, np.zeros_like(ak), 40, H)                                  # the AI found nobody


def test_restored_pixels_are_despilled():
    ak, aa = key_alpha(), ai_alpha()
    fk = np.zeros((H, W, 3), np.float32)
    fa = np.zeros((H, W, 3), np.float32); fa[..., 0] = 0.5; fa[..., 1] = 0.9; fa[..., 2] = 0.3        # the AI's colour is still green-tinted
    _, fg, _ = blend_hair(ak, fk, aa, fa, 1.0, 40, screen_channel=1)
    assert (fg[LOST][:, 1] <= 0.85 * np.maximum(fg[LOST][:, 0], fg[LOST][:, 2]) + 1e-6).all()
    _, raw, _ = blend_hair(ak, fk, aa, fa, 1.0, 40)
    assert raw[LOST][:, 1].mean() > fg[LOST][:, 1].mean() + 0.2 and np.array_equal(fg[CRISP], fk[CRISP])


def test_restored_pixels_take_the_ai_colour_the_rest_keeps_the_keys():
    ak, aa = key_alpha(), ai_alpha()
    fk = np.zeros((H, W, 3), np.float32); fa = np.ones((H, W, 3), np.float32)
    a, fg, used = blend_hair(ak, fk, aa, fa, 1.0, 40)
    assert used and fg[LOST].mean() > 0.7 and np.array_equal(fg[CRISP], fk[CRISP]) and np.array_equal(fg[R < 40], fk[R < 40]) and fg[R > 110].max() == 0


# -------------------------------------------------------------------------------------------------------- inside the engine, with a stand-in model
class Spy:
    """stands in for the AI model: finds the non-green person and adds wispy 'hair' just outside it"""
    calls = 0
    def reset(self): pass
    def __call__(self, rgb, ds):
        Spy.calls += 1
        person = (rgb[..., 1] < 200).astype(np.uint8)
        a0 = person.astype(np.float32)
        ring = cv2.dilate(person, np.ones((13, 13), np.uint8)).astype(np.float32) - a0
        return np.clip(a0 + 0.7 * ring, 0, 1), rgb.astype(np.float32) / 255.0
    provider = "Test"


@pytest.fixture
def with_model(monkeypatch):
    monkeypatch.setattr(models, "runtime_available", lambda: (True, ""))
    monkeypatch.setattr(models, "is_installed", lambda n: True)
    monkeypatch.setattr(matting, "_model", lambda: Spy())
    Spy.calls = 0


def alpha_view(prep, media, params, t=1.0):
    p = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", **params}}]))
    assert p.plan, [e.message for e in p.report.errors]
    png = render_frame(p.plan, media["video"], t, None, str(media["dir"]), view="alpha")
    return np.asarray(Image.open(io.BytesIO(png)).convert("L"), dtype=float) / 255.0, p


def test_engine_uses_the_model_only_when_asked_and_only_adds_where_the_key_saw_something(prep, media, with_model):
    base, _ = alpha_view(prep, media, {})
    assert Spy.calls == 0                                                            # off by default: the model is not even run
    hair, p = alpha_view(prep, media, {"hair_detail": 1.0, "hair_width": 30})
    assert Spy.calls > 0
    partial = (base > 0.05) & (base < 0.95)                                           # the key's soft edge pixels
    assert partial.sum() > 50 and hair[partial].mean() > base[partial].mean() + 0.1   # restored where the key was unsure and the model is confident
    assert (hair >= base - 1e-9).all()                                                # it never removes anything
    screen_halo = cv2.dilate((base > 0.5).astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & (base < 0.02)
    assert hair[screen_halo].max() <= 0.03                                           # the model halo over pure screen does not come in
    info = [d for d in p.plan.describe() if d["id"] == "k"][0]["hair"]
    assert info and info["used"] > 0 and info["skipped"] == 0


def test_cache_key_and_validation(prep, media, monkeypatch):
    from evp.engine import prepare
    off = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}}]))
    assert off.report.ok                                                             # off: no model needed at all
    from evp.blueprint import Operation, apply_params
    def mk(**kw):
        op = Operation("k", "background_removal", 0, 3, 0, 90, apply_params("background_removal", {"mode": "screen", **kw})[0])
        return matting.matte_key(media["video"], op, 320, 180, 30.0, 0, 90)
    assert mk() != mk(hair_detail=0.5) and mk(hair_detail=0.5) != mk(hair_detail=0.5, hair_width=30) and mk(hair_detail=0.5) == mk(hair_detail=0.5)
    monkeypatch.setattr(models, "runtime_available", lambda: (False, "onnxruntime is not installed"))
    p = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "hair_detail": 0.5}}]))
    assert [e.code for e in p.report.errors] == ["MODEL_RUNTIME_MISSING"] and "Hair detail" in p.report.errors[0].message
    assert prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}}])).report.ok
