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
    assert s["operations"]["hair_edge_refinement"]["supported"] is False


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
    inv = client.post("/api/render", headers=H, json={"blueprint": bp(ops=[{"id": "x", "type": "hair_edge_refinement", "start": 0, "end": 1}]), "preset": "export"})
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


# ---------------------------------------------------------------- problem reports (Cloudflare R2, S3 API)
import http.server, threading as _th, zipfile as _zip, io as _io


class _FakeR2:
    """local stand-in for the S3 endpoint: records every PUT"""
    def __init__(self, status=200):
        self.puts, outer = [], self
        class H(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                outer.puts.append((self.path, dict(self.headers), body)); self.send_response(status); self.end_headers()
            def log_message(self, *a): pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H); self.url = f"http://127.0.0.1:{self.srv.server_port}"
        _th.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self): self.srv.shutdown()


def test_sigv4_matches_the_published_aws_test_vector():
    import datetime as dt
    from evp.reporter import sigv4_headers
    h = sigv4_headers("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"range": "bytes=0-9", "x-amz-content-sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
                      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                      region="us-east-1", now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc))
    assert h["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_report_upload_content_masking_and_secret_handling(tmp_path, monkeypatch):
    from evp.reporter import Reporter
    monkeypatch.setenv("HOME", str(tmp_path))
    fake = _FakeR2()
    try:
        r = Reporter(tmp_path / "data", "9.9", allow_local=True)
        assert not r.configured() and not r.send("test", "x")["ok"]
        r.save_config(fake.url, "evp-reports", "AKIDEXAMPLE0000", "SECRETSECRETSECRET", True, True)
        assert (tmp_path / "data" / "report.json").stat().st_mode & 0o077 == 0                        # owner-only file
        pub = r.public_config()
        assert pub["has_secret"] and "SECRETSECRETSECRET" not in json.dumps(pub) and pub["access_key_id_masked"] == "…0000"
        bp = {"project": {"source_video": f"{tmp_path}/secret folder/clip.mp4"}, "assets": [{"id": "a", "type": "image", "path": f"{tmp_path}/pics/bg.png"}]}
        res = r.send("render_failed", f"ffmpeg failed reading {tmp_path}/secret folder/clip.mp4", {"x": 1}, bp, f"error in {tmp_path}/x")
        assert res["ok"], res
        path, headers, body = fake.puts[0]
        assert path.startswith("/evp-reports/reports/") and path.endswith(".zip")
        auth = headers["Authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE0000/") and "SECRETSECRETSECRET" not in json.dumps(headers)
        z = _zip.ZipFile(_io.BytesIO(body)); names = set(z.namelist())
        assert {"report.json", "blueprint.json", "log.txt"} <= names
        everything = " ".join(z.read(n).decode() for n in names)
        assert str(tmp_path) not in everything and "SECRETSECRETSECRET" not in everything       # home folder masked, no secret
        assert "clip.mp4" in z.read("blueprint.json").decode() and "secret folder" not in z.read("blueprint.json").decode()   # paths reduced to file names
        assert not any(n.endswith((".mp4", ".png", ".mkv")) for n in names)                         # never media
    finally: fake.close()


def test_report_failure_is_queued_and_retried_and_auto_reports_are_rate_limited(tmp_path):
    from evp.reporter import Reporter
    bad = _FakeR2(status=403)
    r = Reporter(tmp_path, "1", allow_local=True); r.save_config(bad.url, "bkt-1", "AKID", "SECRET", True, True)
    res = r.send("test", "boom")
    assert not res["ok"] and res["queued"] and r.public_config()["pending"] == 1 and "SECRET" not in json.dumps(res)
    bad.close()
    good = _FakeR2(); r.save_config(good.url, "bkt-1", None, None, None, None)
    try:
        assert r.flush_outbox() == 1 and r.public_config()["pending"] == 0 and len(good.puts) == 1
        sent = [r.auto("render_failed", f"unique failure {chr(65 + i)}") for i in range(10)]
        assert sum(sent) == 6                                                                       # 6 per hour at most
        assert not r.auto("render_failed", "unique failure A")                                       # and never the same one twice
        r.save_config(None, None, None, None, None, False)
        assert not r.auto("render_failed", "another one")                                            # auto_send off = silent
    finally: good.close()


def test_report_endpoint_must_be_an_r2_address():
    from evp.reporter import validate_endpoint
    assert validate_endpoint("https://94cd5cc82406d3f30d1eb8b8c245d661.r2.cloudflarestorage.com/") .endswith(".r2.cloudflarestorage.com")
    for bad in ("http://abc.r2.cloudflarestorage.com", "https://evil.example.com", "https://r2.cloudflarestorage.com.evil.com", "ftp://x"):
        with pytest.raises(ValueError): validate_endpoint(bad)


def test_report_api_never_returns_the_secret_and_job_failures_report_automatically(tmp_path, media, monkeypatch):
    fake = _FakeR2()
    monkeypatch.setenv("EVP_REPORT_ALLOW_LOCAL", "1")
    monkeypatch.setenv("HOME", str(tmp_path))                      # paths under the home folder are what gets masked
    c = TestClient(create_app("tok", str(tmp_path / "data")))
    try:
        r = c.put("/api/report/config", headers=H, json={"endpoint": fake.url, "bucket": "evp-reports", "access_key_id": "AKID1234", "secret_access_key": "TOPSECRET"})
        assert r.status_code == 200 and "TOPSECRET" not in r.text and r.json()["configured"]
        assert "TOPSECRET" not in c.get("/api/report/config", headers=H).text
        assert c.put("/api/report/config", headers=H, json={"endpoint": "https://evil.example.com"}).status_code == 422
        assert c.post("/api/report/test", headers=H).json()["ok"] and len(fake.puts) == 1
        # a real failing render sends a report on its own
        c.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "P", "source": media["video"]})
        import evp.engine as eng
        def boom(*a, **k): raise RuntimeError(f"filter graph exploded at {tmp_path}/secret")
        monkeypatch.setattr(eng, "compile_plan", boom)
        job = c.post("/api/render", headers=H, json={"blueprint": bp(), "filename": "x"}).json()["job"]
        assert wait(c, job["id"])["state"] == "failed"
        t = time.time()
        while len(fake.puts) < 2 and time.time() - t < 10: time.sleep(0.1)
        assert len(fake.puts) == 2, "no automatic report arrived"
        z = _zip.ZipFile(_io.BytesIO(fake.puts[1][2]))
        rep = json.loads(z.read("report.json"))
        assert rep["kind"] == "render_failed" and "filter graph exploded" in rep["message"] and str(tmp_path) not in z.read("report.json").decode()
        assert "blueprint.json" in z.namelist()
    finally: fake.close()
