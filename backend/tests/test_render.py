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
