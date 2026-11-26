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
def test_every_template_validates(t, prep):
    b = dict(t["blueprint"]); b = bp(ops=[dict(o) for o in b["operations"]])
    r = prep(b)
    assert r.report.ok, [i.message for i in r.report.errors]
    assert len(r.plan.steps) == len(b["operations"]), [i.message for i in r.report.warnings]      # nothing may be silently skipped
    assert not [w for w in r.report.warnings if w.code == "SKIPPED_OPERATION"]


def test_served_over_the_api(tmp_path):
    c = TestClient(create_app("tok", str(tmp_path / "d")))
    j = c.get("/api/templates", headers={"x-evp-token": "tok"}).json()
    assert len(j["templates"]) == len(TEMPLATES) and j["sections"] == list(SECTIONS)
