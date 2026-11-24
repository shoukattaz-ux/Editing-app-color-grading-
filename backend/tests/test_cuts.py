"""Cutting / trimming: clips (edit decision list) must be frame-exact in video AND audio, and everything
downstream (operation timing, AI frame decoding, previews) must use the cut timeline."""
import numpy as np, cv2, pytest
from conftest import bp, frame_ids, audio_freqs, export_ids, make_ramp
from evp.engine import prepare, render_frame
from evp.security import PathPolicy


def clips(*rng):
    return [{"id": f"c{i}", "start": a, "end": b} for i, (a, b) in enumerate(rng)]


@pytest.fixture
def prep_r(ramp):
    def _p(blueprint, video=None):
        return prepare(blueprint, video or ramp["video"], PathPolicy([ramp["dir"]]), ramp["dir"])
    return _p


# ---------------------------------------------------------------- validation
def test_clip_validation(prep_r):
    ok = prep_r(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 2}]) | {"clips": clips((0.4, 1.4), (2.0, 3.0))})
    assert ok.report.ok, ok.report.errors
    b = ok.report.blueprint
    assert b.timeline_duration == pytest.approx(2.0) and ok.plan.total_frames == 50 and ok.plan.edl == [(10, 35), (50, 75)]
    t = ok.to_json()["timeline"]
    assert t["cut"] and t["duration"] == pytest.approx(2.0) and t["clips"][1]["timeline_start"] == pytest.approx(1.0)

    def codes(c, ops=None):
        return [e.code for e in prep_r(bp(ops=ops or []) | {"clips": c}).report.errors]
    assert "INVALID_TIMESTAMP" in codes(clips((2, 1)))
    assert "TIME_OUT_OF_RANGE" in codes(clips((0, 9)))
    assert "TIME_OUT_OF_RANGE" in codes(clips((5, 6)))
    assert "DUPLICATE_ID" in codes([{"id": "a", "start": 0, "end": 1}, {"id": "a", "start": 1, "end": 2}])
    assert "EMPTY_TIMELINE" in codes([])
    assert "INVALID_STRUCTURE" in codes("nope")
    assert "TOO_MANY_REORDERED_CLIPS" in codes([{"id": f"c{i}", "start": (98 - 2 * i) / 25, "end": (99 - 2 * i) / 25} for i in range(49)])


def test_operations_are_validated_against_the_cut_timeline(prep_r):
    cut = {"clips": clips((0, 1), (2, 3))}                                                      # 2 s timeline
    assert prep_r(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 2}]) | cut).report.ok
    e = prep_r(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 3}]) | cut).report.errors
    assert e and e[0].code == "TIME_OUT_OF_RANGE" and "cut timeline" in e[0].message
    assert prep_r(bp(ops=[{"id": "g", "type": "color_grading", "start": 0}]) | cut).report.blueprint.operations[0].end == pytest.approx(2.0)


def test_single_clip_covering_everything_is_not_a_cut(prep_r):
    p = prep_r(bp() | {"clips": clips((0, 4))})
    assert p.report.ok and p.plan.edl is None and p.report.blueprint.clips == []


def test_uncut_blueprint_unchanged(prep_r):
    p = prep_r(bp())
    assert p.plan.edl is None and p.plan.total_frames == 100 and p.to_json()["timeline"]["cut"] is False


# ---------------------------------------------------------------- exact video
def test_in_order_cuts_are_frame_exact(prep_r, ramp):
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((0.4, 1.4), (2.0, 3.0))})
    assert ids == list(range(10, 35)) + list(range(50, 75))


def test_cut_starting_at_zero_and_ending_at_the_end(prep_r, ramp):
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((0, 0.2), (3.8, 4))}, name="edge.mp4")
    assert ids == list(range(0, 5)) + list(range(95, 100))


def test_split_without_removing_changes_nothing(prep_r, ramp):
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((0, 1.2), (1.2, 4))}, name="split.mp4")
    assert ids == list(range(100))


def test_reordered_and_duplicated_clips(prep_r, ramp):
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((2.0, 3.0), (0.0, 0.4), (2.0, 2.2))}, name="reorder.mp4")
    assert ids == list(range(50, 75)) + list(range(0, 10)) + list(range(50, 55))


def test_overlapping_clips(prep_r, ramp):
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((0.4, 1.0), (0.8, 1.2))}, name="overlap.mp4")
    assert ids == list(range(10, 25)) + list(range(20, 30))


@pytest.mark.parametrize("key", ["video", "ntsc"])
def test_cuts_on_ntsc_rate_source(prep_r, ramp, key):
    src = ramp[key]
    ids, _ = export_ids(prep_r, ramp, bp() | {"clips": clips((0.5, 1.0), (2.0, 2.5))}, src=src, name=f"out_{key}.mp4")
    fps = 25 if key == "video" else 29.97
    a1, b1, a2, b2 = (round(x * fps) for x in (0.5, 1.0, 2.0, 2.5))
    assert ids == list(range(a1, b1)) + list(range(a2, b2))


def test_output_fps_differs_from_source(prep_r, ramp):
    """clips snap to the OUTPUT frame grid; 25 fps source rendered at 50 fps duplicates frames."""
    ids, out = export_ids(prep_r, ramp, bp(output={"resolution": "source", "fps": 50}) | {"clips": clips((1.0, 1.5))}, src=ramp["silent"], name="fps50.mp4")
    assert len(ids) == 25 and ids[0] in (25, 24) and ids[-1] <= 38        # 0.5 s at 50 fps = 25 frames of source frames 25..37


# ---------------------------------------------------------------- audio
def test_audio_follows_the_cuts_in_content_and_length(prep_r, ramp):
    _, out = export_ids(prep_r, ramp, bp() | {"clips": clips((2.0, 3.0), (0.0, 1.0))}, name="audio.mp4")
    freqs, dur = audio_freqs(out)
    assert abs(dur - 2.0) < 0.06
    assert freqs[0:2] == [400, 400] and freqs[2:4] == [200, 200], freqs     # source second 2 (400 Hz) then second 0 (200 Hz)


def test_in_order_cut_audio_and_av_sync(prep_r, ramp):
    _, out = export_ids(prep_r, ramp, bp() | {"clips": clips((0.0, 1.0), (3.0, 4.0))}, name="audio2.mp4")
    freqs, dur = audio_freqs(out)
    assert freqs == [200, 200, 500, 500] and abs(dur - 2.0) < 0.06
    import json, subprocess
    from evp.media import ffprobe_bin
    s = json.loads(subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"]
    d = {x["codec_type"]: float(x["duration"]) for x in s}
    assert abs(d["video"] - d["audio"]) < 0.06


def test_audio_gain_operation_uses_timeline_time(prep_r, ramp):
    from conftest import audio_freqs as af
    import subprocess, numpy as np
    from evp.media import ffmpeg_bin
    _, out = export_ids(prep_r, ramp, bp(ops=[{"id": "a", "type": "audio_gain", "start": 1.0, "end": 2.0, "parameters": {"gain_db": -60}}]) | {"clips": clips((0, 1), (2, 3))}, name="gain.mp4")
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(out), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    a = np.abs(np.frombuffer(raw, np.int16).astype(float))
    first, second = a[: 14000].mean(), a[18000:30000].mean()
    assert first > 1000 and second < 50                          # silent exactly in timeline [1,2) -> the second clip


def test_continuous_split_has_no_audible_dip(prep_r, ramp):
    _, whole = export_ids(prep_r, ramp, bp(), name="w.mp4")
    _, split = export_ids(prep_r, ramp, bp() | {"clips": clips((0, 1.2), (1.2, 4))}, name="s.mp4")
    import subprocess, numpy as np
    from evp.media import ffmpeg_bin
    rd = lambda p: np.frombuffer(subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(p), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout, np.int16).astype(float)
    a, b = rd(whole), rd(split)
    n = min(len(a), len(b))
    seam = slice(int(1.2 * 16000) - 400, int(1.2 * 16000) + 400)
    assert np.abs(b[seam]).max() > 0.7 * np.abs(a[seam]).max()


# ---------------------------------------------------------------- operations and previews on the cut timeline
def test_operation_timing_is_on_the_cut_timeline(prep_r, ramp):
    """grade active on timeline [1.0, 1.4): in a timeline made of source 0.4-1.0 + 2.0-3.0 that is source frames 50..59."""
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 1.0, "end": 1.4, "parameters": {"brightness": 0.3}}]) | {"clips": clips((0.4, 1.0), (2.0, 3.0))}
    p = prep_r(b)
    assert p.report.ok
    lum = []
    for n in (14, 15, 24, 25, 34, 35):                          # timeline frames around the op's edges (25 .. 35)
        png = render_frame(p.plan, ramp["video"], n / 25, None, str(ramp["dir"]))
        lum.append(cv2.imdecode(np.frombuffer(png, np.uint8), 0).mean())
    # frames 14,15 (before op) and 35 (after) unchanged vs the ramp; 25..34 brightened
    base = lambda src: (16 + 2 * src - 16) * 255 / 219
    for n, v in zip((14, 15, 24, 25, 34, 35), lum):
        src = 10 + n if n < 15 else 50 + (n - 15)
        inside = 25 <= n < 35
        assert (v > base(src) + 30) == inside, (n, v, base(src))


@pytest.mark.parametrize("first,count", [(0, 4), (12, 6), (14, 3), (3, 20)])
def test_ai_decode_uses_the_same_cuts(ramp, first, count):
    from evp.ml.frames import decode
    edl = [(50, 75), (0, 10), (50, 55)]                          # includes reorder + duplicate
    from evp import edl as E
    expect = [E.source_frame(edl, first + i) for i in range(count)]
    got = [int(round((f.astype(float).mean() - 16) / 2 * 255 / 219 + 0)) for f in decode(ramp["silent"], 64, 48, 25.0, first, count, edl)]
    # RGB value of a grey with Y=16+2n is (2n)*255/219; invert
    got = [int(round(f.astype(float).mean() * 219 / 255 / 2)) for f in decode(ramp["silent"], 64, 48, 25.0, first, count, edl)]
    assert got == expect


@pytest.mark.parametrize("key,fps", [("silent", 25.0), ("ntsc", 29.97)])        # 29.97 = the rate the engine derives from probe()
@pytest.mark.parametrize("first", [0, 1, 17, 33, 61])
def test_ai_decode_is_exact_without_cuts_too(ramp, key, fps, first):
    """regression for seeking: decoding from frame N must start exactly at N (any rate)."""
    from evp.ml.frames import decode
    got = [int(round(f.astype(float).mean() * 219 / 255 / 2)) for f in decode(ramp[key], 64, 48, fps, first, 3)]
    assert got == [first, first + 1, first + 2]


def test_preview_frame_on_cut_timeline_is_exact(prep_r, ramp):
    p = prep_r(bp() | {"clips": clips((2.0, 3.0), (0.0, 0.4))})        # reordered: timeline 0..24 = src 50..74, 25..34 = src 0..9
    for tf, src in ((0, 50), (24, 74), (25, 0), (34, 9), (12, 62)):
        im = cv2.imdecode(np.frombuffer(render_frame(p.plan, ramp["video"], tf / 25, None, str(ramp["dir"])), np.uint8), 0).astype(float)
        assert int(round(im.mean() * 219 / 255 / 2)) == src, (tf, src)


def test_chroma_key_plus_cuts_end_to_end(tmp_path, media, policy):
    """real effect pipeline on a cut timeline: background replaced only inside its (timeline) range."""
    from evp.engine import Job
    from evp.compiler import RenderOptions
    b = bp(ops=[{"id": "k", "type": "chroma_key", "start": 0, "end": 2, "parameters": {"background_color": "#ff0000"}}],
           ) | {"clips": clips((0, 1), (2, 3))}
    p = prepare(b, media["video"], policy, media["dir"])
    assert p.report.ok, p.report.errors
    out = tmp_path / "k.mp4"
    j = Job(p.plan, media["video"], str(out), RenderOptions(preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    import subprocess
    n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip())
    assert n == 60                                                # exact frame count, from ffprobe (OpenCV's estimate can be off by one)
    cap = cv2.VideoCapture(str(out)); cap.set(cv2.CAP_PROP_POS_FRAMES, 10); _, f = cap.read()
    assert f[5, 5, 2] > 200 and f[5, 5, 1] < 60                  # red background where the green screen was


def test_ai_cache_key_depends_on_the_cuts():
    from evp import edl
    assert edl.key([(0, 10)]) != edl.key([(0, 11)]) and edl.key(None) == ""
    assert edl.pieces_for_range([(10, 20), (50, 60)], 5, 10) == [(15, 20), (50, 55)]
    assert edl.is_in_order([(0, 5), (5, 9)]) and not edl.is_in_order([(5, 9), (0, 5)]) and not edl.is_in_order([(0, 6), (5, 9)])


def test_job_refuses_to_overwrite_its_source(prep_r, ramp):
    from evp.engine import Job
    from evp.compiler import RenderOptions
    import hashlib, pathlib
    h = lambda: hashlib.sha256(pathlib.Path(ramp["silent"]).read_bytes()).hexdigest()
    before = h()
    p = prep_r(bp(), ramp["silent"])
    j = Job(p.plan, ramp["silent"], ramp["silent"], RenderOptions(), str(ramp["dir"])); j.run()
    assert j.state == "failed" and "overwrite the original" in j.error and h() == before


def test_every_ai_generator_accepts_the_cut_list():
    """the engine passes edl= to all analysis passes; a missing parameter must fail here, not mid-render"""
    import inspect
    from evp.ml import matting, face, upscale
    for fn in (matting.generate_matte, face.generate_control, upscale.generate_upscale):
        assert "edl" in inspect.signature(fn).parameters, fn.__name__
    for fn in (matting.matte_key, face.control_key, upscale.upscale_key):
        assert "edl" in inspect.signature(fn).parameters, fn.__name__


def test_face_analysis_follows_the_cuts(ramp, tmp_path):
    """frames the face tracker sees on a cut timeline are the cut timeline's frames (spy on the decoder input)"""
    from evp.ml import face
    from evp.blueprint import Operation
    from evp.registry import OPERATIONS
    seen = []
    class SpyTracker:
        def __init__(self, *a, **k): pass
        def update(self, bgr):
            seen.append(int(round(bgr.astype(float).mean() * 219 / 255 / 2))); return []
    orig = face.FaceTracker; face.FaceTracker = SpyTracker
    try:
        params = {k: v.default for k, v in OPERATIONS["face_tone_correction"].params.items()}
        op = Operation("f", "face_tone_correction", 0, 1, 0, 6, params)
        ref = face.generate_control(ramp["silent"], op, 64, 48, 25.0, 0, 6, str(tmp_path), edl=[(50, 53), (10, 13)])
    finally:
        face.FaceTracker = orig
    assert seen == [50, 51, 52, 10, 11, 12] and ref.frames == 6
