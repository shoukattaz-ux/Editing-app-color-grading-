"""Keyframe animation through the real compile path.

Oracle: an animated frame must equal (pixel for pixel) a STATIC render of the same operation whose parameter is set to the
interpolated value at that time. This is what makes the animation trustworthy: it is exactly the static look, per frame.
"""
import json
import numpy as np, cv2, pytest
from conftest import bp
from evp.engine import prepare, render_frame, Job
from evp.compiler import RenderOptions, compile_plan
from evp.keyframes import Keyframe, value_at
from evp.security import PathPolicy

ASSETS = [{"id": "bg", "type": "image", "path": "bg.png"}]


def png(plan, media, t, scale=None):
    return cv2.imdecode(np.frombuffer(render_frame(plan, media["video"], t, scale, str(media["dir"])), np.uint8), cv2.IMREAD_COLOR).astype(int)


def plans(prep, media, op, extra_ops=(), **bpkw):
    return prep(bp(ops=[*extra_ops, op], assets=ASSETS, **bpkw))


def kfs_of(op):
    return {n: [Keyframe(float(k["time"]), float(k["value"]), k.get("easing", "linear")) for k in sorted(v, key=lambda k: k["time"])] for n, v in op["keyframes"].items()}


def static_twin(op, t):
    """same operation with constant parameters = the animated values at timeline time t"""
    p = dict(op.get("parameters", {}))
    for n, k in kfs_of(op).items():
        v = value_at(k, t - op["start"])
        p[n] = int(round(v)) if n == "font_size" else v
    return {k: v for k, v in op.items() if k != "keyframes"} | {"parameters": p}


def assert_matches_static(prep, media, op, times, extra_ops=(), tol=0, **bpkw):
    anim = plans(prep, media, op, extra_ops, **bpkw)
    assert anim.plan, [e.message for e in anim.report.errors]
    seen = []
    for t in times:
        a = png(anim.plan, media, t)
        s = plans(prep, media, static_twin(op, t), extra_ops, **bpkw)
        assert s.plan, [e.message for e in s.report.errors]
        b = png(s.plan, media, t)
        d = np.abs(a - b).max()
        assert d <= tol, f"t={t}: animated differs from static render of the interpolated value (max diff {d})"
        seen.append(a)
    return seen


T = [0.5, 0.7, 1.0, 1.3, 1.9, 2.4, 2.8]        # before / at / inside / after the operation [1.0, 2.5)


# ------------------------------------------------------------------------------------- colour grading
GRADE = {"exposure": (-1.0, 1.0), "contrast": (0.6, 1.8), "brightness": (-0.2, 0.3), "saturation": (0.0, 2.0), "gamma": (0.6, 2.0),
         "vibrance": (-1.0, 1.5), "temperature": (-60, 70), "tint": (-50, 60), "highlights": (-0.8, 0.6), "shadows": (-0.6, 0.9),
         "whites": (-0.5, 0.8), "blacks": (-0.7, 0.9)}


@pytest.mark.parametrize("param", sorted(GRADE))
def test_grade_parameter_animation_is_exactly_the_static_look(prep, media, param):
    v0, v1 = GRADE[param]
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.5, "keyframes": {param: [
        {"time": 0, "value": v0, "easing": "ease_in_out"}, {"time": 1.5, "value": v1}]}}
    frames = assert_matches_static(prep, media, op, T)
    inside = [frames[2], frames[3], frames[4]]
    assert np.abs(inside[0] - inside[2]).max() > 0, f"'{param}' animation had no visible effect"


def test_grade_several_parameters_and_all_easings_together(prep, media):
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.5, "parameters": {"preset": "warm_film", "contrast": 1.1},
          "keyframes": {"exposure": [{"time": 0, "value": 0.0, "easing": "ease_in"}, {"time": 0.5, "value": 0.8, "easing": "hold"}, {"time": 1.0, "value": -0.5, "easing": "ease_out"}, {"time": 1.5, "value": 0.2}],
                        "temperature": [{"time": 0, "value": -40}, {"time": 1.5, "value": 50, "easing": "linear"}],
                        "shadows": [{"time": 0.2, "value": 0.5, "easing": "ease_in_out"}, {"time": 1.2, "value": -0.4}]}}
    # tol=4: at t=1.0 the interpolated exposure is exactly 0, where a STATIC render omits the filter but an animated chain must keep it
    # (a neutral exposure filter still round-trips through float RGB: at most +-4 levels, invisible). Every other frame is exact.
    assert_matches_static(prep, media, op, [1.0], tol=4)
    assert_matches_static(prep, media, op, [33 / 30, 36 / 30, 45 / 30, 54 / 30, 60 / 30, 72 / 30])


def test_keyframes_outside_the_operation_range_still_define_the_curve(prep, media):
    """after a split the bracketing keyframes lie outside the operation; values must still be exact"""
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0,
          "keyframes": {"exposure": [{"time": -1.0, "value": -1.0, "easing": "ease_in_out"}, {"time": 3.0, "value": 1.0}]}}
    assert_matches_static(prep, media, op, [1.0, 1.4, 1.9])


def test_animation_on_a_partial_range_does_not_change_frames_outside_it(prep, media):
    """outside its range an animated operation looks exactly like the same operation made static (same structure), i.e. the
    animation machinery adds nothing. (Any RGB-domain effect, static or animated, resamples chroma slightly even where its
    time range disables it - a property of the pipeline - so the comparison is with the static twin, not the bare source.)"""
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0, "keyframes": {"exposure": [{"time": 0, "value": 1.0}, {"time": 1, "value": -1.0}]}}
    anim = plans(prep, media, op).plan
    for t in (6 / 30, 27 / 30, 66 / 30, 87 / 30):
        twin = plans(prep, media, static_twin(op, t)).plan
        assert np.abs(png(anim, media, t) - png(twin, media, t)).max() == 0, t
        assert np.abs(png(anim, media, t) - png(prep(bp()).plan, media, t)).mean() < 3        # and visually the source


# ------------------------------------------------------------------------------------- chroma key
def test_chroma_key_similarity_blend_and_spill_animate_exactly(prep, media):
    op = {"id": "k", "type": "chroma_key", "start": 0.5, "end": 2.5, "parameters": {"background_color": "#ff0000", "spill_strength": 0.5},
          "keyframes": {"similarity": [{"time": 0, "value": 0.02}, {"time": 2, "value": 0.6, "easing": "ease_in"}],
                        "blend": [{"time": 0, "value": 0.0}, {"time": 2, "value": 0.5}],
                        "spill_strength": [{"time": 0, "value": 1.0}, {"time": 2, "value": 0.0}]}}
    frames = assert_matches_static(prep, media, op, [0.5, 0.9, 1.4, 1.9, 2.3])
    assert np.abs(frames[0] - frames[3]).max() > 0


def test_ai_key_ops_reject_chroma_keyframes_gracefully(prep, media):
    """background_removal in AI mode has no chroma parameters in use; keyframes on them are accepted (harmless) not crashing validation"""
    p = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "end": 2, "parameters": {"mode": "chroma"},
                      "keyframes": {"similarity": [{"time": 0, "value": 0.1}, {"time": 1, "value": 0.3}]}}]))
    assert p.report.ok


# ------------------------------------------------------------------------------------- text
TEXT = {"x": (0.2, 0.8), "y": (0.3, 0.8), "font_size": (20, 60), "opacity": (0.1, 1.0), "box_opacity": (0.0, 1.0)}


@pytest.mark.parametrize("param", sorted(TEXT))
def test_text_parameter_animation_is_exact(prep, media, param):
    v0, v1 = TEXT[param]
    op = {"id": "t", "type": "text_overlay", "start": 1.0, "end": 2.5, "parameters": {"text": "Hello", "box": True},
          "keyframes": {param: [{"time": 0, "value": v0, "easing": "ease_in_out"}, {"time": 1.5, "value": v1}]}}
    frames = assert_matches_static(prep, media, op, [30 / 30, 39 / 30, 53 / 30, 66 / 30])          # frame-aligned: the preview snaps to frames
    assert np.abs(frames[0] - frames[3]).max() > 0


def test_text_fade_in_out_title(prep, media):
    op = {"id": "t", "type": "text_overlay", "start": 0.5, "end": 2.5, "parameters": {"text": "Title", "y": 0.5},
          "keyframes": {"opacity": [{"time": 0, "value": 0}, {"time": 0.5, "value": 1}, {"time": 1.5, "value": 1}, {"time": 2.0, "value": 0}]}}
    base = png(prep(bp()).plan, media, 1.0)
    p = plans(prep, media, op).plan
    assert np.abs(png(p, media, 0.5) - png(prep(bp()).plan, media, 0.5)).max() == 0            # invisible at the very start
    assert np.abs(png(p, media, 1.0) - base).max() > 20                                          # fully visible mid-way
    near_end = np.abs(png(p, media, 74 / 30) - png(prep(bp()).plan, media, 74 / 30)).max()      # frame 74: opacity 0.067 -> at most ~17 levels
    assert near_end < 30 < np.abs(png(p, media, 1.0) - base).max()                                 # nearly gone vs fully visible


# ------------------------------------------------------------------------------------- background (Ken Burns)
def ken_op(**kf):
    return {"id": "r", "type": "background_replacement", "start": 0.5, "end": 2.5, "parameters": {"asset_id": "bg", "fit": "cover"}, "keyframes": kf}


KEY = {"id": "k", "type": "chroma_key", "start": 0, "end": 3}


def test_background_eq_animation_is_exact(prep, media):
    op = ken_op(brightness=[{"time": 0, "value": -0.2}, {"time": 2, "value": 0.3}], saturation=[{"time": 0, "value": 0.2}, {"time": 2, "value": 1.8}])
    assert_matches_static(prep, media, op, [0.5, 0.9, 1.5, 2.2], extra_ops=[KEY])


def test_background_pan_is_exact(prep, media):
    op = ken_op(offset_x=[{"time": 0, "value": -0.2, "easing": "ease_in_out"}, {"time": 2, "value": 0.2}], offset_y=[{"time": 0, "value": 0.1}, {"time": 2, "value": -0.1}])
    assert_matches_static(prep, media, op, [0.5, 0.9, 1.5, 2.2], extra_ops=[KEY])


def test_background_zoom_follows_the_keyframes(prep, media):
    """zoom is applied after a fixed-size fit (two resampling steps), so compare with a small tolerance and check the geometry"""
    op = ken_op(scale=[{"time": 0, "value": 1.0}, {"time": 2, "value": 2.0}])
    anim = plans(prep, media, op, [KEY]).plan
    for t in (0.5, 1.5, 2.3):
        a = png(anim, media, t)
        s = png(plans(prep, media, static_twin(op, t), [KEY]).plan, media, t)
        assert np.abs(a - s).mean() < 3.0, (t, np.abs(a - s).mean())
    assert np.abs(png(anim, media, 0.5) - png(anim, media, 2.3)).mean() > 2      # and it really moved between the keyframes


# ------------------------------------------------------------------------------------- command files / chains
def test_command_file_contains_only_changes_and_is_frame_timed(prep, media, tmp_path):
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0, "parameters": {"contrast": 1.3},
          "keyframes": {"exposure": [{"time": 0, "value": 0.0}, {"time": 1, "value": 1.0}]}}
    p = plans(prep, media, op).plan
    c = compile_plan(p, media["video"], str(tmp_path / "o.mp4"), RenderOptions(), str(tmp_path))
    assert "sendcmd=f=" in c.filtergraph and "exposure@" in c.filtergraph
    cmd = next(f for f in c.work_files if f.endswith(".cmd"))
    lines = open(cmd).read().strip().split("\n")
    assert len(lines) == 29                                              # 30 frames in [1,2) minus the first (carried by the filter's initial options)
    assert lines[0].startswith(f"{(31 - 0.5) / 30:.6f} exposure@")
    assert all(l.count(",") == 0 and "contrast" not in l for l in lines)   # only the animated option, never the static one


def test_static_operations_are_not_touched_by_the_animation_machinery(prep, media, tmp_path):
    p = plans(prep, media, {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0, "parameters": {"exposure": 0.5}}).plan
    c = compile_plan(p, media["video"], str(tmp_path / "o.mp4"), RenderOptions(), str(tmp_path))
    assert "sendcmd" not in c.filtergraph and "@" not in c.filtergraph and not [f for f in c.work_files if f.endswith(".cmd")]


def test_preview_starting_inside_the_operation_catches_up_and_skips_old_commands(prep, media, tmp_path):
    op = {"id": "g", "type": "color_grading", "start": 0.0, "end": 3.0, "keyframes": {"exposure": [{"time": 0, "value": -1.0}, {"time": 3, "value": 1.0}]}}
    p = plans(prep, media, op).plan
    c = compile_plan(p, media["video"], "-", RenderOptions(t_start=2.0, frame_only=True), str(tmp_path))
    assert "sendcmd" not in c.filtergraph or not [f for f in c.work_files if f.endswith(".cmd")]      # a single frame needs no commands
    init = float(c.filtergraph.split("exposure@kf1=exposure=")[1].split(",")[0].split("[")[0])
    assert init == pytest.approx(value_at(kfs_of(op)["exposure"], 2.0), abs=1e-3)


# ------------------------------------------------------------------------------------- preview == export, cuts
def test_export_matches_preview_frame_by_frame(prep, media, tmp_path):
    op = {"id": "g", "type": "color_grading", "start": 1.0, "end": 2.5,
          "keyframes": {"exposure": [{"time": 0, "value": -0.8, "easing": "ease_in_out"}, {"time": 1.5, "value": 0.8}], "saturation": [{"time": 0, "value": 0.3}, {"time": 1.5, "value": 1.6}]}}
    p = plans(prep, media, op, output={"resolution": "source", "fps": "source", "crf": 0, "preset": "ultrafast"}).plan
    out = tmp_path / "anim.mp4"
    j = Job(p, media["video"], str(out), RenderOptions(crf=0, preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    cap = cv2.VideoCapture(str(out))
    for n in (29, 30, 31, 45, 60, 74, 75, 80):
        cap.set(cv2.CAP_PROP_POS_FRAMES, n); _, ex = cap.read()
        pv = png(p, media, n / 30)
        assert np.abs(pv - ex.astype(int)).mean() < 1.5, (n, np.abs(pv - ex.astype(int)).mean())


def test_animation_follows_the_cut_timeline(prep, media, tmp_path):
    """operation times (and so keyframes) are on the cut timeline: [1.0, 2.0) of the cut timeline is source 2.0-3.0 here"""
    cuts = {"clips": [{"id": "a", "start": 0, "end": 1}, {"id": "b", "start": 2, "end": 3}]}
    kf = [Keyframe(0, -0.6), Keyframe(1, 1.0)]
    anim = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0,
                         "keyframes": {"exposure": [{"time": 0, "value": -0.6}, {"time": 1, "value": 1.0}]}}]) | cuts).plan
    for tf in (31, 40, 50, 58):
        t = tf / 30
        twin = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0, "parameters": {"exposure": value_at(kf, t - 1.0)}}]) | cuts).plan
        assert np.abs(png(anim, media, t) - png(twin, media, t)).max() == 0, tf


# ------------------------------------------------------------------------------------- audio
def test_audio_gain_automation_follows_the_curve(prep, media, tmp_path):
    import subprocess
    from evp.media import ffmpeg_bin
    op = {"id": "a", "type": "audio_gain", "start": 0.0, "end": 3.0,
          "keyframes": {"gain_db": [{"time": 0, "value": 0.0}, {"time": 1.0, "value": -24.0}, {"time": 2.0, "value": -24.0}, {"time": 3.0, "value": 0.0}]}}
    p = prep(bp(ops=[op])).plan
    out = tmp_path / "audio.mp4"
    j = Job(p, media["video"], str(out), RenderOptions(preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    sr = 16000
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(out), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(float)
    ref = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", media["video"], "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True).stdout
    r = np.frombuffer(ref, np.int16).astype(float)
    rms = lambda x, t0, t1: np.sqrt(np.mean(x[int(t0 * sr):int(t1 * sr)] ** 2))
    kf = [Keyframe(0, 0.0), Keyframe(1, -24.0), Keyframe(2, -24.0), Keyframe(3, 0.0)]
    for t0, t1 in ((0.1, 0.2), (0.4, 0.5), (0.9, 1.0), (1.2, 1.8), (2.4, 2.5), (2.8, 2.9)):
        expect = value_at(kf, (t0 + t1) / 2)                               # gain at the window's centre
        got = 20 * np.log10(rms(a, t0, t1) / rms(r, t0, t1))
        assert abs(got - expect) < 1.5, (t0, round(got, 2), round(expect, 2))   # gain updates per audio frame; allow the in-window ramp + AAC error


# ------------------------------------------------------------------------------------- validation
def test_keyframe_validation(prep):
    def errs(kf, op_type="color_grading", params=None):
        p = prep(bp(ops=[{"id": "o", "type": op_type, "start": 0, "end": 2, "parameters": params or {"text": "x"} if op_type == "text_overlay" else (params or {}), "keyframes": kf}]))
        return [(e.code, e.message) for e in p.report.errors]
    ok = [{"time": 0, "value": 0.0}, {"time": 1, "value": 1.0}]
    assert errs({"exposure": ok}) == []
    assert errs({"nope": ok})[0][0] == "UNKNOWN_PARAMETER"
    assert errs({"lut_intensity": ok})[0][0] == "PARAM_NOT_ANIMATABLE" and "LUT" in errs({"lut_intensity": ok})[0][1]
    assert errs({"preset": ok})[0][0] == "PARAM_NOT_ANIMATABLE"
    assert errs({"lift": ok})[0][0] == "PARAM_NOT_ANIMATABLE"
    r = errs({"blur": ok}, "background_replacement"); assert r[0][0] == "PARAM_NOT_ANIMATABLE" and "Gaussian blur" in r[0][1] and "scale" in r[0][1]
    assert errs({"exposure": []})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": "x"})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": [{"time": 0, "value": 9.0}]})[0][0] == "INVALID_KEYFRAME"               # out of range
    assert errs({"exposure": [{"time": 0, "value": "a"}]})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": [{"time": "x", "value": 0}]})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": [{"time": 0, "value": 0, "easing": "bounce"}]})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": [{"time": 1, "value": 0}, {"time": 1, "value": 1}]})[0][0] == "INVALID_KEYFRAME"   # same time
    assert errs({"exposure": [0, 1]})[0][0] == "INVALID_KEYFRAME"
    assert errs({"exposure": [{"time": i, "value": 0} for i in range(65)]})[0][0] == "TOO_MANY_KEYFRAMES"
    assert errs([1, 2])[0][0] == "INVALID_STRUCTURE"


def test_unsorted_keyframes_are_accepted_and_sorted_negative_times_allowed(prep):
    p = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 2,
                      "keyframes": {"exposure": [{"time": 1, "value": 1.0}, {"time": -0.5, "value": -1.0}, {"time": 0.5, "value": 0.0, "easing": "hold"}]}}]))
    assert p.report.ok
    k = p.report.blueprint.operations[0].keyframes["exposure"]
    assert [x.time for x in k] == [-0.5, 0.5, 1.0] and k[1].easing == "hold"


def test_registry_flags_match_the_documented_set():
    from evp.registry import OPERATIONS
    anim = {t: sorted(n for n, p in s.params.items() if p.animatable) for t, s in OPERATIONS.items() if any(p.animatable for p in s.params.values())}
    assert "blur" not in anim["background_replacement"] and "denoise" not in anim.get("video_enhancement", [])
    assert set(anim) == {"color_grading", "chroma_key", "background_removal", "background_replacement", "text_overlay", "audio_gain",
                         "face_tone_correction", "face_relighting", "ai_upscale"}
    assert all(OPERATIONS[t].params[n].type in ("number", "integer") for t, ns in anim.items() for n in ns)


# ------------------------------------------------------------------------------------- analysis-side (face / upscale)
def test_face_analysis_receives_per_frame_parameters(ramp, tmp_path, monkeypatch):
    from evp.ml import face
    from evp.blueprint import Operation
    from evp.registry import OPERATIONS
    seen = []
    class Spy:
        def __init__(self, *a, **k): pass
        def update(self, bgr): return []
    monkeypatch.setattr(face, "FaceTracker", Spy)
    monkeypatch.setitem(face.KINDS, "face_relighting", lambda rgb, faces, p, _: seen.append(round(p["key_angle"], 3)) or (np.zeros(rgb.shape[:2], np.float32),) * 3)
    params = {k: v.default for k, v in OPERATIONS["face_relighting"].params.items()}
    op = Operation("r", "face_relighting", 0.4, 0.8, 10, 20, params, keyframes={"key_angle": [Keyframe(0.0, -60.0), Keyframe(0.4, 60.0)]})
    face.generate_control(ramp["silent"], op, 64, 48, 25.0, 10, 10, str(tmp_path))
    assert seen == pytest.approx([-60 + 120 * i / 10 for i in range(10)], abs=1e-6)
    # and the analysis cache is keyed on the animation
    op2 = Operation("r", "face_relighting", 0.4, 0.8, 10, 20, params, keyframes={"key_angle": [Keyframe(0.0, -60.0), Keyframe(0.4, 30.0)]})
    assert face.control_key("face_relighting", ramp["silent"], op, 64, 48, 25.0, 10, 10) != face.control_key("face_relighting", ramp["silent"], op2, 64, 48, 25.0, 10, 10)


def test_face_tone_colour_controls_use_commands(prep, media, tmp_path):
    op = {"id": "f", "type": "face_tone_correction", "start": 0, "end": 2, "keyframes": {"warmth": [{"time": 0, "value": -50}, {"time": 2, "value": 50}], "saturation": [{"time": 0, "value": 0.5}, {"time": 2, "value": 1.5}]}}
    p = prep(bp(ops=[op]))
    assert p.report.ok
    from evp.ml.face import ControlRef
    from evp.ml import face as F
    # a face-control video with no faces is enough to compile and run the graph
    from evp.engine import ensure_mattes
    try: ensure_mattes(p.plan, media["video"], str(tmp_path))
    except Exception as e: pytest.skip(f"face model unavailable: {e}")
    c = compile_plan(p.plan, media["video"], str(tmp_path / "f.mp4"), RenderOptions(preset="ultrafast"), str(tmp_path))
    assert "colorchannelmixer@" in c.filtergraph and "eq@" in c.filtergraph and "sendcmd=f=" in c.filtergraph
    j = Job(p.plan, media["video"], str(tmp_path / "f.mp4"), RenderOptions(preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error


def test_upscale_strength_and_grain_animate_per_frame(prep, media, tmp_path):
    """AI upscale mixes the network output with a classical upscale by `strength`: at 0 the frame must equal the classical result,
    at 1 the static strength=1 result, in between something different from both - all driven by keyframes."""
    from evp.ml import models
    try:
        import onnx  # noqa
        ok, why = models.runtime_available()
        assert ok, why
        models.ensure_model("esrgan_general")
    except Exception as e:
        pytest.skip(f"AI upscaling unavailable: {e}")
    out = {"resolution": "480x270", "fps": "source"}
    base = {"id": "u", "type": "ai_upscale", "start": 0, "end": 3, "parameters": {"temporal_stability": 0.0, "face_detail": 1.0}}
    anim = prep(bp(ops=[base | {"keyframes": {"strength": [{"time": 0, "value": 0.0}, {"time": 1.0, "value": 1.0}, {"time": 3.0, "value": 1.0}]}}], output=out)).plan
    classic = prep(bp(output=out)).plan
    full = prep(bp(ops=[base | {"parameters": base["parameters"] | {"strength": 1.0}}], output=out)).plan
    at = lambda plan, t: png(plan, media, t)
    assert np.abs(at(anim, 0.0) - at(classic, 0.0)).mean() < 1.0               # strength 0 -> classical upscale (OpenCV vs FFmpeg Lanczos differ slightly)
    assert np.abs(at(anim, 1.0) - at(full, 1.0)).max() == 0                    # strength 1 -> exactly the static strength=1 render
    mid = at(anim, 0.5)                                                        # (synthetic frame is mostly flat: judge by the max, not the mean)
    assert np.abs(mid - at(classic, 0.5)).max() > 8 and np.abs(mid - at(full, 0.5)).max() > 5    # strength 0.5 -> in between
    # the temporal filter and grain can be keyframed too (components are created from the extreme keyframe values)
    both = prep(bp(ops=[base | {"keyframes": {"temporal_stability": [{"time": 0, "value": 0.0}, {"time": 3, "value": 0.9}],
                                              "grain": [{"time": 0, "value": 0.0}, {"time": 3, "value": 1.0}]}}], output=out)).plan
    assert np.abs(at(both, 0.0) - at(full, 0.0)).max() == 0                    # at t=0 the keyframed values equal the static ones -> identical render
    assert np.abs(at(both, 2.9) - at(full, 2.9)).max() > 0                     # by the end grain/temporal differ from the static render
