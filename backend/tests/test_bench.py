"""Test speed: the measurement is stored with its device and the estimates use it only on that device."""
import json, time
import pytest
from fastapi.testclient import TestClient
from evp.ml import bench, upscale, restore
from evp.server import create_app

H = {"x-evp-token": "tok"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_file", lambda: tmp_path / "benchmark.json")
    monkeypatch.setattr(bench, "_STATE", {"state": "idle", "step": "", "result": None, "error": None})
    return tmp_path / "benchmark.json"


def save(store, **kw):
    store.write_text(json.dumps(dict({"device": bench.device_tag(), "upscale_factor": 0.2, "faces": {"gpen512": 0.4}, "created": 1}, **kw)))


def test_without_a_measurement_the_built_in_estimates_are_used(store):
    assert bench.measured() is None
    base = upscale.estimate_seconds_per_frame(1280, 720, "fast", 2.0, False)
    assert base == pytest.approx(1280 * 720 * 0.25 * 8.5e-6)
    assert restore.estimate_seconds_per_frame(False, 1, "gpen512") == pytest.approx(2.2)


def test_a_measurement_on_this_device_replaces_the_guesses(store):
    save(store)
    base = 1280 * 720 * 0.25 * 8.5e-6
    assert upscale.estimate_seconds_per_frame(1280, 720, "fast", 2.0, True) == pytest.approx(base * 0.2)     # the real speed, whatever the guess for a graphics card was
    assert restore.estimate_seconds_per_frame(True, 2, "gpen512") == pytest.approx(0.8)                       # 2 faces x the measured 0.4 s
    assert restore.estimate_seconds_per_frame(True, 1, "codeformer") == pytest.approx(0.27)                   # not measured (not installed): the built-in guess


def test_a_measurement_from_another_device_is_shown_but_not_used(store):
    save(store, device="Dml|AMD Radeon RX 6600|auto")
    assert bench.measured() is None
    s = bench.status()
    assert s["result"] and s["valid"] is False and s["state"] == "done"
    assert upscale.estimate_seconds_per_frame(1280, 720, "fast", 2.0, False) == pytest.approx(1280 * 720 * 0.25 * 8.5e-6)


def test_the_endpoints_start_a_run_and_report_it(store, monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "run", lambda on_step=lambda s: None: (on_step("Upscaling"), {"device": bench.device_tag(), "upscale_factor": 0.5, "faces": {}})[1])
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    assert c.get("/api/ai-benchmark", headers=H).json()["state"] == "idle"
    assert c.post("/api/ai-benchmark", headers=H).status_code == 200
    for _ in range(40):
        s = c.get("/api/ai-benchmark", headers=H).json()
        if s["state"] != "running": break
        time.sleep(0.05)
    assert s["state"] == "done" and s["result"]["upscale_factor"] == 0.5


def test_a_failed_run_says_why_and_does_not_break_the_next(store, monkeypatch, tmp_path):
    def boom(on_step=lambda s: None): raise RuntimeError("no model")
    monkeypatch.setattr(bench, "run", boom)
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    c.post("/api/ai-benchmark", headers=H)
    for _ in range(40):
        s = c.get("/api/ai-benchmark", headers=H).json()
        if s["state"] != "running": break
        time.sleep(0.05)
    assert s["state"] == "failed" and "no model" in s["error"]
    monkeypatch.setattr(bench, "run", lambda on_step=lambda s: None: {"device": bench.device_tag(), "upscale_factor": 1.0, "faces": {}})
    assert c.post("/api/ai-benchmark", headers=H).json()["state"] == "running"


@pytest.mark.skipif(not (__import__("evp.ml.models", fromlist=["x"]).is_installed("gfpgan")), reason="the face model is not downloaded here")
def test_the_real_test_runs_and_gives_sensible_numbers(store):
    r = bench.run()
    assert r["upscale_us_per_px"] > 0 and r["faces"] and all(v and v > 0 for v in r["faces"].values())
    assert store.is_file() and bench.measured()["upscale_factor"] == pytest.approx(r["upscale_factor"])
