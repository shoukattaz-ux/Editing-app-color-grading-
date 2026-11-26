"""Why-isn't-my-GPU-used diagnosis, hardware decoding of renders, and the instant repeat of a live frame."""
import time
from conftest import bp
import evp.media as media
from evp.compiler import compile_plan, RenderOptions

KEY = {"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "chroma", "spill_suppression": False}}
AMD = [{"index": 0, "name": "AMD Radeon RX 6700 XT", "vram_mb": 12288, "vendor": 0x1002, "software": False}]


def topics(d): return {x["topic"]: x for x in d}


def test_diagnosis_tells_an_amd_user_exactly_what_is_wrong(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(media, "encoders", lambda: {"libx264", "h264_amf"})
    monkeypatch.setattr(media, "_try_encoder", lambda n: False)
    monkeypatch.setattr(media, "encoder_error", lambda n: "DLL amfrt64.dll failed to open")
    monkeypatch.setattr(media, "hwaccels", lambda: ("d3d11va", "dxva2"))
    d = topics(media.gpu_diagnosis({"provider": "CPU", "running_on": "the processor"}, AMD, ["CPUExecutionProvider"]))
    assert "AMD" in d["Graphics cards"]["text"]
    ai = d["AI (keying, hair, upscaling)"]; assert ai["status"] == "warn" and "DirectML" in ai["text"] and "onnxruntime-directml" in ai["fix"]
    enc = d["Video encoding (export / preview video)"]; assert enc["status"] == "warn" and "amfrt64" in enc["text"] and "Radeon" in enc["fix"]
    assert d["Video decoding (source file)"]["status"] == "ok"
    assert "processor" in d["Keying, colour grading, compositing"]["text"]


def test_diagnosis_is_green_when_everything_works(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(media, "encoders", lambda: {"libx264", "h264_amf"})
    monkeypatch.setattr(media, "_try_encoder", lambda n: n == "h264_amf")
    monkeypatch.setattr(media, "hwaccels", lambda: ("d3d11va",))
    d = topics(media.gpu_diagnosis({"provider": "Dml", "running_on": "AMD Radeon RX 6700 XT"}, AMD, ["DmlExecutionProvider", "CPUExecutionProvider"]))
    assert d["AI (keying, hair, upscaling)"]["status"] == "ok" and d["Video encoding (export / preview video)"]["status"] == "ok" and "h264_amf" in d["Video encoding (export / preview video)"]["text"]


def test_renders_decode_on_the_gpu_but_single_frames_never_do(prep, media_files=None):
    p = prep(bp(ops=[KEY]))
    on = compile_plan(p.plan, "in.mp4", "out.mp4", RenderOptions(hwdecode=True)).args
    assert "-hwaccel" in on and on.index("-hwaccel") < on.index("-i")
    assert "-hwaccel" not in compile_plan(p.plan, "in.mp4", "out.mp4", RenderOptions(hwdecode=False)).args
    assert "-hwaccel" not in compile_plan(p.plan, "in.mp4", "-", RenderOptions(hwdecode=True, frame_only=True)).args


def test_gpu_endpoint_carries_the_diagnosis(tmp_path):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    r = TestClient(create_app("tok", str(tmp_path / "d"))).get("/api/gpu", headers={"x-evp-token": "tok"}).json()
    assert isinstance(r["diagnosis"], list) and r["diagnosis"] and all({"topic", "status", "text"} <= set(x) for x in r["diagnosis"])


def test_the_same_live_frame_comes_back_instantly_and_a_change_does_not(tmp_path, media):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    H = {"x-evp-token": "tok"}
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Q", "source": media["video"]})
    ops = lambda sat: [KEY, {"id": "r", "type": "background_replacement", "start": 0, "depends_on": ["k"], "parameters": {"color": "#204060"}},
                       {"id": "g", "type": "color_grading", "start": 0, "depends_on": ["r"], "parameters": {"saturation": sat}}]
    body = lambda sat: {"blueprint": bp(ops=ops(sat)), "t": 1.0, "width": 480}
    t0 = time.time(); a = c.post("/api/preview/frame", headers=H, json=body(0.0)); first = time.time() - t0
    t0 = time.time(); b = c.post("/api/preview/frame", headers=H, json=body(0.0)); again = time.time() - t0
    other = c.post("/api/preview/frame", headers=H, json=body(0.5))
    assert a.status_code == b.status_code == other.status_code == 200
    assert a.content == b.content and again < first * 0.6 + 0.02            # a repeat is served from the cache
    assert other.content != a.content                                          # any change renders a new picture


def test_two_renders_at_once_do_not_share_the_text_file(prep, tmp_path):
    """a live frame and a preview video render in parallel: each needs its own text file (one cleaning up must not break the other)"""
    op = {"id": "t", "type": "text_overlay", "start": 0, "parameters": {"text": "Hello"}}
    p = prep(bp(ops=[op]))
    a = compile_plan(p.plan, "in.mp4", "a.mp4", RenderOptions(), str(tmp_path)).work_files
    b = compile_plan(p.plan, "in.mp4", "b.mp4", RenderOptions(), str(tmp_path)).work_files
    ta, tb = [f for f in a if f.endswith(".txt")], [f for f in b if f.endswith(".txt")]
    assert ta and tb and set(ta).isdisjoint(tb)


def test_a_live_frame_inside_a_long_cached_matte_lands_on_exactly_that_frame(tmp_path):
    """after Apply the whole clip's matte is cached; a live frame later in the clip SEEKS into that file (instead of generating and throwing
    away every earlier frame). It must show precisely the matte of that frame - the subject moves, so a frame off would show."""
    import numpy as np, subprocess, cv2
    from evp.samples import make_greenscreen
    from evp.engine import prepare, render_frame, ensure_mattes
    from evp.security import PathPolicy
    from evp.media import ffmpeg_bin
    v = str(make_greenscreen(tmp_path / "gs.mp4", 4))
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "temporal_smooth": 0.0}}]
    p = prepare({"blueprint_version": "1.0", "operations": ops}, v, PathPolicy([tmp_path]), tmp_path, strict=True)
    ensure_mattes(p.plan, v, str(tmp_path))                                    # the whole range, as after "Apply to full video"
    ref = p.plan.mattes["k"]; fps = p.plan.blueprint.output.fps
    assert ref.start_frame == 0 and ref.frames > 60
    for n in (37, 61):
        got = cv2.imdecode(np.frombuffer(render_frame(p.plan, v, n / fps, None, str(tmp_path), view="alpha"), np.uint8), cv2.IMREAD_GRAYSCALE).astype(float)
        raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", ref.path, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        want = np.frombuffer(raw, np.uint8).reshape(got.shape).astype(float)
        wrong = [np.abs(got - cv2.imdecode(np.frombuffer(render_frame(p.plan, v, (n + d) / fps, None, str(tmp_path), view="alpha"), np.uint8), cv2.IMREAD_GRAYSCALE)).mean() for d in (1,)]
        assert np.abs(got - want).mean() < 1.0, f"frame {n}: live matte differs from the cached frame by {np.abs(got - want).mean():.2f}"
        assert wrong[0] > 3 * max(0.05, np.abs(got - want).mean()), "the test clip must move enough to tell neighbouring frames apart"


def test_lookup_blend_gives_the_same_picture_as_the_per_pixel_formula(tmp_path):
    """the speed-up turns blend=all_expr into lut2: same numbers (within 1 level of rounding) for the formulas the compositor uses"""
    import subprocess, numpy as np
    from evp.compiler import fast_blends
    from evp.media import ffmpeg_bin
    src = "testsrc2=s=320x180:d=0.04,format=gbrp"
    for e in ["clip(A*(1+0.660*(B/128-1))\\,0\\,255)", "clip(A*(255-B)/128*0.480\\,0\\,255)", "A*B/255", "clip(B+(A-B)*0.62500\\,0\\,255)"]:
        out = []
        for chain in (f"blend=all_expr='{e}'", fast_blends(f"blend=all_expr='{e}'")):
            g = f"{src}[a];testsrc=s=320x180:d=0.04,format=gbrp,hflip[b];[a][b]{chain},format=rgb24"
            r = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", g, "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
            assert r.returncode == 0, r.stderr
            out.append(np.frombuffer(r.stdout, np.uint8).astype(int))
        assert "lut2" in fast_blends(f"blend=all_expr='{e}'")
        assert np.abs(out[0] - out[1]).max() <= 1, e
    assert fast_blends("blend=all_expr='if(gt(Y\\,3)\\,A\\,B)'").startswith("blend=")         # position-dependent formulas stay a blend


def test_draft_processes_at_preview_size_and_scales_pixel_settings(tmp_path):
    """fast draft: the whole live frame (key included) runs at preview size; pixel-sized settings shrink with it; the full plan is untouched"""
    from evp.samples import make_greenscreen
    from evp.engine import prepare, draft_plan, render_frame
    from evp.security import PathPolicy
    import numpy as np, cv2
    v = str(make_greenscreen(tmp_path / "gs.mp4", 2, size="1920x1080"))
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "edge_soften": 2.0, "edge_shrink": 1.0}},
           {"id": "r", "type": "background_replacement", "start": 0, "depends_on": ["k"], "parameters": {"color": "#203040", "blur": 4.0}}]
    p = prepare({"blueprint_version": "1.0", "operations": ops}, v, PathPolicy([tmp_path]), tmp_path, strict=True)
    d = draft_plan(p.plan, 960)
    assert (d.blueprint.output.width, d.blueprint.output.height) == (960, 540)
    k = next(o for o in d.steps if o.id == "k"); r = next(o for o in d.steps if o.id == "r")
    assert abs(k.params["edge_soften"] - 1.0) < 1e-9 and abs(k.params["edge_shrink"] - 0.5) < 1e-9 and abs(r.params["blur"] - 2.0) < 1e-9
    assert p.plan.blueprint.output.width == 1920 and next(o for o in p.plan.steps if o.id == "k").params["edge_soften"] == 2.0     # the real plan is untouched
    full = cv2.imdecode(np.frombuffer(render_frame(p.plan, v, 1.0, 960, str(tmp_path)), np.uint8), 1).astype(float)
    dr = cv2.imdecode(np.frombuffer(render_frame(d, v, 1.0, 960, str(tmp_path)), np.uint8), 1).astype(float)
    assert full.shape == dr.shape and np.abs(full - dr).mean() < 2.0            # the draft looks like the real thing
    small = tmp_path / "small.mp4"
    make_greenscreen(small, 1, size="640x360")
    p2 = prepare({"blueprint_version": "1.0", "operations": ops[:1]}, str(small), PathPolicy([tmp_path]), tmp_path, strict=True)
    assert draft_plan(p2.plan, 960) is p2.plan                                 # already preview size: nothing to do


def test_draft_frame_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from evp.server import create_app
    from evp.samples import make_greenscreen
    v = str(make_greenscreen(tmp_path / "gs.mp4", 2, size="1280x720"))
    H = {"x-evp-token": "tok"}
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Q", "source": v})
    b = {"blueprint": bp(ops=[KEY]), "t": 1.0, "width": 960}
    a, d = c.post("/api/preview/frame", headers=H, json=b), c.post("/api/preview/frame", headers=H, json={**b, "draft": True})
    assert a.status_code == d.status_code == 200 and a.content[:4] == d.content[:4] == b"\x89PNG"
