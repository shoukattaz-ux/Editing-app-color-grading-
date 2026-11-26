"""Media bin: everything imported stays listed and reusable, independent of the blueprint."""
import shutil
import pytest
from fastapi.testclient import TestClient
from evp.server import create_app
from conftest import bp

H = {"x-evp-token": "tok"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app("tok", str(tmp_path / "data")))


@pytest.fixture
def proj(client, tmp_path, media):
    r = client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "M", "source": media["video"]})
    assert r.status_code == 200, r.text
    return r.json()


def bin_(client): return client.get("/api/project/media", headers=H).json()


def test_imported_background_stays_in_the_bin_even_when_the_blueprint_does_not_reference_it(client, proj, media):
    a = client.post("/api/project/asset", headers=H, json={"path": media["bg"]}).json()["path"]
    client.put("/api/project/blueprint", headers=H, json={"blueprint": bp()})            # the blueprint never registered the asset (or the edit was undone)
    b = bin_(client)
    assert [x["path"] for x in b["assets"]] == [a]
    it = b["assets"][0]
    assert it["kind"] == "image" and it["exists"] and it["width"] and it["height"] and it["size"] > 0
    lut = client.post("/api/project/asset", headers=H, json={"path": media["lut"]}).json()["path"]
    vid = client.post("/api/project/asset", headers=H, json={"path": media["silent"]}).json()["path"]
    kinds = {x["path"]: x for x in bin_(client)["assets"]}
    assert kinds[lut]["kind"] == "lut" and kinds[vid]["kind"] == "video" and kinds[vid]["duration"] > 1 and kinds[vid]["has_audio"] is False


def test_source_videos_are_remembered_when_replaced(client, proj, media):
    s1 = bin_(client)["sources"]
    assert len(s1) == 1 and s1[0]["current"] and s1[0]["name"] == "gs.mp4"
    client.post("/api/project/source", headers=H, json={"path": media["silent"]})
    s2 = bin_(client)["sources"]
    assert [s["name"] for s in s2] == ["silent.mp4", "gs.mp4"] and [s["current"] for s in s2] == [True, False]     # old one stays, reusable
    client.post("/api/project/source", headers=H, json={"path": media["video"]})
    assert [s["name"] for s in bin_(client)["sources"]] == ["gs.mp4", "silent.mp4"]                                   # no duplicate, newest first


def test_the_bin_survives_closing_the_app(client, proj, media, tmp_path):
    client.post("/api/project/asset", headers=H, json={"path": media["bg"]})
    client.post("/api/project/source", headers=H, json={"path": media["silent"]})
    client.post("/api/project/save", headers=H)
    c2 = TestClient(create_app("tok", str(tmp_path / "d2")))
    c2.post("/api/project/open", headers=H, json={"path": proj["path"]})
    b = bin_(c2)
    assert len(b["assets"]) == 1 and {s["name"] for s in b["sources"]} == {"gs.mp4", "silent.mp4"}


def test_a_missing_source_is_still_listed_as_missing(client, proj, media, tmp_path):
    keep = tmp_path / "temp.mp4"; shutil.copy(media["video"], keep)
    client.post("/api/project/source", headers=H, json={"path": str(keep)})
    client.post("/api/project/source", headers=H, json={"path": media["video"]})
    keep.unlink()
    gone = [s for s in bin_(client)["sources"] if s["name"] == "temp.mp4"][0]
    assert gone["exists"] is False


def test_thumbnails(client, proj, media):
    a = client.post("/api/project/asset", headers=H, json={"path": media["bg"]}).json()["path"]
    r = client.get("/api/media/thumb", headers=H, params={"src": "asset", "path": a, "w": 96})
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    import cv2, numpy as np
    im = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert im.shape[1] == 96
    s = client.get("/api/media/thumb", headers=H, params={"src": "source", "path": media["video"]})
    assert s.status_code == 200 and s.content[:4] == b"\x89PNG"
    lut = client.post("/api/project/asset", headers=H, json={"path": media["lut"]}).json()["path"]
    assert client.get("/api/media/thumb", headers=H, params={"src": "asset", "path": lut}).status_code == 404       # a LUT has no picture


def test_thumbnails_cannot_be_used_to_read_other_files(client, proj, media, tmp_path):
    other = tmp_path / "secret.png"; shutil.copy(media["bg"], other)
    assert client.get("/api/media/thumb", headers=H, params={"src": "source", "path": str(other)}).status_code == 404    # not a source of this project
    assert client.get("/api/media/thumb", headers=H, params={"src": "asset", "path": "../secret.png"}).status_code in (400, 404)
    assert client.get("/api/media/thumb", headers=H, params={"src": "asset", "path": str(other)}).status_code in (400, 404)


def test_remove_deletes_only_the_project_copy(client, proj, media, tmp_path):
    a = client.post("/api/project/asset", headers=H, json={"path": media["bg"]}).json()["path"]
    assert client.post("/api/project/asset/remove", headers=H, json={"path": a}).status_code == 200
    assert bin_(client)["assets"] == [] and __import__("os").path.isfile(media["bg"])         # the original is untouched
    assert client.post("/api/project/asset/remove", headers=H, json={"path": "../x.png"}).status_code in (400, 404)
    assert client.post("/api/project/asset/remove", headers=H, json={"path": media["video"]}).status_code in (400, 404)


def test_requires_token(client, proj):
    assert client.get("/api/project/media").status_code == 401
    assert client.get("/api/media/thumb", params={"src": "asset", "path": "assets/x.png"}).status_code == 401


def test_an_earlier_source_video_can_be_removed_from_the_bin_but_not_the_current_one(client, proj, media, tmp_path):
    other = tmp_path / "other.mp4"; shutil.copy(media["video"], other)
    client.post("/api/project/source", headers=H, json={"path": str(other)})           # the first video becomes an earlier source
    paths = [s["path"] for s in bin_(client)["sources"]]
    first = next(p for p in paths if not p.endswith("other.mp4"))
    assert len(paths) == 2
    cur = client.post("/api/project/source/forget", headers=H, json={"path": str(other)})
    assert cur.status_code == 409 and "editing now" in cur.text                          # the video being edited stays
    assert client.post("/api/project/source/forget", headers=H, json={"path": first}).status_code == 200
    assert [s["path"] for s in bin_(client)["sources"]] == [str(other.resolve())]
    import os
    assert os.path.isfile(first)                                                          # only the reference went, never the file
    assert client.post("/api/project/source/forget", headers=H, json={"path": first}).status_code == 404
