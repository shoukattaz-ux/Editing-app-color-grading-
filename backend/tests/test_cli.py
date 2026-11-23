import json, shutil
from pathlib import Path
from evp.cli import main
from evp.media import probe

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_shipped_sample_blueprint_validates_and_renders(tmp_path, capsys):
    assert main(["sample", str(tmp_path)]) == 0
    shutil.copy(SAMPLES / "space_presenter.blueprint.json", tmp_path / "bp.json")
    vid = str(tmp_path / "greenscreen.mp4")
    assert main(["validate", "--video", vid, "--blueprint", str(tmp_path / "bp.json")]) == 0
    out = tmp_path / "out.mp4"
    assert main(["run", "--video", vid, "--blueprint", str(tmp_path / "bp.json"), "--out", str(out)]) == 0
    assert abs(probe(out).duration - 6) < 0.2


def test_cli_invalid_blueprint_exit_code(tmp_path, capsys):
    main(["sample", str(tmp_path)])
    (tmp_path / "bad.json").write_text(json.dumps({"blueprint_version": "1.0", "operations": [{"id": "a", "type": "nope", "start": 0, "end": 1}]}))
    assert main(["validate", "--video", str(tmp_path / "greenscreen.mp4"), "--blueprint", str(tmp_path / "bad.json")]) == 2
    assert "UNKNOWN_OPERATION" in capsys.readouterr().out


def test_cli_refuses_to_overwrite_source(tmp_path):
    import pytest
    from evp.security import PathError
    main(["sample", str(tmp_path)])
    shutil.copy(SAMPLES / "space_presenter.blueprint.json", tmp_path / "bp.json")
    v = str(tmp_path / "greenscreen.mp4")
    with pytest.raises(PathError):
        main(["run", "--video", v, "--blueprint", str(tmp_path / "bp.json"), "--out", v])
