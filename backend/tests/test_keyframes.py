"""Keyframe maths: interpolation, easing, FFmpeg expression equivalence, splitting; shared vectors with the TypeScript editor."""
import json, subprocess
from pathlib import Path
import numpy as np, pytest
from evp.keyframes import Keyframe, EASINGS, value_at, to_expr, split_at, ease
from evp.media import ffmpeg_bin

VEC = Path(__file__).resolve().parents[2] / "shared" / "keyframe_vectors.json"
K = lambda *a: [Keyframe(*x) for x in a]


def test_shared_vectors_are_current_and_match():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import gen_keyframe_vectors as g
    assert json.loads(VEC.read_text()) == g.build(), "run backend/scripts/gen_keyframe_vectors.py"
    for c in json.loads(VEC.read_text())["cases"]:
        kfs = [Keyframe(**k) for k in c["keyframes"]]
        for s in c["samples"]:
            assert value_at(kfs, s["t"]) == pytest.approx(s["v"], abs=1e-9), (c["name"], s)


def test_hold_outside_and_endpoints():
    k = K((1, 2.0, "linear"), (3, 6.0, "linear"))
    assert value_at(k, 0) == 2.0 and value_at(k, 1) == 2.0 and value_at(k, 3) == 6.0 and value_at(k, 99) == 6.0
    assert value_at(k, 2) == pytest.approx(4.0)


@pytest.mark.parametrize("name", EASINGS)
def test_easing_endpoints_and_monotonic(name):
    assert ease(name, 0.0) == 0.0 and ease(name, 1.0) == (0.0 if name == "hold" else 1.0)
    us = np.linspace(0, 1, 101)
    vals = [ease(name, float(u)) for u in us]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))


def test_easing_shapes():
    assert ease("ease_in", 0.5) < 0.5 < ease("ease_out", 0.5)
    assert ease("ease_in_out", 0.5) == pytest.approx(0.5) and ease("ease_in_out", 0.1) < 0.1 and ease("ease_in_out", 0.9) > 0.9


def test_hold_keeps_value_until_next_keyframe():
    k = K((0, 1.0, "hold"), (2, 5.0, "linear"), (4, 9.0, "linear"))
    assert value_at(k, 1.999) == 1.0 and value_at(k, 2.0) == 5.0 and value_at(k, 3) == pytest.approx(7.0)


def test_unsorted_or_duplicate_times_are_the_callers_problem_but_search_is_correct_for_sorted_input():
    k = K(*[(i * 0.5, float(i), "linear") for i in range(40)])
    for i in range(39):
        assert value_at(k, i * 0.5 + 0.25) == pytest.approx(i + 0.5)


# ---------------------------------------------------------------- expression == value_at (verified by FFmpeg's own evaluator)
def ffmpeg_values(expr: str, seconds: float, rate: int) -> np.ndarray:
    out = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s={rate}:d={seconds}",
                          "-f", "f32le", "-ac", "1", "-"], capture_output=True)
    assert out.returncode == 0, out.stderr.decode()
    return np.frombuffer(out.stdout, np.float32)


@pytest.mark.parametrize("t0", [0.0, 1.5])
def test_expression_equals_python_for_every_shared_case(t0):
    for c in json.loads(VEC.read_text())["cases"]:
        kfs = [Keyframe(**k) for k in c["keyframes"]]
        expr = to_expr(kfs, t0)
        rate = 40
        v = ffmpeg_values(expr, 8, rate)
        expect = np.array([value_at(kfs, i / rate - t0) for i in range(len(v))])
        assert np.abs(v - expect).max() < 2e-5, (c["name"], t0, np.abs(v - expect).max())


def test_expression_is_compact_and_safe():
    k = K(*[(i * 0.1, float(i % 3), ["linear", "ease_in_out"][i % 2]) for i in range(64)])
    e = to_expr(k, 2.0)
    bad = ["'", "\\", ";", "[", "]", '"']
    assert len(e) < 20000 and not any(c in e for c in bad) and e.count("(") == e.count(")")
    assert to_expr(K((0, 3.5)), 9.0) == "3.5"


# ---------------------------------------------------------------- split
def test_split_halves_render_exactly_like_the_original_for_any_easing():
    k = K((0, 0.0, "ease_in_out"), (2, 10.0, "linear"), (5, 4.0, "ease_out"), (6, 4.0, "linear"), (7, 1.0, "linear"))
    length = 6.0
    for t in (0.7, 1.0, 2.0, 3.3, 5.5):
        left, right = split_at(k, t, length)
        for s in np.linspace(0, t, 40):
            assert value_at(left, s) == pytest.approx(value_at(k, s), abs=1e-12), ("left", t, s)
        for s in np.linspace(0, length - t, 40):
            assert value_at(right, s) == pytest.approx(value_at(k, s + t), abs=1e-12), ("right", t, s)
        assert [x.time for x in right] == sorted(x.time for x in right)


def test_split_prunes_keyframes_that_cannot_matter():
    k = K(*[(float(i), float(i * i), "linear") for i in range(10)])
    left, right = split_at(k, 4.5, 9.0)
    assert [x.time for x in left] == [0, 1, 2, 3, 4, 5]                     # up to the first keyframe after the split
    assert [x.time for x in right] == pytest.approx([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5])   # one bracketing key before the start
    one = K((1.0, 3.0))
    assert split_at(one, 0.5, 2.0) == ([Keyframe(1.0, 3.0)], [Keyframe(0.5, 3.0)])
