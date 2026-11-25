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
