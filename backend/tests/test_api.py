import json, time
import pytest
from fastapi.testclient import TestClient
from evp.server import create_app
from conftest import bp

H = {"x-evp-token": "tok"}
ASSETS = [{"id": "bg", "type": "image", "path": "assets/bg.png"}]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app("tok", str(tmp_path / "data")))


@pytest.fixture
def proj(client, tmp_path, media):
    r = client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "P", "source": media["video"]})
    assert r.status_code == 200, r.text
    a = client.post("/api/project/asset", headers=H, json={"path": media["bg"]}).json()["path"]
    assert a == "assets/bg.png"
    return r.json()


def wait(client, job_id, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{job_id}", headers=H).json()
        if j["state"] in ("done", "failed", "cancelled"): return j
        time.sleep(0.1)
    raise TimeoutError


def test_requires_token(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/schema").status_code == 401
    assert client.get("/api/schema", headers={"x-evp-token": "wrong"}).status_code == 401
    assert client.get("/api/schema?token=tok").status_code == 200


def test_schema_lists_registry(client):
    s = client.get("/api/schema", headers=H).json()
    assert s["operations"]["color_grading"]["parameters"]["contrast"]["maximum"] == 3.0
    assert s["operations"]["face_relighting"]["supported"] is False


def test_no_project_gives_409(client):
    assert client.post("/api/blueprint/validate", headers=H, json={"blueprint": {}}).status_code == 409


def test_new_project_reports_video(proj):
    assert proj["video"]["width"] == 320 and proj["video"]["fps"] == 30.0


def test_validate_reports_errors_and_plan(client, proj):
    ops = [{"id": "k", "type": "chroma_key", "start": 0, "end": 3}, {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg"}}]
    ok = client.post("/api/blueprint/validate", headers=H, json={"blueprint": bp(ops=ops, assets=ASSETS)}).json()
    assert ok["ok"] and [s["id"] for s in ok["plan"]] == ["k", "r"]
    bad = client.post("/api/blueprint/validate", headers=H, json={"blueprint": bp(ops=[{"id": "x", "type": "nope", "start": 0, "end": 1}])}).json()
    assert not bad["ok"] and bad["errors"][0]["code"] == "UNKNOWN_OPERATION"
    raw = client.post("/api/blueprint/validate", headers=H, json={"blueprint": "{not json"}).json()
    assert raw["errors"][0]["code"] == "INVALID_JSON"


def test_path_traversal_asset_blocked(client, proj):
    r = client.post("/api/blueprint/validate", headers=H, json={"blueprint": bp(assets=[{"id": "x", "type": "image", "path": "../../../etc/passwd.png"}])}).json()
    assert r["errors"][0]["code"] == "UNSAFE_PATH"
    assert client.get("/api/media/project", headers=H, params={"path": "../../etc/passwd"}).status_code == 400


def test_frame_preview_original_vs_edited(client, proj):
    b = bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 3, "parameters": {"background_color": "#ff0000"}}])
    e = client.post("/api/preview/frame", headers=H, json={"blueprint": b, "t": 1, "width": 160})
    o = client.post("/api/preview/frame", headers=H, json={"blueprint": b, "t": 1, "width": 160, "original": True})
    assert e.status_code == o.status_code == 200 and e.content[:4] == b"\x89PNG" and e.content != o.content
    bad = client.post("/api/preview/frame", headers=H, json={"blueprint": bp(ops=[{"id": "q", "type": "nope"}]), "t": 1})
    assert bad.status_code == 422


def test_proxy_render_and_cache_and_serving(client, proj):
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"preset": "warm_film"}}])
    r = client.post("/api/render", headers=H, json={"blueprint": b, "preset": "proxy"}).json()
    assert not r["cached"] and wait(client, r["job"]["id"])["state"] == "done"
    again = client.post("/api/render", headers=H, json={"blueprint": b, "preset": "proxy"}).json()
    assert again["cached"] and again["output"] == r["output"]
    m = client.get("/api/media/project", headers=H, params={"path": r["output"]}, )
    assert m.status_code == 200 and len(m.content) > 1000
    rng = client.get("/api/media/project", headers={**H, "range": "bytes=0-99"}, params={"path": r["output"]})
    assert rng.status_code == 206


def test_export_job_lifecycle_and_failure_paths(client, proj, media):
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 3}], output={"resolution": "160x90", "fps": 30, "preset": "ultrafast"})
    r = client.post("/api/render", headers=H, json={"blueprint": b, "preset": "export", "filename": "../../bad name", "encoder": "cpu"}).json()
    j = wait(client, r["job"]["id"])
    assert j["state"] == "done" and "/exports/" in j["output"] and ".." not in r["output"]
    assert client.post(f"/api/jobs/{r['job']['id']}/resume", headers=H).status_code == 409
    assert client.get("/api/jobs/nope", headers=H).status_code == 404
    inv = client.post("/api/render", headers=H, json={"blueprint": bp(ops=[{"id": "x", "type": "face_relighting", "start": 0, "end": 1}]), "preset": "export"})
    assert inv.status_code == 422


def test_save_open_cycle_over_api(client, proj, tmp_path):
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 1}])
    assert client.put("/api/project/blueprint", headers=H, json={"blueprint": b}).status_code == 200
    v = client.post("/api/project/save", headers=H).json()["version"]
    c2 = TestClient(create_app("tok", str(tmp_path / "d2")))
    p = c2.post("/api/project/open", headers=H, json={"path": proj["path"]}).json()
    assert p["blueprint"] == b and p["version"] == v and p["video"]["width"] == 320
    assert c2.get("/api/project/recent", headers=H).json()[0]["name"] == "P"


def test_recovery_flow_over_api(client, proj, tmp_path):
    time.sleep(0.02)
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 1}])
    client.put("/api/project/blueprint", headers=H, json={"blueprint": b})      # autosave only, no save
    c2 = TestClient(create_app("tok", str(tmp_path / "d3")))
    p = c2.post("/api/project/open", headers=H, json={"path": proj["path"]}).json()
    assert p["recovery_available"] and p["blueprint"]["operations"] == []
    assert c2.post("/api/project/recover", headers=H).json()["blueprint"] == b


def test_missing_source_is_reported_not_crashed(client, tmp_path, media):
    import shutil
    s = tmp_path / "tmp.mp4"; shutil.copy(media["video"], s)
    p = client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Q", "source": str(s)}).json()
    client.post("/api/project/save", headers=H)
    s.unlink()
    c2 = TestClient(create_app("tok", str(tmp_path / "d4")))
    o = c2.post("/api/project/open", headers=H, json={"path": p["path"]}).json()
    assert o["missing"]["source"] and o["video"] is None
    rel = c2.post("/api/project/relink", headers=H, json={"kind": "source", "path": media["video"]}).json()
    assert rel["video"] and not rel["missing"]["source"]


def test_corrupt_source_rejected(client, tmp_path):
    bad = tmp_path / "bad.mp4"; bad.write_bytes(b"not a video")
    client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Z"})
    r = client.post("/api/project/source", headers=H, json={"path": str(bad)})
    assert r.status_code == 400 and "orrupt" in r.json()["detail"]
