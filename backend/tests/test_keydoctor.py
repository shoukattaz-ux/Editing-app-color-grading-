"""Key doctor: measured findings with real fixes, on synthetic footage whose faults are known."""
import io, subprocess
import numpy as np
import pytest
from PIL import Image
from conftest import bp
import test_cleanplate as T
from evp.blueprint import apply_params
from evp.keydoctor import diagnose_frames, diagnose
from evp.media import ffmpeg_bin

P, _ = apply_params("background_removal", {"mode": "screen"})
XX, YY = T.XX, T.YY
FC = [210, 160, 122]


def u8(f): return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)
def flat(v=0.85): return np.broadcast_to(np.array([30 * v, 200 * v, 40 * v], np.float32) / 255.0, (T.H, T.W, 3)).copy()


def scene(scr, cx=150, subject=None, rim_spill=0.0, holes=False, noise=0.0, room=False, seed=0):
    d = np.hypot(XX - cx, YY - 90); a = np.clip((46 - d) / 4, 0, 1)
    F = np.broadcast_to(np.array(subject or FC, np.float32) / 255.0, (T.H, T.W, 3)).copy()
    if rim_spill: F = np.where(((d < 46) & (d > 38))[..., None], F + np.array([0, rim_spill, 0], np.float32), F)
    if holes:
        for hx, hy in ((cx - 10, 80), (cx + 14, 100), (cx, 70)): a = np.where(np.hypot(XX - hx, YY - hy) < 5, 0.5, a)
    scr = scr.copy()
    if room: scr[:, :60] = np.array([0.5, 0.45, 0.4], np.float32)
    f = a[..., None] * F + (1 - a[..., None]) * scr
    if noise: f = f + np.random.default_rng(seed).normal(0, noise, f.shape).astype(np.float32)
    return u8(f)


def check(mk, burst=False, params=P):
    samples = [(i * 0.4, mk(i)) for i in range(8)]
    return diagnose_frames(samples, [mk(i) for i in range(6)] if burst else [], params)


def ids(r): return {f["id"]: f for f in r["findings"]}


def test_a_healthy_key_gets_a_clean_bill():
    r = check(lambda i: scene(flat(), cx=140 + 4 * i), burst=True)
    assert r["findings"] == [] and r["score"] == 100 and "healthy" in r["summary"] and r["frames_checked"] == 8


def test_uneven_screen_recommends_a_clean_plate():
    f = ids(check(lambda i: scene(T.screen(0.45, 0.4), cx=140 + 4 * i)))
    assert "UNEVEN_SCREEN" in f and f["UNEVEN_SCREEN"]["fix"]["advice"] == "clean_plate" and f["UNEVEN_SCREEN"]["measure"]["screen_evenness"] < 0.55
    assert f["UNEVEN_SCREEN"]["time"] is not None


def test_screen_that_does_not_fill_the_frame_gets_a_garbage_mask_with_the_right_size():
    f = ids(check(lambda i: scene(flat(), cx=200 + 4 * i, room=True)))
    fx = f["SCREEN_EDGE_LEFT"]["fix"]["add_ops"][0]
    assert fx["type"] == "matte_mask" and fx["params"]["mode"] == "garbage" and fx["params"]["x"] == 0.0 and fx["params"]["height"] == 1.0
    assert 0.18 <= fx["params"]["width"] <= 0.26                                        # the room strip is 60 of 320 px = 0.1875
    assert not any(k.startswith("SCREEN_EDGE") and k != "SCREEN_EDGE_LEFT" for k in f)  # nothing on the other sides
    # applying the mask really removes it: the matte there becomes black
    from evp.masks import render_mask
    m = render_mask(fx["params"], 320, 180)
    assert m[90, 30] == 255 and m[90, 200] == 0


def test_see_through_subject_recommends_solidity_above_the_current_value():
    r = ids(check(lambda i: scene(flat(), cx=140 + 4 * i, holes=True)))
    assert r["SEE_THROUGH"]["severity"] == "problem" and r["SEE_THROUGH"]["fix"]["set"]["solidity"] >= 0.3
    hi, _ = apply_params("background_removal", {"mode": "screen", "solidity": 0.6})
    assert ids(check(lambda i: scene(flat(), cx=140 + 4 * i, holes=True), params=hi))["SEE_THROUGH"]["fix"]["set"]["solidity"] >= 0.6       # never lowers it


def test_green_spill_on_a_dark_subject():
    f = ids(check(lambda i: scene(flat(), cx=150, subject=[40, 45, 40], rim_spill=0.04)))
    assert "SPILL" in f and f["SPILL"]["fix"]["set"]["spill_suppression"] is True and f["SPILL"]["fix"]["set"]["spill_strength"] >= 0.8
    off, _ = apply_params("background_removal", {"mode": "screen", "spill_suppression": False})
    assert "Spill removal is off" in ids(check(lambda i: scene(flat(), cx=150, subject=[40, 45, 40], rim_spill=0.04), params=off))["SPILL"]["detail"]


def test_noise_is_reported_and_the_fix_raises_temporal_smoothing():
    f = ids(check(lambda i: scene(flat(), cx=150, noise=0.05, seed=i), burst=True))
    assert "NOISY_SCREEN" in f and f["NOISY_SCREEN"]["fix"]["set"]["temporal_smooth"] > 0.5


def test_no_screen_in_the_footage_points_to_ai_mode():
    gray = lambda i: u8(np.full((T.H, T.W, 3), 0.45, np.float32) + np.random.default_rng(i).normal(0, .01, (T.H, T.W, 3)).astype(np.float32))
    r = check(gray)
    assert [f["id"] for f in r["findings"]] == ["NO_SCREEN"] and r["findings"][0]["fix"]["set"] == {"mode": "ai"} and r["score"] < 90


def test_every_fix_is_made_of_real_settings(prep):
    """each suggested setting must be a valid parameter value of Background Removal, each added operation a valid matte_mask"""
    from evp.registry import OPERATIONS
    bad = [check(lambda i: scene(T.screen(0.45, 0.4), cx=200 + 4 * i, room=True, holes=True, noise=0.05, seed=i), burst=True),
           check(lambda i: scene(flat(), cx=150, subject=[40, 45, 40], rim_spill=0.04))]
    seen = 0
    for r in bad:
        for f in r["findings"]:
            fx = f["fix"] or {}
            for k, v in (fx.get("set") or {}).items():
                ok = apply_params("background_removal", {"mode": "screen", k: v})[1]
                assert not ok, (k, v, ok); seen += 1
            for op in fx.get("add_ops", []):
                assert not apply_params(op["type"], op["params"])[1]; seen += 1
    assert seen >= 4


def test_diagnose_reads_real_footage_through_the_decoder(tmp_path):
    S = T.screen(0.45, 0.4)
    frames = [scene(S, cx=140 + 3 * i, room=True) for i in range(30)]
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p",
                    str(tmp_path / "s.mp4")], input=b"".join(f.tobytes() for f in frames), check=True)
    r = diagnose(str(tmp_path / "s.mp4"), P, 0, 30, 25.0, 320, 180, None)
    got = {f["id"] for f in r["findings"]}
    assert {"SCREEN_EDGE_LEFT", "UNEVEN_SCREEN"} <= got and r["frames_checked"] == 10
    assert all(f["time"] is None or 0 <= f["time"] <= 1.2 for f in r["findings"])


def test_api_endpoint(tmp_path, media):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    H = {"x-evp-token": "tok"}
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "D", "source": media["video"]})
    b = bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}}])
    r = c.post("/api/key/doctor", headers=H, json={"blueprint": b, "op_id": "k"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["frames_checked"] >= 1 and 0 <= j["score"] <= 100 and isinstance(j["findings"], list) and j["summary"]
    assert c.post("/api/key/doctor", headers=H, json={"blueprint": b, "op_id": "nope"}).status_code == 404
    assert c.post("/api/key/doctor", headers=H, json={"blueprint": bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {}}]), "op_id": "g"}).status_code == 422
    assert c.post("/api/key/doctor", json={"blueprint": b, "op_id": "k"}).status_code == 401


def test_a_mask_you_already_drew_silences_that_finding(tmp_path):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    import subprocess
    S = T.screen(0.2, 0.9)
    frames = [scene(S, cx=200 + 3 * (i % 8), room=True) for i in range(30)]
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180", "-r", "25", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p",
                    str(tmp_path / "s.mp4")], input=b"".join(f.tobytes() for f in frames), check=True)
    H = {"x-evp-token": "tok"}
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "D", "source": str(tmp_path / "s.mp4")})
    key = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}}
    r1 = c.post("/api/key/doctor", headers=H, json={"blueprint": bp(ops=[key]), "op_id": "k"}).json()
    f1 = [f for f in r1["findings"] if f["id"] == "SCREEN_EDGE_LEFT"][0]
    mask = {"id": "m", "type": "matte_mask", "start": 0, "parameters": f1["fix"]["add_ops"][0]["params"]}
    r2 = c.post("/api/key/doctor", headers=H, json={"blueprint": bp(ops=[key, mask]), "op_id": "k"}).json()
    assert not any(f["id"] == "SCREEN_EDGE_LEFT" for f in r2["findings"])
    # a chroma key has no 'mode' or edge controls: the doctor says what to use instead of offering settings that do not exist
    ck = {"id": "c", "type": "chroma_key", "start": 0, "parameters": {}}
    r3 = c.post("/api/key/doctor", headers=H, json={"blueprint": bp(ops=[ck]), "op_id": "c"}).json()
    assert all(not (f["fix"] or {}).get("set") or set(f["fix"]["set"]) <= {"similarity", "blend", "spill_suppression", "spill_strength"} for f in r3["findings"])
