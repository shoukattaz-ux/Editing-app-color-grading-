"""Ready-made looks must all be valid blueprints on a real video, and the list must be served."""
import pytest
from fastapi.testclient import TestClient
from conftest import bp
from evp.templates import TEMPLATES, SECTIONS
from evp.server import create_app


def test_ids_are_unique_and_sections_known():
    ids = [t["id"] for t in TEMPLATES]
    assert len(ids) == len(set(ids)) and all(t["section"] in SECTIONS for t in TEMPLATES)
    assert {t["section"] for t in TEMPLATES} == set(SECTIONS)                      # every section has at least one ready-made look


@pytest.mark.parametrize("t", TEMPLATES, ids=lambda t: t["id"])
def test_every_template_validates(t, prep, media):
    b = dict(t["blueprint"])
    b = bp(ops=[dict(o) for o in b["operations"]], assets=[dict(a, path=media["bg"]) for a in b["assets"]])      # the editor puts the photo itself in place of "@photo"
    r = prep(b)
    assert r.report.ok, [i.message for i in r.report.errors]
    assert len(r.plan.steps) == len(b["operations"]), [i.message for i in r.report.warnings]      # nothing may be silently skipped
    assert not [w for w in r.report.warnings if w.code == "SKIPPED_OPERATION"]


def test_served_over_the_api(tmp_path):
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    j = c.get("/api/templates", headers={"x-evp-token": "tok"}).json()
    assert len(j["templates"]) == len(TEMPLATES) and j["sections"] == list(SECTIONS)


def test_looks_that_need_the_photo_say_so_and_name_their_strength_ops():
    t = {x["id"]: x for x in TEMPLATES}["fix_overexposed"]
    assert t["uses_source"] and t["blueprint"]["assets"][0]["path"] == "@photo" and t["strength_ops"] == ["wall", "me"]
    ids = {o["id"] for o in t["blueprint"]["operations"]}
    assert set(t["strength_ops"]) <= ids
    assert all(not x["uses_source"] for x in TEMPLATES if x["id"] != "fix_overexposed" and not x["blueprint"]["assets"])
