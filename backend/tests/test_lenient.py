"""Lenient intake: a blueprint with some bad parts still runs; every change is reported; security and strict mode are unchanged."""
import copy, json
import pytest
from evp.engine import prepare
from evp.security import PathPolicy
from conftest import bp


@pytest.fixture
def lp(media, policy):
    def _p(blueprint, **kw):
        return prepare(blueprint, media["video"], policy, media["dir"], strict=kw.pop("strict", False))
    return _p


def codes(p): return [w.code for w in p.report.warnings]


def test_user_blueprint_with_nested_groups_now_runs_and_is_translated(lp, media):
    theirs = {"blueprint_version": "1.1", "assets": [{"id": "ref", "type": "image", "path": "bg.png", "role": "target"}],
              "output": {"resolution": "source", "fps": "source", "codec": "h264", "format": "mp4", "crf": 17, "preset": "medium"},
              "operations": [{"id": "g", "type": "studio_look", "start": 0, "parameters": {
                  "preset": "clean_bright", "auto_match": 1.0, "reference_asset_id": "ref", "look_name": "x", "grade_intent": "y",
                  "tone": {"exposure": 0.25, "contrast": 16, "highlights": -28, "shadows": 20, "whites": 12, "blacks": -16},
                  "white_balance": {"temperature": 3, "tint": 2, "auto_neutralize_color_cast": True},
                  "color": {"saturation": 7, "vibrance": 14, "avoid_orange_skin": True},
                  "detail": {"texture": 10, "clarity": 9, "sharpening": 25, "noise_reduction": 8, "dehaze": 3},
                  "subject_adjustments": {"enabled": True, "face_exposure": 0.15, "skin_smoothing": 0},
                  "safety_limits": {"avoid_plastic_skin": True}}}],
              "project": {"name": "n", "notes": ["a", "b"]}}
    strict = prepare(copy.deepcopy(theirs), media["video"], PathPolicy([media["dir"]]), media["dir"], strict=True)
    assert not strict.report.ok                                              # strict mode is unchanged
    p = lp(theirs)
    assert p.report.ok and p.plan, [e.message for e in p.report.errors]
    assert "LENIENT_IMPORT" in codes(p) and "TRANSLATED" in codes(p)
    by = {o["id"]: o for o in p.repaired["operations"]}
    cg = by["g_grade"]["parameters"]
    assert cg["contrast"] == pytest.approx(1.16) and cg["highlights"] == pytest.approx(-0.28) and cg["saturation"] == pytest.approx(1.07)
    assert cg["vibrance"] == pytest.approx(0.14) and cg["texture"] == pytest.approx(0.10) and cg["exposure"] == 0.25
    assert by["g_detail"]["parameters"] == {"sharpen": pytest.approx(0.25), "denoise": pytest.approx(0.08)}
    assert by["g_face"]["type"] == "face_tone_correction" and by["g_face"]["parameters"]["exposure"] == 0.15
    assert by["g"]["parameters"]["reference_asset_id"] == "ref"             # real controls on the look survive
    assert "look_name" not in by["g"]["parameters"] and "tone" not in by["g"]["parameters"]


def test_bad_operation_is_skipped_good_one_runs(lp):
    p = lp(bp(ops=[{"id": "ok", "type": "color_grading", "start": 0, "parameters": {"contrast": 1.2}},
                   {"id": "bad", "type": "teleport", "start": 0, "parameters": {}},
                   {"id": "bad2", "type": "color_grading", "start": 99, "end": 100, "parameters": {}}]))
    assert p.report.ok and [o.id for o in p.report.blueprint.operations] == ["ok"]
    assert codes(p).count("SKIPPED_OPERATION") == 2


def test_out_of_range_clamped_unknown_dropped_enum_defaulted(lp):
    p = lp(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 50,
                    "parameters": {"contrast": 9, "preset": "no_such", "saturation": "1.2", "magic": 1, "lut_intensity": None}}]))
    assert p.report.ok
    q = p.report.blueprint.operations[0]
    assert q.params["contrast"] == 3.0 and q.params["saturation"] == 1.2 and q.params["preset"] == "none"
    assert q.end <= 3.01                                                      # end past the video was clamped
    assert {"CLAMPED", "DROPPED_PARAMETER"} <= set(codes(p))


def test_alias_names_and_missing_ids(lp):
    p = lp(bp(ops=[{"type": "color_correction", "start": 0, "parameters": {"temp": 10}}, {"id": "a b", "type": "sharpen", "parameters": {"sharpen": 0.5}}]))
    assert p.report.ok
    t = [(o.type, o.params.get("temperature"), o.params.get("sharpen")) for o in p.report.blueprint.operations]
    assert t[0][:2] == ("color_grading", 10) and t[1][0] == "video_enhancement"


def test_dependent_of_a_dropped_operation_follows(lp):
    ops = [{"id": "key", "type": "background_removal", "start": 0, "parameters": {"similarity": 99}},     # clamps, stays
           {"id": "bg", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "missing"}, "depends_on": ["key"]},
           {"id": "g", "type": "color_grading", "start": 0, "parameters": {"exposure": 0.2}}]
    p = lp(bp(ops=ops, assets=[{"id": "missing", "type": "image", "path": "nope.png"}]))
    assert p.report.ok
    ids = [o.id for o in p.report.blueprint.operations]
    assert "bg" not in ids and "g" in ids and "DROPPED_ASSET" in codes(p)


def test_unsafe_asset_path_is_still_refused(lp):
    p = lp(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {}}], assets=[{"id": "x", "type": "image", "path": "../../etc/passwd.png"}]))
    assert p.report.ok and "x" not in p.report.blueprint.assets and "DROPPED_ASSET" in codes(p)


def test_output_combo_fixed_bad_output_value_defaulted(lp):
    key = [{"id": "k", "type": "chroma_key", "start": 0, "parameters": {}}]
    p = lp(bp(ops=key, output={"resolution": "source", "fps": "source", "codec": "prores4444", "format": "mp4", "crf": 99, "preset": "turbo"}))
    assert p.report.ok, [e.message for e in p.report.errors]
    o = p.report.blueprint.output
    assert o.codec == "prores4444" and o.format == "mov" and o.crf == 51 and o.preset == "medium"
    q = lp(bp(ops=[], output={"resolution": "source", "fps": "source", "codec": "prores4444", "format": "mov"}))     # transparent but nothing to key
    assert q.report.ok and q.report.blueprint.output.codec == "h264" and "AUTO_FIXED" in codes(q)


def test_bad_keyframes_dropped_rest_kept(lp):
    p = lp(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {},
                    "keyframes": {"exposure": [{"time": 0, "value": 0}, {"time": 1, "value": 0.5}], "denoise": [{"time": 0, "value": 1}]}}]))
    assert p.report.ok
    assert set(p.report.blueprint.operations[0].keyframes) == {"exposure"} and "DROPPED_KEYFRAMES" in codes(p)


def test_unreadable_files_stay_fatal(lp):
    assert not lp("{not json").report.ok
    assert not lp([1, 2]).report.ok


def test_clean_blueprint_gets_no_notes_and_strict_flag_respected(lp):
    good = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {"contrast": 1.1}}])
    p = lp(good)
    assert p.report.ok and "LENIENT_IMPORT" not in codes(p)
    bad = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {"contrast": 9}}]); bad["strict"] = True
    assert not lp(bad).report.ok                                              # a blueprint can demand strict checking
