"""Clean plate: the keyer knows the empty screen pixel by pixel; falls back when the plate does not fit."""
import io, subprocess
import numpy as np
import pytest
from PIL import Image
from conftest import bp
from evp.blueprint import apply_params
from evp.engine import render_frame
from evp.media import ffmpeg_bin
from evp.ml.screenkey import ScreenKeyer
from evp.ml.cleanplate import load_plate

W, H = 320, 180
YY, XX = np.mgrid[0:H, 0:W].astype(np.float32)
FAR = np.hypot(XX - 160, YY - 90) > 55


def screen(amp=0.3, shadow=0.5):
    """a wrinkled screen with a dark shadow band (what a smooth model cannot guess)"""
    import cv2
    M = 0.8 + amp * np.sin(XX / 7.0) * np.cos(YY / 11.0) + 0.1 * np.sin(XX / 31 + YY / 17)
    M = cv2.GaussianBlur(np.where((XX > 215) & (XX < 255), M * shadow, M), (0, 0), 1.2)
    return (np.stack([30 * M, 200 * M, 40 * M], -1) / 255.0).astype(np.float32)


def scene(scr, drift=1.0, cx=160):
    a = np.clip((46 - np.hypot(XX - cx, YY - 90)) / 4, 0, 1)
    F = np.array([210, 160, 122], np.float32) / 255.0
    return np.clip(a[..., None] * F + (1 - a[..., None]) * scr * drift, 0, 1), a


def key(img, plate=None, **kw):
    p, _ = apply_params("background_removal", {"mode": "screen", **kw})
    k = ScreenKeyer(p, plate)
    for _ in range(2): a, _fg = k((img * 255 + 0.5).astype(np.uint8))
    return a, k


def test_plate_removes_the_haze_a_smooth_model_leaves_and_survives_exposure_drift():
    S = screen(); f, gt = scene(S, drift=0.92)                       # the lights are 8 % dimmer than when the plate was recorded
    a0, k0 = key(f); a1, k1 = key(f, S)
    assert a0[FAR].max() > 0.2                                          # without the plate: haze around the wrinkles / shadow band
    assert a1[FAR].max() < 0.05 and a1[FAR].mean() < a0[FAR].mean() / 3 # with it: the screen is clean
    assert np.abs(a1 - gt)[gt > 0.99].mean() < 0.01 and k1.plate_frames == 2 and k1.plate_rejected == 0
    h = screen(0.45, 0.4); fh, _ = scene(h, 0.92)
    assert key(fh, h)[0][FAR].max() < 0.8 * key(fh)[0][FAR].max()       # a harsher screen: clearly better too


def test_strength_zero_is_the_normal_key_and_a_wrong_plate_is_ignored():
    S = screen(); f, _ = scene(S)
    base, _ = key(f)
    wrong, kw = key(f, np.roll(S, 25, axis=1))                          # camera moved between plate and shot
    assert np.array_equal(wrong, base) and kw.plate_rejected == 2 and kw.plate_frames == 0


def test_plate_loader_picture_and_averaged_video(tmp_path):
    S = (screen() * 255 + 0.5).astype(np.uint8)
    Image.fromarray(S).save(tmp_path / "plate.png")
    p = load_plate(str(tmp_path / "plate.png"), W, H)
    assert p.shape == (H, W, 3) and np.abs(p * 255 - S).max() <= 1
    bigger = load_plate(str(tmp_path / "plate.png"), W * 2, H * 2)
    assert bigger.shape == (H * 2, W * 2, 3)
    rng = np.random.default_rng(0)
    raw = b"".join((np.clip(S.astype(int) + rng.integers(-20, 21, S.shape), 0, 255).astype(np.uint8)).tobytes() for _ in range(30))
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0",
                    "-pix_fmt", "yuv444p", str(tmp_path / "plate.mp4")], input=raw, check=True)
    v = load_plate(str(tmp_path / "plate.mp4"), W, H)
    single = np.clip(S.astype(int) + rng.integers(-20, 21, S.shape), 0, 255)
    assert np.abs(v * 255 - S).mean() < 0.4 * np.abs(single - S).mean()   # averaging many noisy frames beats one noisy frame
    with pytest.raises(RuntimeError): load_plate(str(tmp_path / "missing.mp4"), W, H)


@pytest.fixture
def shot(tmp_path):
    S = screen(0.45, 0.4)
    frames = []
    for i in range(10):
        f, _ = scene(S, 0.94, cx=140 + 4 * i)
        frames.append((f * 255 + 0.5).astype(np.uint8).tobytes())
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0",
                    "-pix_fmt", "yuv444p", str(tmp_path / "shot.mp4")], input=b"".join(frames), check=True)
    Image.fromarray((S * 255 + 0.5).astype(np.uint8)).save(tmp_path / "plate.png")
    return tmp_path


def alpha_view(prep_dir, plate, strength=1.0, t=0.2):
    from evp.engine import prepare
    from evp.security import PathPolicy
    params = {"mode": "screen", **({"clean_plate_asset_id": "plate", "clean_plate_strength": strength} if plate else {})}
    b = bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": params}],
           assets=[{"id": "plate", "type": "image", "path": "plate.png"}] if plate else [])
    p = prepare(b, str(prep_dir / "shot.mp4"), PathPolicy([prep_dir]), prep_dir, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    png = render_frame(p.plan, str(prep_dir / "shot.mp4"), t, None, str(prep_dir / f"c_{plate}_{strength}"), view="alpha")
    return np.asarray(Image.open(io.BytesIO(png)).convert("L"), dtype=float) / 255.0, p


def test_end_to_end_in_the_engine(shot):
    base, _ = alpha_view(shot, False)
    with_plate, p = alpha_view(shot, True)
    far = np.hypot(XX - 148, YY - 90) > 60
    assert with_plate[far].max() < base[far].max() * 0.85 and with_plate[far].mean() <= base[far].mean()
    assert np.abs(with_plate[np.hypot(XX - 148, YY - 90) < 30] - 1).mean() < 0.02          # the subject stays solid
    # strength: 0 = the plain key, 1 = the plate key, 0.5 in between (a mix of the two results)
    zero, _ = alpha_view(shot, True, strength=0.0)
    assert np.abs(zero - base).max() < 1e-6
    half, _ = alpha_view(shot, True, strength=0.5)
    assert np.allclose(half, 0.5 * base + 0.5 * with_plate, atol=0.01)
    info = [d for d in p.plan.describe() if d["id"] == "k"][0]
    assert info["plate"] and info["plate"]["used"] > 0 and info["plate"]["rejected"] == 0


def test_cache_key_changes_with_the_plate_file_and_strength(shot, tmp_path):
    from evp.ml.matting import matte_key
    from evp.engine import prepare
    from evp.security import PathPolicy
    mk = lambda strength, plate: matte_key(str(shot / "shot.mp4"), prepare(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "clean_plate_asset_id": "plate", "clean_plate_strength": strength}}],
         assets=[{"id": "plate", "type": "image", "path": "plate.png"}]), str(shot / "shot.mp4"), PathPolicy([shot]), shot, strict=True).plan.steps[0], W, H, 25.0, 0, 10, None, plate)
    a = mk(1.0, str(shot / "plate.png")); assert a == mk(1.0, str(shot / "plate.png"))
    assert a != mk(0.5, str(shot / "plate.png")) and a != mk(1.0, None)
    import os, time
    time.sleep(0.01); os.utime(shot / "plate.png", None)
    assert a != mk(1.0, str(shot / "plate.png"))                                          # re-recorded plate: matte is recomputed


def test_plate_in_other_modes_is_reported_and_asset_type_checked(prep, media):
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "clean_plate_asset_id": "bg"}}]
    p = prep(bp(ops=ops, assets=[{"id": "bg", "type": "image", "path": "bg.png"}]))
    assert p.report.ok and any(w.code == "CLEAN_PLATE_IGNORED" for w in p.report.warnings)
    bad = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "clean_plate_asset_id": "l"}}], assets=[{"id": "l", "type": "lut", "path": "warm.cube"}]))
    assert not bad.report.ok
