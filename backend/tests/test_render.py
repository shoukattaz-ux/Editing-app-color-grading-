import numpy as np, cv2
import subprocess, json
from pathlib import Path
import pytest
from conftest import bp, pixel
from evp.engine import render_frame, Job, JobManager
from evp.compiler import RenderOptions, compile_plan
from evp.media import probe, ffmpeg_bin

ASSETS = [{"id": "bg", "type": "image", "path": "bg.png"}]
KEY = {"id": "k", "type": "background_removal", "start": 0, "end": 3}


def frame(prep, media, ops, t, assets=ASSETS, w=None):
    p = prep(bp(ops=ops, assets=assets))
    assert p.plan, p.report.errors
    return render_frame(p.plan, media["video"], t, w, str(media["dir"]))


def test_unprocessed_frame_is_green(prep, media):
    r, g, b = pixel(frame(prep, media, [], 1.0), 5, 5)
    assert g > 200 and r < 40 and b < 40


def test_background_replacement_replaces_green_keeps_subject(prep, media):
    png = frame(prep, media, [KEY, {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg"}, "depends_on": ["k"]}], 1.0)
    r, g, b = pixel(png, 5, 5)
    assert not (g > 200 and r < 40), "green screen still visible"
    r2, g2, b2 = pixel(png, 160, 90)      # subject disc must survive
    assert r2 > 150 and g2 > 100 and b2 < 160 and r2 > b2


def test_replacement_only_inside_time_range(prep, media):
    ops = [KEY, {"id": "r", "type": "background_replacement", "start": 1.0, "end": 2.0, "parameters": {"asset_id": "bg"}}]
    before, inside, after = (pixel(frame(prep, media, ops, t), 5, 5) for t in (0.5, 1.5, 2.5))
    assert before[1] > 200 and after[1] > 200 and not inside[1] > 200


def test_range_boundary_is_frame_exact(prep, media):
    ops = [KEY, {"id": "r", "type": "background_replacement", "start": 1.0, "end": 2.0, "parameters": {"asset_id": "bg"}}]
    f29, f30, f59, f60 = (pixel(frame(prep, media, ops, n / 30), 5, 5)[1] > 200 for n in (29, 30, 59, 60))
    assert (f29, f30, f59, f60) == (True, False, False, True)


def test_chroma_key_alone_fills_background_color(prep, media):
    ops = [{"id": "k", "type": "chroma_key", "start": 0, "end": 3, "parameters": {"background_color": "#ff0000"}}]
    r, g, b = pixel(frame(prep, media, ops, 1.0), 5, 5)
    assert r > 200 and g < 40 and b < 40


def test_blue_key_leaves_green_untouched(prep, media):
    ops = [{"id": "k", "type": "chroma_key", "start": 0, "end": 3, "parameters": {"key_color": "blue", "background_color": "#ff0000"}}]
    r, g, b = pixel(frame(prep, media, ops, 1.0), 5, 5)
    assert g > 200 and r < 40


def test_color_grading_changes_pixels_in_range_only(prep, media):
    ops = [{"id": "g", "type": "color_grading", "start": 1, "end": 2, "parameters": {"temperature": 60, "saturation": 1.2}}]
    out = [pixel(frame(prep, media, ops, t), 160, 90) for t in (0.5, 1.5, 2.5)]
    assert max(abs(a - b) for a, b in zip(out[0], out[2])) <= 2                   # unchanged outside the range
    assert out[1][0] > out[0][0] + 5 and out[1][2] < out[0][2] - 5                  # warmer inside = more red, less blue


def test_exposure_and_contrast_direction(prep, media):
    base = sum(pixel(frame(prep, media, [], 1.0), 160, 90))
    up = sum(pixel(frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"exposure": 1.0}}], 1.0), 160, 90))
    down = sum(pixel(frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"exposure": -1.0}}], 1.0), 160, 90))
    assert down < base < up


def test_lift_gamma_gain_curves_and_tone_controls_compile_and_change(prep, media):
    base = pixel(frame(prep, media, [], 1.0), 160, 90)
    params = {"lift": [0.1, 0, 0], "gain": [0, 0, 0.1], "curve_red": [[0, 0], [0.5, 0.8], [1, 1]], "shadows": 0.5, "highlights": -0.3,
              "blacks": -0.5, "whites": 0.5, "vibrance": 0.5, "tint": 20}
    out = pixel(frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": params}], 1.0), 160, 90)
    assert out != base


def test_all_presets_render(prep, media):
    from evp.registry import OPERATIONS
    for pr in OPERATIONS["color_grading"].params["preset"].enum:
        frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"preset": pr}}], 1.0)


@pytest.mark.parametrize("intensity", [1.0, 0.5])
def test_lut_applied(prep, media, intensity):
    assets = [{"id": "lut", "type": "lut", "path": "warm.cube"}]
    base = pixel(frame(prep, media, [], 1.0, assets), 160, 90)
    out = pixel(frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"lut_asset_id": "lut", "lut_intensity": intensity}}], 1.0, assets), 160, 90)
    assert out[0] > base[0] and abs(out[2] - base[2]) <= 3     # red boosted by the LUT; blue untouched


def test_partial_lut_is_between(prep, media):
    assets = [{"id": "lut", "type": "lut", "path": "warm.cube"}]
    mk = lambda i: pixel(frame(prep, media, [{"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"lut_asset_id": "lut", "lut_intensity": i}}], 1.0, assets), 160, 90)[0]
    assert mk(0.0 + 0.001) <= mk(0.5) <= mk(1.0)


def test_enhancement_ranged_non_timeline_filter(prep, media):
    ops = [{"id": "e", "type": "video_enhancement", "start": 1, "end": 2, "parameters": {"stabilize": True, "denoise": 0.5, "sharpen": 1.0}}]
    frame(prep, media, ops, 1.5)


def test_text_overlay_no_expansion_and_draws(prep, media):
    ops = [{"id": "t", "type": "text_overlay", "start": 0, "end": 3, "parameters": {"text": "A' ; [x] %{pts} \\n", "box": True, "y": 0.5, "x": 0.5}}]
    a, b = frame(prep, media, ops, 1.0), frame(prep, media, [], 1.0)
    assert a != b


def test_scale_width_preview(prep, media):
    import cv2, numpy as np
    png = frame(prep, media, [], 1.0, w=160)
    assert cv2.imdecode(np.frombuffer(png, np.uint8), 1).shape[:2] == (90, 160)


def test_full_render_with_everything(prep, media):
    ops = [KEY, {"id": "r", "type": "background_replacement", "start": 0.5, "end": 2.5, "parameters": {"asset_id": "bg", "blur": 3, "scale": 1.2, "offset_x": 0.1}, "depends_on": ["k"]},
           {"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": {"preset": "warm_film"}},
           {"id": "e", "type": "video_enhancement", "start": 0, "end": 3, "parameters": {"denoise": 0.2, "sharpen": 0.3}},
           {"id": "a", "type": "audio_gain", "start": 0, "end": 3, "parameters": {"gain_db": -6, "fade_in": 0.5, "fade_out": 0.5}},
           {"id": "t", "type": "text_overlay", "start": 1, "end": 2, "parameters": {"text": "Hi"}}]
    p = prep(bp(ops=ops, assets=ASSETS, output={"resolution": "160x90", "fps": 24, "format": "mp4", "codec": "h264", "preset": "ultrafast"}))
    assert p.plan, p.report.errors
    out = media["dir"] / "full.mp4"
    j = Job(p.plan, media["video"], str(out), RenderOptions(), str(media["dir"]))
    j.run()
    assert j.state == "done", j.error
    info = probe(out)
    assert (info.width, info.height) == (160, 90) and abs(info.fps - 24) < .01 and abs(info.duration - 3) < 0.15 and info.has_audio
    assert j.progress == 1.0


def test_h265_and_mov(prep, media):
    p = prep(bp(output={"resolution": "160x90", "fps": 30, "format": "mov", "codec": "h265", "preset": "ultrafast"}))
    out = media["dir"] / "h265.mov"
    j = Job(p.plan, media["video"], str(out), RenderOptions(), str(media["dir"])); j.run()
    assert j.state == "done", j.error
    assert probe(out).codec == "hevc"


def test_original_never_modified(prep, media):
    import hashlib
    h = lambda: hashlib.sha256(Path(media["video"]).read_bytes()).hexdigest()
    before = h()
    p = prep(bp(ops=[KEY]))
    Job(p.plan, media["video"], str(media["dir"] / "x.mp4"), RenderOptions(), str(media["dir"])).run()
    assert h() == before


def test_output_path_safety(media, tmp_path):
    from evp.security import safe_output_path, PathError
    with pytest.raises(PathError): safe_output_path(media["video"], media["video"], media["dir"])
    with pytest.raises(PathError): safe_output_path("/etc/x.mp4", media["video"], media["dir"])


def test_cancel_removes_partial_output(prep, media):
    ops = [{"id": "e", "type": "video_enhancement", "start": 0, "end": 3, "parameters": {"denoise": 1, "sharpen": 2}}]
    p = prep(bp(ops=ops, output={"resolution": "source", "fps": 30, "preset": "veryfast"}))
    out = media["dir"] / "cancel.mp4"
    j = Job(p.plan, media["video"], str(out), RenderOptions(), str(media["dir"]))
    j.cancel()                      # cancelled before start
    j.run()
    assert j.state == "cancelled" and not out.exists()
    assert not list(media["dir"].glob("cancel.mp4.part*"))


def test_pause_resume_cancel_running_job(prep, media):
    import threading, time
    long = media["dir"] / "long.mp4"
    from evp.samples import make_greenscreen
    make_greenscreen(long, 20, size="640x360")
    p = prep(bp(ops=[{"id": "e", "type": "video_enhancement", "start": 0, "end": 20, "parameters": {"denoise": 1, "sharpen": 2}}],
                output={"resolution": "source", "fps": "source", "preset": "slow"}), str(long))
    out = media["dir"] / "long_out.mp4"
    j = Job(p.plan, str(long), str(out), RenderOptions(), str(media["dir"]))
    th = threading.Thread(target=j.run); th.start()
    for _ in range(100):
        if j.state == "running" and j._proc: break
        time.sleep(0.05)
    assert j.pause() and j.state == "paused"
    assert j.resume() and j.state == "running"
    j.cancel(); th.join(20)
    assert j.state == "cancelled" and not out.exists()


def test_failed_job_reports_and_retry(prep, media):
    p = prep(bp(ops=[KEY]))
    j = Job(p.plan, str(media["dir"] / "ghost.mp4"), str(media["dir"] / "f.mp4"), RenderOptions(), str(media["dir"]))
    j.run()
    assert j.state == "failed" and j.error
    m = JobManager(); m.jobs[j.id] = j
    r = m.retry(j.id); assert r.id != j.id


def test_hardware_fallback_to_cpu(prep, media):
    p = prep(bp(output={"resolution": "160x90", "fps": 30, "preset": "ultrafast"}))
    j = Job(p.plan, media["video"], str(media["dir"] / "hw.mp4"), RenderOptions(encoder="h264_nvenc"), str(media["dir"]))
    j.run()
    assert j.state == "done", j.error   # nvenc unavailable here -> compiler falls back to libx264 (or retry path)


def test_execution_is_deterministic_in_dependency_order(prep):
    ops = [{"id": "g", "type": "color_grading", "start": 0, "end": 3}, {"id": "k", "type": "chroma_key", "start": 0, "end": 3},
           {"id": "r", "type": "background_replacement", "start": 0, "end": 3}]
    a = [o.id for o in prep(bp(ops=ops)).plan.steps]
    assert a == ["k", "r", "g"]


def test_disabled_operation_not_executed(prep, media):
    ops = [dict(KEY, enabled=False, parameters={"background_color": "#ff0000"})]
    r, g, b = pixel(frame(prep, media, ops, 1.0), 5, 5)
    assert g > 200


def test_prores_export_10bit_422_with_pcm(prep, media):
    import json, subprocess
    from evp.media import ffprobe_bin
    p = prep(bp(output={"resolution": "160x90", "fps": 30, "format": "mov", "codec": "prores", "prores_profile": "lt"}))
    assert p.plan, p.report.errors
    out = media["dir"] / "pr.mov"
    j = Job(p.plan, media["video"], str(out), RenderOptions(), str(media["dir"])); j.run()
    assert j.state == "done", j.error
    s = json.loads(subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"]
    v = next(x for x in s if x["codec_type"] == "video"); a = next(x for x in s if x["codec_type"] == "audio")
    assert v["codec_name"] == "prores" and v["pix_fmt"] == "yuv422p10le" and v.get("profile", "").lower().startswith("lt")
    assert a["codec_name"] == "pcm_s16le"
    assert any(e.code == "INVALID_PARAMETER" for e in prep(bp(output={"format": "mp4", "codec": "prores"})).report.errors)


@pytest.mark.parametrize("rate", ["25", "30000/1001"])
def test_single_frame_preview_shows_the_exact_frame(prep, tmp_path, rate):
    """Regression: seeking to the raw time could show the next frame (AI layers then misaligned by one frame)."""
    import subprocess
    from evp.media import ffmpeg_bin
    from evp.security import PathPolicy
    from evp.engine import prepare
    v = tmp_path / "ramp.mp4"      # every frame a different grey level: frame index = round(level / 3)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s=64x48:r={rate}:d=3", "-vf",
                    "geq=lum='16+3*N':cb=128:cr=128", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(v)], check=True)
    plan = prepare(bp(), str(v), PathPolicy([tmp_path])).plan
    fps = plan.blueprint.output.fps
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(v), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    truth = np.frombuffer(raw, np.uint8).reshape(-1, 48 * 64).mean(1)          # brightness of every source frame
    for t in (0.5, 1.0, 1.37, 2.0):
        im = cv2.imdecode(np.frombuffer(render_frame(plan, str(v), t, None, str(tmp_path)), np.uint8), 0).astype(float)
        shown = int(np.argmin(np.abs(truth - im.mean())))
        assert shown == round(t * fps), (t, shown)


# ---------------------------------------------------------------- lift / gamma / gain (colour wheels)
def _lgg_ref(x, lift, gamma, gain):
    """the documented formula, per channel; x in 0..1"""
    y = max(0.0, (1 + gain) * (x + lift * (1 - x)))
    return min(1.0, max(0.0, y ** (2 ** -gamma)))


def _ramp_video(tmp_path):
    import subprocess
    from evp.media import ffmpeg_bin
    p = tmp_path / "ramp256.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "nullsrc=s=256x16:r=25:d=1,format=gbrp,geq=r='X':g='X':b='X'",
                    "-c:v", "libx264rgb", "-crf", "0", str(p)], check=True)
    return str(p)


@pytest.mark.parametrize("params", [{"lift": [0.1, 0.0, -0.05]}, {"gain": [0.3, -0.2, 0.0]}, {"gamma_rgb": [0.3, 0.0, -0.4]},
                                    {"lift": [0.05, 0.05, 0.1], "gamma_rgb": [-0.2, 0.1, 0.0], "gain": [0.1, 0.1, -0.1]}])
def test_lift_gamma_gain_follow_the_documented_formula(prep, tmp_path, params):
    from evp.security import PathPolicy
    from evp.engine import prepare
    v = _ramp_video(tmp_path)
    p = prepare(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": params}]), v, PathPolicy([tmp_path]), tmp_path)
    assert p.plan, [e.message for e in p.report.errors]
    im = cv2.imdecode(np.frombuffer(render_frame(p.plan, v, 0.2, None, str(tmp_path)), np.uint8), cv2.IMREAD_COLOR).astype(int)[:, :, ::-1]   # RGB
    lift, gamma, gain = (params.get(k, [0, 0, 0]) for k in ("lift", "gamma_rgb", "gain"))
    worst = 0
    for c in range(3):
        row = im[8, :, c]
        for x in range(0, 256, 5):
            exp = 255 * _lgg_ref(x / 255, lift[c], gamma[c], gain[c])
            worst = max(worst, abs(row[x] - exp))
    assert worst <= 3, f"worst deviation from the formula: {worst} levels"       # +-3: 8-bit YUV round trip


def test_lift_only_moves_shadows_gain_never_moves_black_gamma_keeps_both_ends(prep, tmp_path):
    from evp.security import PathPolicy
    from evp.engine import prepare
    v = _ramp_video(tmp_path)
    def row(params):
        p = prepare(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": params}]), v, PathPolicy([tmp_path]), tmp_path)
        return cv2.imdecode(np.frombuffer(render_frame(p.plan, v, 0.2, None, str(tmp_path)), np.uint8), 1).astype(int)[8, :, 1]
    base = row({})
    lift = row({"lift": [0.2, 0.2, 0.2]}) - base
    assert lift[10] > 30 and abs(lift[250]) <= 3 and lift[128] < lift[10] * 0.6        # lift tapers linearly: 0.2*(1-x) -> strongest in the shadows, ~0 at white
    gain = row({"gain": [0.3, 0.3, 0.3]}) - base
    assert abs(gain[0]) <= 2 and gain[100] > 15                                          # black stays, mids/highs scale
    gam = row({"gamma_rgb": [0.4, 0.4, 0.4]}) - base
    assert abs(gam[0]) <= 2 and abs(gam[255]) <= 2 and 20 < gam[128] < 27            # ends fixed, midtones lifted by 255*(0.5**(2**-0.4)-0.5) = 23.3


# ---------------------------------------------------------------- colour fidelity (4:4:4 pipeline, explicit matrix)
def _bars(path, tagged709):
    """lossless 4:2:0 clip of hard colour bars on the chroma grid; optionally BT.709-coded and tagged"""
    g = ("color=c=red:s=320x180:r=25:d=2[a];color=c=0x00c800:s=80x180:r=25:d=2[b];color=c=blue:s=80x180:r=25:d=2[c];color=c=magenta:s=80x180:r=25:d=2[d];"
         "[a][b]overlay=x=80[x];[x][c]overlay=x=160[y];[y][d]overlay=x=240,format=rgb24")
    g += ",scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p" if tagged709 else ",format=yuv420p"
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"] if tagged709 else []
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-filter_complex", g, "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", *tags, str(path)], check=True)
    return str(path)


def _planes(path):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    w, h = 320, 180
    a = np.frombuffer(raw, np.uint8).reshape(-1, w * h * 3 // 2).astype(int)
    return a[:, :w * h].reshape(-1, h, w), a[:, w * h:w * h * 5 // 4].reshape(-1, h // 2, w // 2), a[:, w * h * 5 // 4:].reshape(-1, h // 2, w // 2)


def _render_file(tmp_path, src, ops, name):
    from evp.security import PathPolicy
    from evp.engine import prepare
    p = prepare(bp(ops=ops, output={"resolution": "source", "fps": "source", "crf": 0, "preset": "ultrafast"}), src, PathPolicy([tmp_path]), tmp_path)
    assert p.plan, [e.message for e in p.report.errors]
    out = tmp_path / f"{name}.mp4"
    j = Job(p.plan, src, str(out), RenderOptions(crf=0, preset="ultrafast"), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    return out


def test_neutral_effect_leaves_frames_outside_its_range_bit_exact(tmp_path):
    """Regression: an RGB-domain filter used to resample chroma on EVERY frame, even where the effect is off"""
    src = _bars(tmp_path / "bars.mp4", False)
    neutral = [{"id": "g", "type": "color_grading", "start": 1.0, "end": 2.0, "keyframes": {"exposure": [{"time": 0, "value": 0.0}, {"time": 1, "value": 0.0}]}}]
    plain, with_fx = _planes(_render_file(tmp_path, src, [], "plain")), _planes(_render_file(tmp_path, src, neutral, "neutral"))
    for a, b in zip(plain, with_fx):
        assert np.array_equal(a[:25], b[:25]), "frames outside the effect's range changed"        # untouched
        assert np.abs(a[26:49] - b[26:49]).max() <= 1, "a neutral effect must not change the picture inside its range either"


def test_plain_pipeline_is_bit_exact(tmp_path):
    src = _bars(tmp_path / "bars.mp4", True)
    for a, b in zip(_planes(src), _planes(_render_file(tmp_path, src, [], "plain"))):
        assert np.array_equal(a, b)


@pytest.mark.parametrize("tagged709", [True, False])
@pytest.mark.parametrize("name,params,fn", [
    ("gain", {"gain": [-0.2, -0.2, -0.2]}, lambda r, g, b: (0.8 * r, 0.8 * g, 0.8 * b)),
    ("lift", {"lift": [0, 0, 0.1]}, lambda r, g, b: (r, g, b + 0.1 * (1 - b))),
])
def test_rgb_effects_match_an_independent_float_calculation_on_hard_colour_edges(tmp_path, tagged709, name, params, fn):
    """The picture after an RGB effect equals: full-resolution chroma -> RGB (correct matrix) -> effect -> YUV -> 2x2 chroma mean.
    A 4:2:0->RGB->4:2:0 round trip, or the wrong matrix (601 for HD footage), misses this by 10-30 levels at the colour edges."""
    src = _bars(tmp_path / "bars.mp4", tagged709)
    kr, kb = (0.2126, 0.0722) if tagged709 else (0.299, 0.114)
    out = _planes(_render_file(tmp_path, src, [{"id": "g", "type": "color_grading", "start": 0, "end": 2, "parameters": params}], "fx"))
    Y, U, V = (p[10:40].astype(float) for p in _planes(src))
    up = lambda c: np.repeat(np.repeat(c, 2, axis=-2), 2, axis=-1)
    y, cb, cr = (Y - 16) / 219, (up(U) - 128) / 224, (up(V) - 128) / 224
    r, b = y + 2 * (1 - kr) * cr, y + 2 * (1 - kb) * cb
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    r, g, b = (np.clip(x, 0, 1) for x in fn(*(np.clip(x, 0, 1) for x in (r, g, b))))
    y2 = kr * r + (1 - kr - kb) * g + kb * b
    ref = (16 + 219 * y2, 128 + 224 * (b - y2) / (2 * (1 - kb)), 128 + 224 * (r - y2) / (2 * (1 - kr)))
    ref = (ref[0], *(((c[..., 0::2, 0::2] + c[..., 0::2, 1::2] + c[..., 1::2, 0::2] + c[..., 1::2, 1::2]) / 4) for c in ref[1:]))
    for plane, o, r_ in zip("YUV", out, ref):
        assert np.abs(o[10:40] - np.floor(r_ + 0.5)).max() <= 2, f"{name} {plane}"


def test_output_is_tagged_with_the_matrix_it_was_encoded_with(tmp_path):
    out = _render_file(tmp_path, _bars(tmp_path / "bars.mp4", True), [{"id": "g", "type": "color_grading", "start": 0, "parameters": {"gain": [-0.1, 0, 0]}}], "tag")
    v = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(out)], capture_output=True, text=True).stdout)["streams"][0]
    assert (v["color_space"], v["color_range"], v["pix_fmt"]) == ("bt709", "tv", "yuv420p")


# ---------------------------------------------------------------- studio look
REPL = {"id": "r", "type": "background_replacement", "start": 0, "end": 3, "parameters": {"asset_id": "bg"}, "depends_on": ["k"]}


def _look(**params):
    return {"id": "l", "type": "studio_look", "start": 0, "end": 3, "parameters": {"preset": "none", **params}}


def test_studio_look_ambient_match_tints_the_subject_with_the_background_colour(prep, media):
    base = pixel(frame(prep, media, [KEY, REPL], 1.0), 160, 90)
    lit = pixel(frame(prep, media, [KEY, REPL, _look(ambient_match=1.0)], 1.0), 160, 90)
    bgc = pixel(frame(prep, media, [KEY, REPL], 1.0), 5, 5)                          # the new background's colour
    assert sum(abs(a - b) for a, b in zip(base, lit)) > 6, "subject did not change"
    # the subject moved toward the background's hue: its channel ratios now lean the way the background's do
    lean = lambda p: (p[0] - p[2])
    assert (lean(lit) - lean(base)) * (lean(bgc) - 128) >= 0 or abs(lean(lit) - lean(base)) > 2


def test_studio_look_without_integration_controls_leaves_the_composite_edges_alone(prep, media):
    """an all-zero look is a pass-through: the pipeline must not touch pixels when nothing is asked for"""
    a = pixel(frame(prep, media, [KEY, REPL], 1.0), 160, 90)
    b = pixel(frame(prep, media, [KEY, REPL, _look()], 1.0), 160, 90)
    assert max(abs(x - y) for x, y in zip(a, b)) <= 1


def test_studio_look_global_grade_vignette_darkens_corners_not_centre(prep, media):
    plain = frame(prep, media, [], 1.0); look = frame(prep, media, [_look(vignette=1.0)], 1.0)
    assert sum(pixel(look, 2, 2)) < sum(pixel(plain, 2, 2)) - 30
    assert max(abs(x - y) for x, y in zip(pixel(look, 160, 90), pixel(plain, 160, 90))) <= 3


def test_studio_look_only_inside_its_time_range(prep, media):
    ops = [{"id": "l", "type": "studio_look", "start": 1.0, "end": 2.0, "parameters": {"preset": "studio_cool"}}]
    a, b = pixel(frame(prep, media, ops, 0.5), 80, 60), pixel(frame(prep, media, [], 0.5), 80, 60)
    c, d = pixel(frame(prep, media, ops, 1.5), 80, 60), pixel(frame(prep, media, [], 1.5), 80, 60)
    assert a == b and sum(abs(x - y) for x, y in zip(c, d)) > 10


def test_studio_look_binding_and_validation(prep):
    codes = lambda ops: ({e.code for e in prep(bp(ops=ops, assets=ASSETS)).report.errors}, {w.code for w in prep(bp(ops=ops, assets=ASSETS)).report.warnings})
    assert "STUDIO_NO_BACKGROUND" in codes([_look()])[1]                                    # global grade only: said out loud
    assert "STUDIO_NO_BACKGROUND" not in codes([KEY, REPL, _look()])[1]
    assert "OVERLAP_CONFLICT" in codes([KEY, REPL, _look(), {**_look(), "id": "l2"}])[0]
    assert "INVALID_PARAMETER" in codes([{"id": "l", "type": "studio_look", "start": 0, "parameters": {"preset": "nope"}}])[0]
    assert "STUDIO_RANGE_DIFFERS" in codes([KEY, REPL, {**_look(), "end": 1.0}])[1]


def test_studio_look_presets_resolve_and_overrides_win():
    from evp.studio import resolve, PRESETS
    assert resolve({"preset": "studio_warm"})["glow"] == PRESETS["studio_warm"]["glow"]
    assert resolve({"preset": "studio_warm", "glow": 0.0})["glow"] == 0.0
    assert resolve({"preset": "none"})["vignette"] == 0.0


def test_match_curves_move_the_picture_toward_the_reference_and_are_inert_at_zero():
    from evp.studio import match_curves
    rng = np.random.default_rng(0)
    frame = np.clip(rng.normal(90, 30, (80, 80, 3)), 0, 255).astype(np.uint8)
    ref = np.clip(rng.normal(150, 30, (60, 90, 3)) + [20, 0, -20], 0, 255).astype(np.uint8)       # brighter, warmer, other size
    for pts in match_curves(frame, ref, 0.0): assert all(abs(x - y) < 1e-6 for x, y in pts)
    cv = match_curves(frame, ref, 1.0, max_shift=1.0)
    for ch in range(3):
        xs, ys = zip(*cv[ch]); out = np.interp(frame[..., ch] / 255.0, xs, ys) * 255
        assert abs(out.mean() - ref[..., ch].mean()) < abs(frame[..., ch].mean() - ref[..., ch].mean()) / 4
        assert all(b >= a for a, b in zip(ys, ys[1:]))                                       # monotonic: never inverts tones


def test_studio_look_reference_picture_pulls_the_render_toward_it(prep, media):
    from evp.security import PathPolicy  # noqa
    assets = [*ASSETS, {"id": "ref", "type": "image", "path": "bg.png"}]
    off = frame(prep, media, [_look(contrast=0.1)], 1.0, assets=assets)
    on = frame(prep, media, [_look(contrast=0.1, reference_asset_id="ref", reference_strength=1.0)], 1.0, assets=assets)
    ref = cv2.imread(str(media["dir"] / "bg.png"))[:, :, ::-1].reshape(-1, 3).mean(0)
    mean = lambda png: cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1].reshape(-1, 3).mean(0)
    assert np.abs(mean(on) - ref).sum() < np.abs(mean(off) - ref).sum()


# ---------------------------------------------------------------- subject transform + cast shadow
def _repl(**params):
    return {**REPL, "parameters": {"asset_id": "bg", **params}}


def test_subject_can_be_moved_and_resized(prep, media):
    base = frame(prep, media, [KEY, _repl()], 1.0)
    moved = frame(prep, media, [KEY, _repl(subject_x=0.25, subject_scale=1.3)], 1.0)
    is_subject = lambda p: p[0] > 150 and p[1] > 100 and p[2] < 160 and p[0] > p[2]          # the disc is orange-ish
    assert is_subject(pixel(base, 160, 90)) and not is_subject(pixel(base, 250, 90))
    assert is_subject(pixel(moved, 250, 90))                                                    # now there
    assert pixel(moved, 5, 5) == pixel(base, 5, 5) or max(abs(a - b) for a, b in zip(pixel(moved, 5, 5), pixel(base, 5, 5))) <= 2   # background untouched


def test_default_subject_settings_change_nothing(prep, media):
    a = pixel(frame(prep, media, [KEY, _repl()], 1.0), 160, 90)
    b = pixel(frame(prep, media, [KEY, _repl(subject_scale=1.0, subject_x=0.0, shadow=0.0)], 1.0), 160, 90)
    assert a == b


def test_cast_shadow_only_darkens_the_background_beside_the_subject(prep, media):
    off = frame(prep, media, [KEY, _repl()], 1.0)
    on = frame(prep, media, [KEY, _repl(shadow=0.8, shadow_blur=6, shadow_dx=0.12, shadow_dy=0.0)], 1.0)
    o = cv2.imdecode(np.frombuffer(off, np.uint8), 1).astype(int); n = cv2.imdecode(np.frombuffer(on, np.uint8), 1).astype(int)
    diff = (n - o).sum(2)
    assert diff.min() < -60, "no shadow appeared"
    assert (diff > 12).sum() < 0.002 * diff.size, "a shadow must never brighten the picture"
    assert max(abs(a - b) for a, b in zip(pixel(on, 160, 90), pixel(off, 160, 90))) <= 3       # the subject itself is not darkened
    assert np.abs(diff[:4, :4]).max() <= 3                                                      # far corner untouched: no veil from the padding


def test_subject_edge_visible_warning(prep):
    def codes(**params):
        return {w.code for w in prep(bp(ops=[KEY, _repl(**params)], assets=ASSETS)).report.warnings}
    assert "SUBJECT_EDGE_VISIBLE" in codes(subject_scale=0.8)
    assert "SUBJECT_EDGE_VISIBLE" in codes(subject_scale=1.05, subject_x=0.2)
    assert "SUBJECT_EDGE_VISIBLE" not in codes(subject_scale=1.3, subject_x=0.1)
    assert "SUBJECT_EDGE_VISIBLE" not in codes()


# ---------------------------------------------------------------- auto match
def _scene(light_left=True, dark=False, warm=True):
    H, W = 120, 200
    x = np.linspace(0, 1, W)[None, :, None] * np.ones((H, 1, 1))
    base = (0.6 - 0.4 * x if light_left else 0.2 + 0.4 * x) * (0.4 if dark else 1.0)
    rgb = np.clip(np.concatenate([base * (1.2 if warm else .8), base, base * (0.7 if warm else 1.2)], 2) * 255, 0, 255).astype(np.uint8)
    mask = np.zeros((H, W), bool); mask[30:120, 70:130] = True
    return rgb, mask


def test_auto_analysis_reads_the_scene_like_a_colourist():
    from evp.studio import analyze
    a, m = _scene(light_left=True, warm=True); a[m] = (90, 70, 60)
    r = analyze(a, m)
    assert r["shadow_dx"] > 0                                   # light on the left -> shadow falls right
    assert int(r["highlight_tint"][1:3], 16) > int(r["highlight_tint"][5:7], 16)      # warm light colour (R > B)
    b, m2 = _scene(light_left=False, dark=True, warm=False); b[m2] = (200, 160, 150)
    r2 = analyze(b, m2)
    assert r2["shadow_dx"] < 0 and r2["vignette"] > r["vignette"]                    # darker room -> stronger vignette
    assert r2["subject_exposure"] < 0 and r["subject_exposure"] >= 0                 # a subject far brighter than the room is pulled down
    for res in (r, r2):
        assert 0.1 <= res["contrast"] <= 0.35 and res["ambient_match"] <= 0.18 and res["tint_amount"] <= 0.15   # conservative by design


def test_skin_guard_measures_hue_shift():
    from evp.studio import skin_shift
    skin = np.full((80, 80, 3), (190, 130, 110), np.uint8); mask = np.ones((80, 80), bool)
    assert skin_shift(skin, skin, mask)[0] < 0.5
    warm = np.full_like(skin, (215, 125, 80))
    deg, ratio = skin_shift(skin, warm, mask)
    assert deg > 3 and ratio > 1
    assert skin_shift(np.zeros_like(skin), np.zeros_like(skin), mask) is None          # no skin -> nothing to guard


def test_studio_look_auto_match_fills_in_settings_and_explicit_values_win(prep, media):
    p = prep(bp(ops=[KEY, REPL, _look(auto_match=1.0)], assets=ASSETS))
    assert p.plan, p.report.errors
    render_frame(p.plan, media["video"], 1.0, None, str(media["dir"]))
    auto = p.plan.look_auto["l"]
    assert {"contrast", "vignette", "highlight_tint", "measured"} <= set(auto) and "light_side_x" in auto["measured"]
    assert any(s["auto"] for s in p.plan.describe() if s["id"] == "l")                  # reported to the user
    from evp.studio import resolve
    assert resolve(p.plan.steps[-1].params, auto)["vignette"] == auto["vignette"]
    assert resolve({**p.plan.steps[-1].params, "vignette": 0.0}, auto)["vignette"] == 0.0
    assert resolve({"preset": "none", "auto_match": 0.0}, auto)["vignette"] == 0.0     # off = off
