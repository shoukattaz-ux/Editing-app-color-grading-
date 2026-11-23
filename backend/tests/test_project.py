import json, os, time
from pathlib import Path
import pytest
from evp.project import Project, ProjectError, RecentProjects
from conftest import bp


def test_create_save_reopen_roundtrip(tmp_path, media):
    p = Project.create(tmp_path, "My Film", media["video"])
    b = bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1, "parameters": {"contrast": 1.2}}])
    p.update(blueprint=b, ui={"zoom": 3}); p.save()
    q = Project.open(p.root)
    assert q.data["blueprint"] == b and q.data["ui"] == {"zoom": 3} and q.data["source"]["path"] == media["video"]
    assert {d.name for d in p.root.iterdir()} >= {"project.json", "assets", "exports", "cache", "versions"}


def test_original_video_untouched_by_project_ops(tmp_path, media):
    before = Path(media["video"]).stat().st_mtime_ns
    p = Project.create(tmp_path, "x", media["video"]); p.save(); p.autosave(); p.save_as(tmp_path, "y")
    assert Path(media["video"]).stat().st_mtime_ns == before


def test_versions_and_restore(tmp_path):
    p = Project.create(tmp_path, "v")
    for i in range(3):
        p.update(blueprint=bp(ops=[{"id": f"o{j}", "type": "color_grading", "start": 0, "end": 1} for j in range(i)])); p.save()
    vs = p.versions(); assert len(vs) == 3
    oldest = vs[-1]["file"]
    p.restore_version(oldest)
    assert len(p.data["blueprint"]["operations"]) == 0
    with pytest.raises(ProjectError): p.restore_version("../../etc/passwd")


def test_crash_recovery_via_autosave(tmp_path):
    p = Project.create(tmp_path, "crash")
    p.update(blueprint=bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1}])); p.save()
    time.sleep(0.02)
    p.update(blueprint=bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1}, {"id": "b", "type": "color_grading", "start": 0, "end": 1}]))
    p.autosave()                                   # app dies here, before save
    q = Project.open(p.root)
    assert q.recovery_available() and len(q.data["blueprint"]["operations"]) == 1
    q.recover()
    assert len(q.data["blueprint"]["operations"]) == 2 and not q.autosave_file.exists()


def test_save_clears_autosave_and_is_atomic(tmp_path):
    p = Project.create(tmp_path, "a"); p.autosave(); p.save()
    assert not p.autosave_file.exists() and not list(p.root.glob("*.tmp"))


def test_corrupt_project_file_message(tmp_path):
    p = Project.create(tmp_path, "c"); p.project_file.write_text("{broken")
    with pytest.raises(ProjectError, match="corrupted"): Project.open(p.root)
    with pytest.raises(ProjectError): Project.open(tmp_path / "nope")


def test_missing_asset_and_source_detection_and_relink(tmp_path, media):
    src = tmp_path / "s.mp4"; src.write_bytes(Path(media["video"]).read_bytes())
    p = Project.create(tmp_path, "m", str(src))
    p.data["blueprint"]["assets"] = [{"id": "bg", "type": "image", "path": "assets/bg.png"}]
    assert p.missing()["assets"][0]["id"] == "bg" and p.missing()["source"] is None
    src.unlink()
    assert p.missing()["source"]
    p.relink("asset", media["bg"], "bg")
    assert p.missing()["assets"] == [] and p.data["blueprint"]["assets"][0]["path"].startswith("assets/")
    p.relink("source", media["video"]); assert p.missing()["source"] is None
    with pytest.raises(ProjectError): p.relink("asset", media["bg"], "ghost")


def test_import_asset_no_overwrite_and_safe_name(tmp_path, media):
    p = Project.create(tmp_path, "i")
    a, b = p.import_asset(media["bg"]), p.import_asset(media["bg"])
    assert a != b and (p.root / a).is_file() and (p.root / b).is_file()


def test_project_name_sanitised(tmp_path):
    p = Project.create(tmp_path, "../../evil/name")
    assert p.root.parent == tmp_path.resolve()


def test_save_as_and_recent(tmp_path):
    p = Project.create(tmp_path, "orig"); p.update(blueprint=bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1}])); p.save()
    q = p.save_as(tmp_path, "copy")
    assert q.root != p.root and q.data["blueprint"] == p.data["blueprint"]
    r = RecentProjects(tmp_path / "cfg" / "recent.json"); r.add(str(p.root), "orig"); r.add(str(q.root), "copy")
    assert [i["name"] for i in r.list()] == ["copy", "orig"]
