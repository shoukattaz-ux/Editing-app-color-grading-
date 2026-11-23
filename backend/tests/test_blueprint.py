import json, copy
import pytest
from conftest import bp
from evp.blueprint import parse_time, apply_params, normalize_color


def codes(p, level="errors"):
    return [i.code for i in getattr(p.report, level)]


def test_valid_example_runs_and_orders(prep):
    p = prep(bp(ops=[
        {"id": "operation_001", "type": "background_removal", "start": 0, "end": 3, "parameters": {"quality": "high", "edge_refinement": True, "spill_suppression": True}},
        {"id": "operation_002", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg", "fit": "cover"}, "depends_on": ["operation_001"]},
        {"id": "operation_003", "type": "color_grading", "start": 0, "end": 3, "parameters": {"temperature": -5, "contrast": 1.12, "saturation": 1.08}}],
        assets=[{"id": "bg", "type": "image", "path": "bg.png"}]))
    assert p.report.ok, p.report.errors
    order = [o.id for o in p.plan.steps]
    assert order.index("operation_001") < order.index("operation_002") < order.index("operation_003")
    assert p.plan.alpha_source["operation_002"] == "operation_001"


@pytest.mark.parametrize("text,code", [("{nope", "INVALID_JSON"), ("[]", "INVALID_STRUCTURE"),
                                       (json.dumps({"blueprint_version": "9.9", "operations": []}), "UNSUPPORTED_VERSION"),
                                       (json.dumps({"blueprint_version": "1.0", "operations": "x"}), "INVALID_STRUCTURE")])
def test_structure_errors(prep, text, code):
    assert code in codes(prep(text))


def test_unknown_and_unsupported_ops_are_errors_not_skipped(prep):
    p = prep(bp(ops=[{"id": "a", "type": "teleport", "start": 0, "end": 1},
                     {"id": "b", "type": "face_relighting", "start": 0, "end": 1},
                     {"id": "c", "type": "hair_edge_refinement", "start": 0, "end": 1}]))
    assert codes(p).count("UNKNOWN_OPERATION") == 1
    assert codes(p).count("UNSUPPORTED_OPERATION") == 2
    assert not p.report.ok


@pytest.mark.parametrize("start,end,code", [(-1, 2, "INVALID_TIMESTAMP"), (2, 1, "INVALID_TIMESTAMP"), ("abc", 2, "INVALID_TIMESTAMP"),
                                            (0, 99, "TIME_OUT_OF_RANGE"), (5, 6, "TIME_OUT_OF_RANGE"), (True, 2, "INVALID_TIMESTAMP")])
def test_timestamp_errors(prep, start, end, code):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": start, "end": end}]))
    assert code in codes(p)


def test_timestamp_formats_and_frame_snapping(prep):
    assert parse_time("00:01:02.500") == 62.5 and parse_time("1:02") == 62 and parse_time("3.25") == 3.25
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": "0:00:00.510", "end": 1.0}]))
    op = p.report.blueprint.operations[0]
    assert op.start_frame == 15 and abs(op.start - 0.5) < 1e-9
    assert "TIME_SNAPPED" in codes(p, "warnings")


def test_end_defaults_to_video_end(prep):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": 1}]))
    assert p.report.blueprint.operations[0].end == 3.0


@pytest.mark.parametrize("params", [{"contrast": 99}, {"saturation": "x"}, {"bogus": 1}, {"curve_master": [[0.5, 0.5], [0.2, 0.2]]},
                                    {"lift": [2, 0, 0]}, {"preset": "nope"}, {"lut_asset_id": "missing"}, {"exposure": float("nan")}])
def test_invalid_parameters(prep, params):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1, "parameters": params}]))
    assert not p.report.ok


def test_defaults_filled(prep):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1}]))
    assert p.report.blueprint.operations[0].params["contrast"] == 1.0


def test_duplicate_ids_and_bad_ids(prep):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1}, {"id": "a", "type": "color_grading", "start": 0, "end": 1},
                     {"id": "x; rm -rf /", "type": "color_grading", "start": 0, "end": 1}]))
    assert "DUPLICATE_ID" in codes(p) and "INVALID_ID" in codes(p)


def test_dependencies_cycle_unknown_and_range(prep):
    ops = [{"id": "a", "type": "chroma_key", "start": 0, "end": 1, "depends_on": ["b"]},
           {"id": "b", "type": "color_grading", "start": 0, "end": 1, "depends_on": ["a"]},
           {"id": "c", "type": "color_grading", "start": 0, "end": 1, "depends_on": ["ghost"]}]
    c = codes(prep(bp(ops=ops)))
    assert "DEPENDENCY_CYCLE" in c and "UNKNOWN_DEPENDENCY" in c
    p = prep(bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 1},
                     {"id": "r", "type": "background_replacement", "start": 0, "end": 2, "depends_on": ["k"]}]))
    assert "DEPENDENCY_RANGE" in codes(p)


def test_replacement_without_alpha_source(prep):
    p = prep(bp(ops=[{"id": "r", "type": "background_replacement", "start": 0, "end": 2}]))
    assert "MISSING_ALPHA_SOURCE" in codes(p)


def test_disabled_key_breaks_replacement_dependency(prep):
    p = prep(bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 3, "enabled": False},
                     {"id": "r", "type": "background_replacement", "start": 0, "end": 2, "depends_on": ["k"]}]))
    assert "MISSING_ALPHA_SOURCE" in codes(p)


def test_overlapping_replacements_conflict_and_stacking_warnings(prep):
    ops = [{"id": "k", "type": "chroma_key", "start": 0, "end": 3},
           {"id": "r1", "type": "background_replacement", "start": 0, "end": 2},
           {"id": "r2", "type": "background_replacement", "start": 1, "end": 3},
           {"id": "g1", "type": "color_grading", "start": 0, "end": 2}, {"id": "g2", "type": "color_grading", "start": 1, "end": 3}]
    p = prep(bp(ops=ops))
    assert "OVERLAP_CONFLICT" in codes(p)
    ok = prep(bp(ops=ops[:1] + ops[3:]))
    assert ok.report.ok and "OVERLAP_STACKED" in codes(ok, "warnings")


def test_adjacent_replacements_allowed(prep):
    p = prep(bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 3},
                     {"id": "r1", "type": "background_replacement", "start": 0, "end": 1.5},
                     {"id": "r2", "type": "background_replacement", "start": 1.5, "end": 3}]))
    assert p.report.ok, p.report.errors


def test_assets_missing_unknown_type_and_traversal(prep):
    assets = [{"id": "m", "type": "image", "path": "nope.png"}, {"id": "t", "type": "image", "path": "../../etc/passwd.png"},
              {"id": "abs", "type": "image", "path": "/etc/passwd.png"}, {"id": "bad", "type": "image", "path": "x.exe"},
              {"id": "v", "type": "video", "path": "bg.png"}]
    c = codes(prep(bp(assets=assets)))
    assert c.count("MISSING_ASSET") == 1 and c.count("UNSAFE_PATH") == 2 and c.count("INVALID_ASSET_EXT") == 2


def test_asset_type_mismatch_and_unknown_asset(prep):
    p = prep(bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 3},
                     {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "lut"}},
                     {"id": "g", "type": "color_grading", "start": 0, "end": 1, "parameters": {"lut_asset_id": "ghost"}}],
                  assets=[{"id": "lut", "type": "lut", "path": "warm.cube"}]))
    assert "ASSET_TYPE_MISMATCH" in codes(p) and "UNKNOWN_ASSET" in codes(p)


def test_output_validation(prep):
    p = prep(bp(output={"resolution": "1921x1080", "fps": 500, "format": "avi", "crf": 99}))
    assert codes(p).count("INVALID_PARAMETER") == 4


def test_unknown_fields_warn(prep):
    p = prep(bp(ops=[{"id": "a", "type": "color_grading", "start": 0, "end": 1, "shell": "rm -rf /"}], extra=1))
    assert "UNKNOWN_FIELD" in codes(p, "warnings") and p.report.ok


def test_no_audio_warning(prep, media):
    p = prep(bp(ops=[{"id": "a", "type": "audio_gain", "start": 0, "end": 1}]), media["silent"])
    assert "NO_AUDIO" in codes(p, "warnings")


def test_bad_source_reported(prep, media):
    p = prep(bp(), str(media["dir"] / "none.mp4"))
    assert "BAD_SOURCE" in codes(p)


def test_color_normalisation():
    assert normalize_color("green") == "#00ff00" and normalize_color("FF00aa") == "#ff00aa"
    with pytest.raises(ValueError): normalize_color("rgb(1,2,3)")


def test_docs_are_up_to_date():
    import json, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import gen_docs
    docs = Path(__file__).resolve().parents[2] / "docs"
    assert json.loads((docs / "blueprint.schema.json").read_text()) == gen_docs.json_schema_doc(), "run backend/scripts/gen_docs.py"
    assert (docs / "OPERATIONS.md").read_text() == gen_docs.ops_md() + "\n"
