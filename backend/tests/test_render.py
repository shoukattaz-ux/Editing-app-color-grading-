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


def _ld(prep, media, p, veil=False):
    import io
    import numpy as np
    from PIL import Image
    ops = [{"id": "v", "type": "color_grading", "start": 0, "end": 3, "parameters": {"curve_master": [[0, 0.2], [1, 1]]}}] if veil else []   # milky: blacks lifted
    if p:
        ops.append({"id": "g", "type": "color_grading", "start": 0, "end": 3, "parameters": p})
    return np.asarray(Image.open(io.BytesIO(frame(prep, media, ops, 1.0))).convert("RGB"), dtype=float)


def test_texture_changes_detail_and_keeps_colour(prep, media):
    import numpy as np
    base, up, down = _ld(prep, media, None), _ld(prep, media, {"texture": 1.0}), _ld(prep, media, {"texture": -1.0})
    hf = lambda a: np.abs(a[:, 1:] - a[:, :-1]).mean()
    assert hf(up) > hf(base) > hf(down)                                                  # + more detail, - less
    assert abs(up.mean(axis=(0, 1)) - base.mean(axis=(0, 1))).max() < 3                   # overall colour/brightness unchanged


def test_clarity_changes_local_contrast_keeps_colour(prep, media):
    import numpy as np
    base, up, down = _ld(prep, media, None), _ld(prep, media, {"clarity": 1.0}), _ld(prep, media, {"clarity": -1.0})
    lc = lambda a: np.abs(a - np.asarray(__import__("cv2").GaussianBlur(a, (0, 0), 6))).mean()   # deviation from the local average
    assert lc(up) > lc(base) > lc(down)
    assert abs(up.mean(axis=(0, 1)) - base.mean(axis=(0, 1))).max() < 3


def test_dehaze_removes_veil_and_negative_adds_it(prep, media):
    import numpy as np
    hazy, clear, hazier = (_ld(prep, media, {"dehaze": v} if v else None, veil=True).mean(axis=2) for v in (0.0, 1.0, -1.0))
    assert np.percentile(clear, 2) < np.percentile(hazy, 2) - 5 < np.percentile(hazier, 2)    # blacks deeper / lifted
    assert clear.std() > hazy.std() > hazier.std()                                              # contrast follows


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


# ---------------------------------------------------------------- professional screen key (mode "screen")
def _hair_scene(seed=3, W=320, H=240, noise=2.0):
    """ground truth: dark hair strands (1-2 px, partly transparent) + a face over an UNEVENLY lit green screen with a shadow patch"""
    rng = np.random.default_rng(seed)
    a = np.zeros((H, W), np.float32)
    cv2.ellipse(a, (W // 2, H // 2 + 20), (75, 95), 0, 0, 360, 1.0, -1)
    cv2.rectangle(a, (0, H - 60), (W, H), 1.0, -1)
    for _ in range(130):
        ang = rng.uniform(-np.pi * 0.95, -np.pi * 0.05); r0 = rng.uniform(85, 100); L = rng.uniform(8, 28)
        x0, y0 = W // 2 + np.cos(ang) * 73, H // 2 + 20 + np.sin(ang) * r0
        pts = [(x0 + np.cos(ang) * t, y0 + np.sin(ang) * t) for t in range(int(L))]
        cv2.polylines(a, [np.array(pts, np.int32)], False, float(rng.uniform(.5, 1)), int(rng.integers(1, 3)), cv2.LINE_AA)
    a = cv2.GaussianBlur(a, (0, 0), 0.8)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    F = np.zeros((H, W, 3), np.float32) + [40, 32, 28]
    F[H // 2 + 5:H // 2 + 85, W // 2 - 48:W // 2 + 48] = (200, 150, 125)
    F[H - 60:] = (35, 45, 85)
    light = 0.62 + 0.5 * (xx / W) - 0.25 * np.exp(-(((xx - 60) ** 2 + (yy - 75) ** 2) / (2 * 30 ** 2)))
    B = np.array([55, 185, 60], np.float32)[None, None, :] * light[..., None]
    I = a[..., None] * F + (1 - a[..., None]) * B + rng.normal(0, noise, (H, W, 3))
    return np.clip(I, 0, 255).astype(np.uint8), a, F


def _keyed_over(tmp_path, I, mode_params):
    """render the scene through the real pipeline over a known background; returns RGB float"""
    from evp.security import PathPolicy
    from evp.engine import prepare
    cv2.imwrite(str(tmp_path / "in.png"), I[:, :, ::-1])
    H, W = I.shape[:2]
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-loop", "1", "-framerate", "25", "-t", "1", "-i", str(tmp_path / "in.png"), "-vf", "format=yuv420p",
                    "-c:v", "libx264", "-qp", "0", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(tmp_path / "in.mp4")], check=True)
    bgim = np.zeros((H, W, 3), np.uint8); bgim[:] = (70, 90, 150)[::-1]; bgim[:, :, 0] = np.linspace(60, 200, W).astype(np.uint8)[None, :]
    cv2.imwrite(str(tmp_path / "bg.png"), bgim)
    ops = [{"id": "k", "type": "background_removal" if "mode" in mode_params else "chroma_key", "start": 0, "parameters": mode_params},
           {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "bg", "fit": "stretch"}, "depends_on": ["k"]}]
    p = prepare(bp(ops=ops, assets=[{"id": "bg", "type": "image", "path": "bg.png"}], output={"resolution": "source", "fps": "source"}),
                str(tmp_path / "in.mp4"), PathPolicy([tmp_path]), tmp_path)
    assert p.plan, [e.message for e in p.report.errors]
    png = render_frame(p.plan, str(tmp_path / "in.mp4"), 0.4, None, str(tmp_path))
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1].astype(np.float32), bgim[:, :, ::-1].astype(np.float32)


def test_screen_key_recovers_hair_on_uneven_screen_far_better_than_the_simple_key(tmp_path):
    I, a, F = _hair_scene()
    (tmp_path / "s").mkdir(); (tmp_path / "c").mkdir()
    new, bgf = _keyed_over(tmp_path / "s", I, {"mode": "screen"})
    old, _ = _keyed_over(tmp_path / "c", I, {"key_color": "#37b93c"})
    truth = a[..., None] * F + (1 - a[..., None]) * bgf
    band = cv2.dilate(((a > 0.02) & (a < 0.98)).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    e_new, e_old = np.abs(new - truth).mean(2)[band].mean(), np.abs(old - truth).mean(2)[band].mean()
    assert e_new < 9.0, e_new
    assert e_new < 0.6 * e_old, (e_new, e_old)                       # measured: ~5.5 vs ~21
    assert np.abs(new - truth).mean() < 3.0                           # and no haze/holes elsewhere (the shadow patch on the screen)


def test_screen_key_detects_blue_screens_and_needs_no_model(tmp_path):
    I, a, F = _hair_scene()
    blue = I[..., [0, 2, 1]]                                          # swap G<->B: the same scene on a blue screen
    Fb = F[..., [0, 2, 1]]
    out, bgf = _keyed_over(tmp_path, blue, {"mode": "screen"})
    truth = a[..., None] * Fb + (1 - a[..., None]) * bgf
    band = cv2.dilate(((a > 0.02) & (a < 0.98)).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    assert np.abs(out - truth).mean(2)[band].mean() < 10.0
    from evp.ml.screenkey import ScreenKeyer
    k = ScreenKeyer({"similarity": 0.12, "blend": 0.08, "auto_screen": True, "key_color": "#00ff00"}); k(blue)
    assert k.screen_name == "blue"


def test_screen_key_temporal_smoothing_calms_flicker_but_not_motion():
    from evp.ml.screenkey import ScreenKeyer
    P = dict(similarity=0.12, blend=0.08, auto_screen=True, key_color="#00ff00", fill_holes=False, edge_shrink=0, edge_soften=0,
             spill_suppression=True, spill_strength=0.5, edge_color_fix=False, matte_threshold=0.0, despeckle=False)
    frames = [_hair_scene(seed=3, noise=6.0)[0]]
    rng = np.random.default_rng(7)
    for _ in range(5): frames.append(np.clip(frames[0].astype(float) + rng.normal(0, 6, frames[0].shape), 0, 255).astype(np.uint8))      # same picture, new sensor noise
    def flicker(ts):
        k = ScreenKeyer({**P, "temporal_smooth": ts}); al = [k(f)[0] for f in frames]
        return np.mean([np.abs(al[i] - al[i - 1]).mean() for i in range(2, len(al))])
    assert flicker(1.0) < 0.7 * flicker(0.0)
    k = ScreenKeyer({**P, "temporal_smooth": 1.0}); k(frames[0])
    moved = np.roll(frames[0], 40, axis=1); a_moved = k(moved)[0]; a_ref = ScreenKeyer({**P, "temporal_smooth": 0.0})(moved)[0]
    assert np.abs(a_moved - a_ref).max() < 0.25                       # a real change passes through (no ghost trail)


def test_screen_key_validation_and_unsupported_op_message(prep):
    r = prep(bp(ops=[{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "edge_shrink": 9}}]))
    assert any(e.code == "INVALID_PARAMETER" for e in r.report.errors)
    h = prep(bp(ops=[{"id": "h", "type": "hair_edge_refinement", "start": 0}]))
    assert any("screen" in e.message for e in h.report.errors)         # tells you where hair refinement lives now


def test_preview_after_auto_measuring_uses_the_matte_of_its_own_frame(tmp_path):
    """Regression: auto_match / reference measuring renders another frame (the look's middle) and used to leave THAT frame's matte
    in the plan, so the first preview of any other frame came out wrong (dark, wrong matte). First render must equal a later one."""
    from evp.security import PathPolicy
    from evp.engine import prepare
    I, a, F = _hair_scene(W=160, H=120)
    cv2.imwrite(str(tmp_path / "in.png"), I[:, :, ::-1])
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-loop", "1", "-framerate", "25", "-t", "2", "-i", str(tmp_path / "in.png"), "-vf", "format=yuv420p",
                    "-c:v", "libx264", "-qp", "0", str(tmp_path / "in.mp4")], check=True)
    bgim = np.full((120, 160, 3), (150, 90, 70), np.uint8); cv2.imwrite(str(tmp_path / "bg.png"), bgim)
    ops = [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}},
           {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "bg", "fit": "stretch"}, "depends_on": ["k"]},
           {"id": "l", "type": "studio_look", "start": 0, "parameters": {"preset": "none", "auto_match": 1.0}}]
    p = prepare(bp(ops=ops, assets=[{"id": "bg", "type": "image", "path": "bg.png"}], output={"resolution": "source", "fps": "source"}),
                str(tmp_path / "in.mp4"), PathPolicy([tmp_path]), tmp_path)
    assert p.plan, [e.message for e in p.report.errors]
    dec = lambda png: cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).astype(float)
    first = dec(render_frame(p.plan, str(tmp_path / "in.mp4"), 0.12, None, str(tmp_path)))          # computes the auto settings (measures at ~1 s)
    again = dec(render_frame(p.plan, str(tmp_path / "in.mp4"), 0.12, None, str(tmp_path)))
    assert np.abs(first - again).mean() < 1.0, np.abs(first - again).mean()
    assert 40 < first.mean() < 200


def test_solidity_makes_semi_transparent_areas_solid_but_keeps_clear_areas_clear():
    from evp.ml.matting import solidify
    a = np.array([[0.0, 0.1, 0.3, 0.6, 0.8, 1.0]], np.float32)
    assert np.array_equal(solidify(a, 0.0), a)                                              # off = untouched
    s = solidify(a, 0.6)[0]
    assert s[0] == 0 and s[-1] == 1 and s[1] == 0                                           # clear stays clear, solid stays solid
    assert s[3] > 0.8 and s[4] >= 0.99                                                       # a 60% finger becomes (nearly) solid
    assert all(s[i] <= s[i + 1] for i in range(len(s) - 1))                                 # monotonic: never inverts
    assert solidify(a, 1.0)[0][3] >= s[3]                                                   # stronger setting = more solid


def test_screen_key_leaves_no_green_on_motion_blurred_skin(tmp_path):
    """Regression: a blurred finger over the screen kept a lime-green outline (skin + green mixture). Over a magenta background no
    green-dominant pixel may remain, and untouched skin must keep its colour."""
    H, W = 240, 320
    I = np.zeros((H, W, 3), np.float32) + [60, 190, 70]                   # the screen
    skin = np.zeros((H, W), np.float32); cv2.rectangle(skin, (60, 40), (110, 200), 1.0, -1); skin = cv2.GaussianBlur(skin, (0, 0), 7)    # motion-blurred finger
    solid = np.zeros((H, W), np.float32); cv2.rectangle(solid, (200, 40), (280, 200), 1.0, -1); solid = cv2.GaussianBlur(solid, (0, 0), 0.8)
    skin_rgb = np.array([205, 150, 120], np.float32)
    hat = np.zeros((H, W), np.float32); cv2.rectangle(hat, (112, 20), (160, 220), 1.0, -1); hat = cv2.GaussianBlur(hat, (0, 0), 0.8)
    hat_rgb = np.array([55, 72, 58], np.float32)                           # dark grey-green: the beanie with a little spill on it
    I = I * (1 - skin[..., None]) + skin_rgb * skin[..., None]
    I = I * (1 - hat[..., None]) + hat_rgb * hat[..., None]
    I = I * (1 - solid[..., None]) + skin_rgb * solid[..., None]
    I = np.clip(I + np.random.default_rng(1).normal(0, 1.5, I.shape), 0, 255).astype(np.uint8)
    from evp.ml.screenkey import ScreenKeyer
    P = dict(similarity=0.12, blend=0.08, auto_screen=True, key_color="#00ff00", fill_holes=False, edge_shrink=0, edge_soften=0, temporal_smooth=0.0,
             spill_suppression=True, spill_strength=0.5, edge_color_fix=True, matte_threshold=0.0, despeckle=False)
    a, fg = ScreenKeyer(P)(I)
    comp = fg * a[..., None] + np.array([1.0, 0.0, 1.0], np.float32) * (1 - a[..., None])          # over magenta
    rgb = comp * 255
    green = (rgb[..., 1] > rgb[..., 0] + 8) & (rgb[..., 1] > rgb[..., 2] + 8) & (a > 0.15)
    assert green.sum() < 15, f"{int(green.sum())} green-dominant pixels left on the blurred finger"
    core = (fg[130:150, 215:265] * 255).mean((0, 1))
    assert np.abs(core - skin_rgb).max() < 6, f"solid skin colour changed: {core}"                    # skin is not shifted by despill


def test_screen_key_treats_a_shadowed_part_of_the_screen_as_screen():
    """Regression: the dim lower-left corner of a real green sheet (in shadow) was judged by absolute greenness, so it came out half solid
    - a olive ribbon around the subject. Greenness must be judged relative to brightness."""
    H, W = 240, 320
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    light = np.where((xx < 130) & (yy > 100), 0.42, 1.0).astype(np.float32)               # a deep shadow over the lower-left of the sheet
    light = cv2.GaussianBlur(light, (0, 0), 6)
    I = np.array([95, 205, 45], np.float32)[None, None, :] * light[..., None]
    subj = np.zeros((H, W), np.float32); cv2.rectangle(subj, (140, 50), (260, 230), 1.0, -1); subj = cv2.GaussianBlur(subj, (0, 0), 0.8)
    I = I * (1 - subj[..., None]) + np.array([200, 140, 110], np.float32) * subj[..., None]
    I = np.clip(I + np.random.default_rng(3).normal(0, 1.2, I.shape), 0, 255).astype(np.uint8)
    from evp.ml.screenkey import ScreenKeyer
    P = dict(similarity=0.12, blend=0.08, auto_screen=True, key_color="#00ff00", fill_holes=False, edge_shrink=0, edge_soften=0, temporal_smooth=0.0,
             spill_suppression=True, spill_strength=0.5, edge_color_fix=True, matte_threshold=0.0, despeckle=False)
    a, _ = ScreenKeyer(P)(I)
    shadow = (xx < 110) & (yy > 120)
    assert a[shadow].mean() < 0.05, f"the shadowed screen came out {a[shadow].mean():.2f} solid"
    assert a[(xx > 160) & (xx < 240) & (yy > 80) & (yy < 200)].min() > 0.9               # the subject stays solid


# ---------------------------------------------------------------- transparent export (ProRes 4444 / WebM VP9 alpha)
def _export_transparent(prep, media, tmp_path, codec, fmt, key=None):
    from evp.security import PathPolicy  # noqa
    ops = [key or {"id": "k", "type": "chroma_key", "start": 0, "parameters": {"edge_feather": 0.5}}]
    p = prep(bp(ops=ops, output={"codec": codec, "format": fmt, "crf": 18, "preset": "ultrafast"}))
    assert p.plan, p.report.errors
    assert p.plan.transparent_key == ops[0]["id"]
    out = tmp_path / f"t.{fmt}"
    j = Job(p.plan, media["video"], str(out), RenderOptions(), str(tmp_path)); j.run()
    assert j.state == "done", j.error
    return out


def _rgba_frame(path, decoder=None):
    cmd = [ffmpeg_bin(), "-v", "error", *(["-c:v", decoder] if decoder else []), "-i", str(path), "-vf", "select=eq(n\\,10)", "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    meta = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True).stdout)["streams"][0]
    return np.frombuffer(raw, np.uint8).reshape(meta["height"], meta["width"], 4)


def test_transparent_export_prores4444_has_a_real_alpha_channel(prep, media, tmp_path):
    out = _export_transparent(prep, media, tmp_path, "prores4444", "mov")
    v = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,profile", "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"][0]
    assert v["codec_name"] == "prores" and v["pix_fmt"].startswith("yuva444p"), v
    f = _rgba_frame(out)
    assert f[2, 2, 3] == 0 and f[-3, -3, 3] == 0, "the green screen must be fully transparent"
    h, w = f.shape[:2]
    cx = f[h // 2, w // 2]
    assert cx[3] == 255 and cx[0] > 150 and cx[2] < 160, f"the subject must be solid and keep its colour: {cx}"      # orange disc
    bad = (f[..., 3] > 200) & (f[..., 1].astype(int) > f[..., 0].astype(int) + 40) & (f[..., 1].astype(int) > f[..., 2].astype(int) + 40)     # int: uint8 would wrap around
    assert not bad.any(), f"green left in {int(bad.sum())} solid pixels"


def test_transparent_export_webm_vp9_alpha(prep, media, tmp_path):
    out = _export_transparent(prep, media, tmp_path, "vp9_alpha", "webm")
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream_tags=alpha_mode", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert tags == "1", f"WebM must be flagged as having alpha: {tags!r}"
    f = _rgba_frame(out, decoder="libvpx-vp9")                      # FFmpeg's native VP9 decoder ignores the alpha plane: use libvpx
    assert f[2, 2, 3] == 0 and f[f.shape[0] // 2, f.shape[1] // 2, 3] == 255


def test_transparent_export_rules_and_preview_checkerboard(prep, media, tmp_path):
    codes = lambda ops, codec="prores4444", fmt="mov": {e.code for e in prep(bp(ops=ops, assets=ASSETS, output={"codec": codec, "format": fmt})).report.errors}
    assert "TRANSPARENT_WITH_BACKGROUND" in codes([KEY, REPL])                                       # a background and transparency are exclusive
    assert "TRANSPARENT_NEEDS_ONE_KEY" in codes([])
    assert "TRANSPARENT_FULL_RANGE" in codes([{**KEY, "start": 1.0, "end": 2.0}])
    assert "INVALID_PARAMETER" in codes([KEY], "prores4444", "mp4") and "INVALID_PARAMETER" in codes([KEY], "vp9_alpha", "mov")
    assert not codes([KEY])                                                                          # a key alone is fine
    p = prep(bp(ops=[KEY], output={"codec": "prores4444", "format": "mov"}))
    png = render_frame(p.plan, media["video"], 1.0, None, str(media["dir"]))                         # preview: checkerboard behind the subject
    im = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).astype(int)
    a, b = im[2, 2], im[2, 20]
    assert a.std() < 8 and abs(int(a[0]) - int(b[0])) > 20, "the preview should show a grey checkerboard where the screen was"


def test_screen_keyer_fast_colour_field_matches_the_exact_blur():
    """_nearest_solid blurs a shrunken copy (7x faster at 720p). Where the picture is visible the result must equal the full-resolution blur."""
    import cv2, numpy as np
    from evp.ml.screenkey import ScreenKeyer
    rng = np.random.default_rng(3)
    h, w = 360, 640
    f = cv2.GaussianBlur(rng.random((h, w, 3)).astype(np.float32), (0, 0), 6)
    solid = np.zeros((h, w), np.float32); cv2.circle(solid, (320, 180), 110, 1.0, -1); solid = cv2.GaussianBlur(solid, (0, 0), 1.5)
    near, got = ScreenKeyer._nearest_solid(f, solid, h, w)
    sig = max(3.0, min(h, w) / 120)
    ref = np.zeros_like(f); rg = np.zeros((h, w, 1), bool)
    for sg in (sig, sig * 4, sig * 16):                                   # the original, exact computation
        den = cv2.GaussianBlur(solid, (0, 0), sg)[..., None]
        cand = cv2.GaussianBlur(f * solid[..., None], (0, 0), sg) / np.maximum(den, 1e-4)
        use = (den > 0.02) & ~rg; ref = np.where(use, cand, ref); rg |= den > 0.02
    vis = solid > 0.01
    assert np.abs(near - np.clip(ref, 0, 1))[vis].max() < 0.02 and (got == rg)[vis].all()
