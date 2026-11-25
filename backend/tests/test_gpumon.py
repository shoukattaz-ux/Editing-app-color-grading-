"""GPU visibility: measured proof of use (Windows counters, emulated here), honest states, no false 'GPU active'."""
import json, sys
import pytest
from evp.ml import gpumon
from evp.ml.gpumon import GpuMonitor, parse_sample

DML = {"provider": "Dml", "wanted": "AMD Radeon RX 580 Series", "fell_back": False, "choice": "auto"}
CPU = {"provider": "CPU", "wanted": "CPU", "fell_back": False, "choice": "cpu"}
FELL = {"provider": "CPU", "wanted": "AMD Radeon RX 580 Series", "fell_back": True, "choice": "auto"}


def out(mine=None, total=None, vram=0):
    return json.dumps({"mine": mine or {}, "total": total or {}, "vram_bytes": vram})


def mon(text, windows=True):
    m = GpuMonitor(runner=lambda s: text, windows=windows)
    m.sample_once()
    return m


def test_parse_takes_the_busiest_engine_and_vram():
    s = parse_sample("noise\n" + out({"Compute_0": 37.2, "3D": 4.0}, {"3D": 80, "Compute_0": 37.2}, 512 * 1048576))
    assert s["percent"] == 37.2 and s["system_percent"] == 80.0 and s["vram_mb"] == 512.0
    assert parse_sample("garbage") is None and parse_sample("") is None


def test_active_when_the_app_really_uses_the_card():
    m = mon(out({"Compute_0": 41.0}, {"Compute_0": 55.0}, 300 * 1048576))
    r = m.snapshot(DML)
    assert r["state"] == "active" and r["percent"] == 41.0 and "41%" in r["label"] and r["adapter"].startswith("AMD Radeon")
    assert "300 MB" in r["detail"] and "Compute_0" in r["detail"]


def test_ready_not_active_when_the_card_is_selected_but_idle():
    r = mon(out({}, {"3D": 90.0})).snapshot(DML)                 # the card is busy with OTHER programs (a game) - that is not us
    assert r["state"] == "ready" and r["percent"] == 0.0 and r["system_percent"] == 90.0


def test_processor_modes_say_so_and_why():
    r = mon(out()).snapshot(CPU)
    assert r["state"] == "cpu" and "not used" in r["label"] and "processor-only" in r["detail"]
    f = mon(out()).snapshot(FELL)
    assert f["state"] == "cpu" and f["fell_back"] and "could not be used" in f["detail"]


def test_cpu_selected_but_gpu_measured_busy_is_reported_as_active():
    # e.g. the hardware video encoder works on the card although AI runs on the processor
    r = mon(out({"VideoEncode": 22.0})).snapshot(CPU, [{"state": "running", "encoder": "h264_amf", "current": "encoding"}])
    assert r["state"] == "active" and r["encoder_on_gpu"] is True and "h264_amf" in r["detail"]


def test_not_measurable_platform_never_invents_a_percentage():
    m = GpuMonitor(windows=False)
    r = m.snapshot(DML)
    assert r["percent"] is None and r["measurable"] is False and r["state"] == "ready" and "only available on Windows" in r["detail"]
    j = m.snapshot(DML, [{"state": "running", "encoder": "libx264", "current": "AI matting"}])
    assert j["state"] == "ready" and j["percent"] is None and j["label"] == "GPU selected"      # a running job alone is not proof: no claim, no %
    h = m.snapshot(DML, [{"state": "running", "encoder": "h264_nvenc", "current": "encoding"}])
    assert h["state"] == "active" and h["percent"] is None and "(on the GPU)" in h["detail"]    # the hardware encoder in use IS named by the job


def test_broken_counters_are_reported_not_hidden():
    m = GpuMonitor(runner=lambda s: "", windows=True); m.sample_once()
    r = m.snapshot(DML)
    assert r["state"] == "ready" and "could not be read" in r["detail"]
    m2 = GpuMonitor(runner=lambda s: (_ for _ in ()).throw(RuntimeError("powershell missing")), windows=True); m2.sample_once()
    assert "powershell missing" in m2.snapshot(DML)["detail"]


def test_script_is_a_valid_template_and_names_our_process_tree():
    s = gpumon._PS % 4242
    assert "$root=4242" in s and "GPU Engine" in s and "Dedicated Usage" in s and "ParentProcessId" in s and "$pid" not in s.lower().replace("$procid", "")


def test_amf_is_an_automatic_choice_and_api_endpoint(tmp_path):
    from evp.compiler import pick_encoder
    import evp.compiler as c
    import types
    orig_e, orig_t = c.encoders, c._try_encoder
    c.encoders = lambda: {"libx264", "h264_amf"}; c._try_encoder = lambda n: n == "h264_amf"
    try: assert pick_encoder("h264", "auto") == "h264_amf" and pick_encoder("h265", "auto") == "libx265"
    finally: c.encoders, c._try_encoder = orig_e, orig_t
    from fastapi.testclient import TestClient
    from evp.server import create_app
    cl = TestClient(create_app("tok", str(tmp_path / "d")))
    r = cl.get("/api/gpu", headers={"x-evp-token": "tok"}).json()
    assert r["state"] in ("cpu", "ready", "active") and "label" in r and "detail" in r


def test_keying_job_explains_why_the_gpu_is_idle():
    r = mon(out({}, {})).snapshot(DML, [{"state": "running", "encoder": None, "current": "Green-screen keying (processor) 'remove_green': frame 137/752"}])
    assert r["state"] == "ready" and "runs on the processor by design" in r["detail"]
    ai = mon(out({}, {})).snapshot(DML, [{"state": "running", "encoder": None, "current": "AI matting 'k': frame 1/9"}])
    assert "by design" not in ai["detail"]
