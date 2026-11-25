"""Auto-save and exact-state restoration: closing or crashing at any moment loses nothing, and reopening shows the same state."""
import json, os, time
import pytest
from fastapi.testclient import TestClient
from evp.project import Project, RecentProjects
from evp.server import create_app
from conftest import bp

H = {"x-evp-token": "tok"}
OPS = [{"id": "g", "type": "color_grading", "start": 0, "parameters": {"contrast": 1.3}}]
UI = {"t": 1.25, "tab": "effects", "mode": "edited", "zoom": 90, "sel": "g", "selClip": None, "layout": {"left": 400, "right": 300, "bottom": 200}}


def test_unsaved_work_is_restored_on_open_and_survives_a_second_crash(tmp_path):
    p = Project.create(tmp_path, "P")
    p.update(blueprint=bp(ops=OPS)); p.autosave()                       # the app was killed here: never saved
    assert json.loads(p.project_file.read_text())["blueprint"]["operations"] == []      # on disk the saved version is still empty
    q = Project.open(p.root, restore=True)
    assert q.data["blueprint"]["operations"] == OPS and q.restored_at is not None
    assert q.saved_blueprint["operations"] == []                         # what "last saved" means, for the Revert button
    assert q.autosave_file.is_file()                                     # kept: crash again before saving and it is still there
    r = Project.open(p.root, restore=True)
    assert r.data["blueprint"]["operations"] == OPS
    assert Project.open(p.root).data["blueprint"]["operations"] == []     # plain open (restore off) is unchanged behaviour


def test_save_clears_the_autosave_and_revert_goes_back(tmp_path):
    p = Project.create(tmp_path, "P")
    p.update(blueprint=bp(ops=OPS)); p.autosave()
    q = Project.open(p.root, restore=True)
    q.revert_to_saved()
    assert q.data["blueprint"]["operations"] == [] and not q.autosave_file.exists() and q.restored_at is None
    q.update(blueprint=bp(ops=OPS)); q.autosave(); q.save()
    assert not q.autosave_file.exists()
    s = Project.open(p.root, restore=True)
    assert s.data["blueprint"]["operations"] == OPS and s.restored_at is None      # saved for real: nothing to restore


def test_a_half_written_autosave_falls_back_to_the_saved_version(tmp_path):
    p = Project.create(tmp_path, "P")
    p.update(blueprint=bp(ops=OPS)); p.save()
    time.sleep(0.01)
    p.autosave_file.write_text('{"blueprint": {"operations": [', encoding="utf-8")     # power cut in the middle of a write
    q = Project.open(p.root, restore=True)
    assert q.data["blueprint"]["operations"] == OPS and q.restored_at is None


def test_view_state_roundtrip_is_independent_of_the_document(tmp_path):
    p = Project.create(tmp_path, "P")
    p.write_ui(UI)
    assert Project.open(p.root).data["ui"] == UI
    assert not p.autosave_file.exists()                                  # moving the playhead is not an "unsaved edit"


def test_recent_orders_by_activity_and_flags_unsaved_work(tmp_path):
    a, b = Project.create(tmp_path, "A"), Project.create(tmp_path, "B")
    r = RecentProjects(tmp_path / "recent.json")
    r.add(str(a.root), "A"); r.add(str(b.root), "B")
    assert [i["name"] for i in r.list()] == ["B", "A"] and not any(i["unsaved"] for i in r.list())
    time.sleep(0.02)
    a.update(blueprint=bp(ops=OPS)); a.autosave()                        # A was worked on last, without saving
    lst = r.list()
    assert lst[0]["name"] == "A" and lst[0]["unsaved"] and not lst[1]["unsaved"]
    assert all(i["modified"] > 0 for i in lst)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app("tok", str(tmp_path / "data")))


def test_full_session_over_the_api_kill_and_reopen(tmp_path, media):
    d = str(tmp_path / "data")
    c1 = TestClient(create_app("tok", d))
    pj = c1.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Work", "source": media["video"]}).json()
    # the video link is on disk from the first second (before any edit)
    assert json.loads((tmp_path / "Work.evproj" / "project.json").read_text())["source"]["path"]
    assert c1.put("/api/project/blueprint", headers=H, json={"blueprint": bp(ops=OPS), "ui": UI}).status_code == 200
    # the app is killed here: no save, no clean shutdown
    c2 = TestClient(create_app("tok", d))                                # a fresh engine process, same data folder
    rec = c2.get("/api/project/recent", headers=H).json()
    assert rec and rec[0]["name"] == "Work" and rec[0]["unsaved"] is True
    st = c2.post("/api/project/open", headers=H, json={"path": rec[0]["path"]}).json()
    assert st["restored"] is True and st["blueprint"]["operations"] == OPS and st["ui"] == UI
    assert st["saved_blueprint"]["operations"] == [] and st["video"] is not None
    # view-only change is saved without touching the document
    UI2 = {**UI, "t": 2.5}
    assert c2.put("/api/project/ui", headers=H, json=UI2).status_code == 200
    c3 = TestClient(create_app("tok", d))
    st3 = c3.post("/api/project/open", headers=H, json={"path": rec[0]["path"]}).json()
    assert st3["ui"]["t"] == 2.5 and st3["blueprint"]["operations"] == OPS
    # save, then revert is a no-op and nothing is flagged any more
    assert c3.post("/api/project/save", headers=H).status_code == 200
    assert c3.get("/api/project/recent", headers=H).json()[0]["unsaved"] is False
    st4 = TestClient(create_app("tok", d)).post("/api/project/open", headers=H, json={"path": rec[0]["path"]}).json()
    assert st4["restored"] is False and st4["blueprint"]["operations"] == OPS
