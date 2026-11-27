"""Blueprint Execution Engine: validate -> plan -> compile -> run (progress/pause/resume/cancel/retry)."""
from __future__ import annotations
from . import fsutil
from . import proc
import os, shutil, signal, subprocess, tempfile, threading, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .blueprint import parse_blueprint, Report, VideoInfo
from .repair import parse_lenient
from .compiler import compile_plan, RenderOptions, Compiled
from .media import probe, MediaError, ffmpeg_bin
from .planner import build_plan, Plan
from .security import PathPolicy, safe_output_path, PathError


@dataclass
class Prepared:
    report: Report
    plan: Plan | None
    video: VideoInfo | None
    repaired: dict | None = None        # the blueprint after lenient repair (None when nothing was repaired / strict)

    def to_json(self) -> dict:
        d = self.report.to_json()
        d["plan"] = self.plan.describe() if self.plan else []
        d["video"] = self.video.__dict__ if self.video else None
        d["repaired"] = self.repaired
        bp = self.report.blueprint
        d["timeline"] = None
        if bp is not None:
            off, clips = 0, []
            fps = bp.output.fps
            for c in bp.clips:
                n = c.end_frame - c.start_frame
                clips.append({"id": c.id, "label": c.label, "source_start": c.start, "source_end": c.end,
                              "timeline_start": off / fps, "timeline_end": (off + n) / fps})
                off += n
            d["timeline"] = {"duration": bp.timeline_duration, "cut": bool(bp.clips), "clips": clips}
        d["ok"] = bool(self.report.ok and self.plan)
        return d


def prepare(blueprint: Any, source: str | None, policy: PathPolicy, base_dir: Path | None = None,
            video: VideoInfo | None = None, strict: bool | None = None) -> Prepared:
    """Steps 1-6 of the engine: parse, validate, resolve dependencies, order."""
    if video is None and source:
        try:
            video = probe(source)
        except MediaError as e:
            rep = Report(); rep.err("BAD_SOURCE", str(e))
            return Prepared(rep, None, None)
    repaired = None
    if strict is None: strict = os.environ.get("EVP_STRICT") == "1"      # the validation test-suite pins the strict validator
    if strict:
        rep = parse_blueprint(blueprint, video, policy, base_dir)
    else:                                  # lenient intake: repair what can be repaired, set aside what cannot, report every change
        box: list = []
        def hook(rp):
            if video: box[:] = [build_plan(rp.blueprint, video, rp)]
        rep, repaired = parse_lenient(blueprint, video, policy, base_dir, hook)
        return Prepared(rep, box[0] if (rep.ok and box) else None, video, repaired)
    plan = None
    if rep.ok and video:
        plan = build_plan(rep.blueprint, video, rep)
    return Prepared(rep, plan, video, repaired)


def ensure_auto(plan: Plan, source: str, cache_dir: str) -> None:
    """studio_look with auto_match: render the composite without any look, find the person by rendering it over magenta, measure, keep the settings."""
    looks = [o for o in plan.steps if o.type == "studio_look" and (o.params.get("auto_match", 0) > 0 or o.params.get("camera_match", 0) > 0)]
    if not looks: return
    import hashlib, json as _json, cv2
    import numpy as np
    from .studio import analyze
    plan.look_auto = {}
    for lk in looks:
        repl = next((o for o in plan.steps if o.type == "background_replacement" and plan.look_for.get(o.id) is lk), None)
        mtimes = [Path(a.resolved).stat().st_mtime_ns for a in plan.blueprint.assets.values() if a.resolved and Path(a.resolved).is_file()]
        ops_sig = [(o.id, o.type, o.start_frame, o.end_frame, repr(sorted(o.params.items())), repr(sorted(o.keyframes))) for o in plan.steps if o.type != "studio_look"]
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, mtimes, lk.id, ops_sig, plan.edl, "auto2"], default=str).encode()).hexdigest()[:24]
        f = Path(cache_dir) / "look" / f"auto_{key}.json"
        if f.is_file():
            plan.look_auto[lk.id] = _json.loads(f.read_text(encoding="utf-8")); continue
        t = ((lk.start_frame + lk.end_frame) // 2) / plan.blueprint.output.fps
        dec = lambda png: cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        plan.measuring = True
        try:
            comp = dec(render_frame(plan, source, t, None, cache_dir, _measuring=True))
            mask = np.zeros(comp.shape[:2], bool)
            if repl is not None:                           # the person = everything that is not the magenta stand-in background
                saved = dict(repl.params)
                repl.params.update(asset_id=None, color="#ff00ff", shadow=0.0)
                try: pink = dec(render_frame(plan, source, t, None, cache_dir, _measuring=True))
                finally: repl.params.clear(); repl.params.update(saved)
                mask = np.abs(pink.astype(int) - [255, 0, 255]).sum(2) > 120
        finally:
            plan.measuring = False
        res = analyze(np.ascontiguousarray(comp), mask)
        plan.look_auto[lk.id] = res
        if mask.any() and lk.params.get("auto_match", 0) > 0:
            # skin guard: render with the derived look; if skin tones moved more than a colourist would allow, ease off the colour-casting controls
            from .studio import skin_shift
            k = 1.0
            for _ in range(3):
                after = dec(render_frame(plan, source, t, None, cache_dir, _measuring=True))
                sh = skin_shift(comp, after, mask)
                if sh is None: break
                deg, ratio = sh
                res["measured"]["skin_hue_shift_deg"] = round(deg, 1); res["measured"]["skin_chroma_ratio"] = round(ratio, 2)
                if deg <= 5.0 and 0.85 <= ratio <= 1.2: break
                k *= max(0.3, min(0.8, 5.0 / max(deg, 5.0)))
                for key in ("ambient_match", "tint_amount"): res[key] = round(res[key] * 0.6, 3)
                res["saturation"] = round(1 + (res["saturation"] - 1) * 0.6, 3)
                res["measured"]["skin_guard_steps"] = res["measured"].get("skin_guard_steps", 0) + 1
                plan.look_auto[lk.id] = res
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(_json.dumps(res), encoding="utf-8")
        plan.look_auto[lk.id] = res


def ensure_enhance(plan: Plan, source: str, cache_dir: str) -> None:
    """auto enhance: measure what each Color Grading will change - three frames of its range. With a removed background that is the finished
    picture (without this grade), or only its subject / only its background when the grade is set to those; otherwise the source as cut. Cached."""
    import hashlib, json as _json
    import numpy as np
    from .enhance import analyze
    from .ml.frames import decode
    plan.enhance = {}
    ops = [o for o in plan.steps if o.type == "color_grading" and float(o.params.get("auto_enhance", 0) or 0) > 0]
    if not ops: return
    out = plan.blueprint.output
    w = min(480, out.width) // 2 * 2; h = max(2, int(round(out.height * w / out.width)) // 2 * 2)
    # measure the picture as it reaches the grade: whenever anything else runs (a key, Smart HDR, portrait blur, other grades ...) render it
    # without this grade; only a grade on its own can read the source directly (faster)
    keyed = any(o.type != "audio_gain" and o.type != "text_overlay" for o in plan.steps if o not in ops) or len(ops) > 1
    for op in ops:
        part = op.params.get("apply_to", "both")
        rep = next((r for r in plan.steps if r.type == "background_replacement" and op in plan.grade_for.get(r.id, [])), None)
        part = part if rep is not None else "both"
        others = [(o.id, o.type, o.start_frame, o.end_frame, repr(sorted(o.params.items())), repr(sorted(o.keyframes))) for o in plan.steps if o is not op]
        mtimes = [Path(a.resolved).stat().st_mtime_ns for a in plan.blueprint.assets.values() if a.resolved and Path(a.resolved).is_file()]
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, op.start_frame, op.end_frame, plan.edl, w, part, keyed and others, keyed and mtimes, "enh13"], default=str).encode()).hexdigest()[:24]
        f = Path(cache_dir) / "look" / f"enhance_{key}.json"
        if f.is_file():
            plan.enhance[op.id] = _json.loads(f.read_text(encoding="utf-8")); continue
        n = max(1, op.end_frame - op.start_frame)
        times = [(op.start_frame + min(n - 1, int(n * q))) for q in (0.2, 0.5, 0.8)]
        frames, masks = [], None
        if not keyed:
            for fr in times: frames += list(decode(source, w, h, out.fps, fr, 1, plan.edl))
        else:
            import cv2
            dec = lambda png: cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
            saved_steps, saved_for = plan.steps, plan.grade_for
            plan.steps = [o for o in saved_steps if o is not op]                 # measure the picture WITHOUT this grade (no double counting)
            plan.grade_for = {k: [o for o in v if o is not op] for k, v in saved_for.items()}
            try:
                masks = [] if part != "both" else None
                for fr in times:
                    t = fr / out.fps
                    frames.append(np.ascontiguousarray(dec(render_frame(plan, source, t, w, cache_dir, _measuring=True))))
                    if part != "both":                                          # where is the subject? render it over magenta
                        saved = dict(rep.params)
                        rep.params.update(asset_id=None, color="#ff00ff", shadow=0.0, reflection=0.0, contact_shadow=0.0)
                        try: pink = dec(render_frame(plan, source, t, w, cache_dir, _measuring=True))
                        finally: rep.params.clear(); rep.params.update(saved)
                        subj = np.abs(pink.astype(int) - [255, 0, 255]).sum(2) > 120
                        masks.append(subj if part == "subject" else ~subj)
            finally:
                plan.steps, plan.grade_for = saved_steps, saved_for
        if not frames: continue
        try: res = analyze(frames, masks)
        except ValueError: continue
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(_json.dumps(res), encoding="utf-8")
        plan.enhance[op.id] = res


def ensure_floor(plan: Plan, source: str, cache_dir: str) -> None:
    """background replacements with a reflection / contact shadow and no floor_y: find the floor = the lowest point of the subject's matte on the middle frame"""
    import hashlib, json as _json
    from .floorfx import measure_bottom, placed_y
    plan.floor = {}
    reps = [o for o in plan.steps if o.type == "background_replacement" and (o.params["reflection"] > 0 or o.params["contact_shadow"] > 0) and o.params.get("floor_y") is None]
    for r in reps:
        sig = [(o.id, o.type, o.start_frame, o.end_frame, repr(sorted(o.params.items())), repr(sorted(o.keyframes))) for o in plan.steps if o.type in ("chroma_key", "background_removal", "matte_mask")]
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, r.id, r.start_frame, r.end_frame, repr(sorted((k, v) for k, v in r.params.items() if k.startswith("subject_"))), sig, plan.edl, "floor1"], default=str).encode()).hexdigest()[:24]
        f = Path(cache_dir) / "floor" / f"floor_{key}.json"
        if f.is_file():
            plan.floor[r.id] = _json.loads(f.read_text(encoding="utf-8")); continue
        t = ((r.start_frame + r.end_frame) // 2) / plan.blueprint.output.fps
        png = render_frame(plan, source, t, 360, cache_dir, _measuring=True, view="alpha")
        a = _gray_png(png)
        frac, _ = measure_bottom(a)
        y = placed_y(frac, r.params)
        res = {"y": round(float(min(1.0, max(0.0, y))), 4), "cut": bool(y >= 0.985 or frac >= 0.995), "raw": round(float(frac), 4)}
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(_json.dumps(res), encoding="utf-8")
        plan.floor[r.id] = res


def _gray_png(png: bytes):
    """a rendered PNG as a 2-D uint8 gray array (OpenCV: already a dependency; Pillow is not installed by the app)"""
    import cv2
    import numpy as np
    a = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if a is None: raise RuntimeError("could not read the measuring frame")
    return a


def _subject_bounds(plan: Plan, r, source: str, cache_dir: str, t: float) -> dict | None:
    """the placed subject's matte bounds at time t as final-frame fractions {x0,x1,y0,y1}, or None when there is no subject"""
    import numpy as np
    a = _gray_png(render_frame(plan, source, t, 360, cache_dir, _measuring=True, view="alpha")).astype(np.float32) / 255.0
    h, w = a.shape
    cols, rows = np.where((a > 0.5).mean(0) > 0.004)[0], np.where((a > 0.5).mean(1) > 0.004)[0]
    if len(cols) == 0 or len(rows) == 0: return None
    s, dx, dy = float(r.params.get("subject_scale", 1.0)), float(r.params.get("subject_x", 0.0)), float(r.params.get("subject_y", 0.0))
    left, top = (1.0 - s) / 2.0 + dx, ((1.0 - s) if r.params.get("subject_anchor", "bottom") == "bottom" else (1.0 - s) / 2.0) + dy
    return {"x0": round(left + cols.min() / w * s, 4), "x1": round(left + (cols.max() + 1) / w * s, 4), "y0": round(top + rows.min() / h * s, 4), "y1": round(top + (rows.max() + 1) / h * s, 4)}


def smooth_track(track: list[list[float]]) -> list[list[float]]:
    """[[t, x0, x1, y0, y1], ...] -> the same with each coordinate averaged with its neighbours (a steady light, not a jittery one)"""
    out = []
    for n, row in enumerate(track):
        a, b = track[max(0, n - 1)], track[min(len(track) - 1, n + 1)]
        out.append([row[0]] + [round(0.25 * a[i] + 0.5 * row[i] + 0.25 * b[i], 4) for i in range(1, 5)])
    return out


def ensure_light_box(plan: Plan, source: str, cache_dir: str) -> None:
    """colour lights: where is the subject? fixed = its matte bounds on the middle frame; follow = its bounds at up to 24 moments through the clip (the light then travels with it).
    Placed through subject_scale / x / y, so a light from the left really starts at the subject's left edge. zone = nothing to measure (your own rectangle)."""
    import hashlib, json as _json
    plan.light_box = {}
    def _wants(lk) -> bool:                                  # a measured box is needed unless everything is the user's own fixed rectangle
        return lk.params.get("colour_light_place", "fixed") != "zone" or any(z.get("follow") for z in (lk.params.get("colour_zones") or []))
    for r in [o for o in plan.steps if o.type == "background_replacement" and plan.look_for.get(o.id) and plan.look_for[o.id].params.get("colour_light", 0) > 0 and _wants(plan.look_for[o.id])]:
        lk_p = plan.look_for[r.id].params
        place = "follow" if (lk_p.get("colour_light_place", "fixed") == "follow" or any(z.get("follow") for z in (lk_p.get("colour_zones") or []))) else lk_p.get("colour_light_place", "fixed")
        fps = plan.blueprint.output.fps
        sig = [(o.id, o.type, o.start_frame, o.end_frame, repr(sorted(o.params.items())), repr(sorted(o.keyframes))) for o in plan.steps if o.type in ("chroma_key", "background_removal", "matte_mask")]
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, r.id, r.start_frame, r.end_frame, repr(sorted((k, v) for k, v in r.params.items() if k.startswith("subject_"))), sig, plan.edl, "box3", place], default=str).encode()).hexdigest()[:24]
        f = Path(cache_dir) / "floor" / f"box_{key}.json"
        if f.is_file():
            plan.light_box[r.id] = _json.loads(f.read_text(encoding="utf-8")); continue
        dur = (r.end_frame - r.start_frame) / fps
        if place == "follow" and dur > 0.6:
            n = int(min(24, max(3, round(dur / 0.25) + 1)))
            ts = [r.start_frame / fps + (dur - 1.0 / fps) * i / (n - 1) for i in range(n)]
            rows = [(t, _subject_bounds(plan, r, source, cache_dir, t)) for t in ts]
            rows = [[round(t, 4), b["x0"], b["x1"], b["y0"], b["y1"]] for t, b in rows if b]
            if not rows: continue
            mid = rows[len(rows) // 2]                           # the reference position: where the subject is in the middle of the clip
            res = {"track": smooth_track(rows), "ref": dict(zip(("x0", "x1", "y0", "y1"), mid[1:]))} if len(rows) >= 2 else dict(zip(("x0", "x1", "y0", "y1"), rows[0][1:]))
        else:
            b = _subject_bounds(plan, r, source, cache_dir, ((r.start_frame + r.end_frame) // 2) / fps)
            if not b: continue
            res = b
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(_json.dumps(res), encoding="utf-8")
        plan.light_box[r.id] = res


def ensure_references(plan: Plan, source: str, cache_dir: str) -> None:
    """studio_look with a reference picture: render the frame without the correction, measure it against the reference, keep the curves."""
    looks = [o for o in plan.steps if o.type == "studio_look" and o.params.get("reference_asset_id") and o.params["reference_strength"] > 0]
    if not looks: return
    import hashlib, json as _json, cv2
    import numpy as np
    from .studio import match_curves
    plan.look_curves = {}                                  # the measuring render must not use stale curves
    for lk in looks:
        ref_path = plan.blueprint.assets[lk.params["reference_asset_id"]].resolved
        ops_sig = [(o.id, o.type, o.start_frame, o.end_frame, sorted(o.params.items(), key=lambda kv: kv[0]).__repr__(), repr(sorted(o.keyframes))) for o in plan.steps]
        st = Path(ref_path).stat()
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, ref_path, st.st_size, st.st_mtime_ns, lk.id, ops_sig, plan.edl], default=str).encode()).hexdigest()[:24]
        f = Path(cache_dir) / "look" / f"ref_{key}.json"
        if f.is_file():
            plan.look_curves[lk.id] = _json.loads(f.read_text(encoding="utf-8"))
            continue
        mid = (lk.start_frame + lk.end_frame) // 2
        png = render_frame(plan, source, mid / plan.blueprint.output.fps, None, cache_dir, _measuring=True)
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
        ref = cv2.imread(ref_path, cv2.IMREAD_COLOR)
        if ref is None: raise RuntimeError(f"cannot read reference picture {ref_path}")
        ref = cv2.resize(ref[:, :, ::-1], (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_AREA)
        curves = match_curves(np.ascontiguousarray(frame), np.ascontiguousarray(ref), lk.params["reference_strength"])
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(_json.dumps(curves), encoding="utf-8")
        plan.look_curves[lk.id] = curves


def ensure_mattes(plan: Plan, source: str, cache_dir: str, progress=None, cancel=None, frame: int | None = None) -> None:
    """Generate (or reuse cached) AI mattes for every 'ai' background_removal in the plan.
    frame=None -> whole operation range (export/proxy). frame=N -> a cheap single-frame matte for live preview,
    unless the full-range matte is already cached."""
    keyers, faces, ups, rests = plan.ai_keyers(), plan.face_ops(), plan.upscale_ops(), plan.restore_ops()
    if not keyers and not faces and not ups and not rests: return
    out = plan.blueprint.output
    W, H, fps = out.width, out.height, out.fps
    total = sum(k.end_frame - k.start_frame for k in keyers + faces + ups + rests)
    done = 0
    if rests:
        from .ml.restore import generate_restore, restore_key, _paths
        from .ml.models import is_installed
        for r in rests:
            n = r.end_frame - r.start_frame
            have = all(f.is_file() for f in _paths(cache_dir, restore_key(source, r, W, H, fps, r.start_frame, n, plan.edl)))
            if not have and frame is not None and not is_installed("gfpgan"):          # a live frame must not freeze on a 348 MB download
                from .ml.models import ModelError
                raise ModelError("The AI face restoration model is not installed yet. Open this effect's Properties and press 'Download the model (348 MB)'.")
            if frame is None or have:
                cb = (lambda i, _n, d=done, op=r: progress(d + i, total, op.id, "AI face restoration")) if progress else None
                plan.restorations[r.id] = generate_restore(source, r, W, H, fps, r.start_frame, n, cache_dir, cb, cancel, edl=plan.edl)
            else:
                fr = min(max(frame, r.start_frame), r.end_frame - 1)
                plan.restorations[r.id] = generate_restore(source, r, W, H, fps, fr, 1, cache_dir, warmup=2, edl=plan.edl)
            done += n
    if ups:
        from .ml.upscale import generate_upscale, upscale_key
        v = plan.video
        for u in ups:
            n = u.end_frame - u.start_frame
            cached = (Path(cache_dir) / "upscale" / f"up_{upscale_key(source, u, W, H, fps, u.start_frame, n, plan.edl)}.mkv").is_file()
            if frame is None or cached:
                cb = (lambda i, _n, d=done, op=u: progress(d + i, total, op.id, "AI upscaling")) if progress else None
                plan.upscales[u.id] = generate_upscale(source, u, v.width, v.height, W, H, fps, u.start_frame, n, cache_dir, cb, cancel, edl=plan.edl)
            else:
                fr = min(max(frame, u.start_frame), u.end_frame - 1)
                plan.upscales[u.id] = generate_upscale(source, u, v.width, v.height, W, H, fps, fr, 1, cache_dir, warmup=2, edl=plan.edl)
            done += n
    if not keyers and not faces: return
    if faces:
        from .ml.face import generate_control, control_key
        for f in faces:
            n = f.end_frame - f.start_frame
            from .ml.face import ANALYSIS_LONG_SIDE  # noqa
            cached = (Path(cache_dir) / "face" / f"ctrl_{control_key(f.type, source, f, W, H, fps, f.start_frame, n, plan.edl)}.json").is_file()
            if frame is None or cached:
                cb = (lambda i, _n, d=done, op=f: progress(d + i, total, op.id, "Face analysis")) if progress else None
                plan.face_controls[f.id] = generate_control(source, f, W, H, fps, f.start_frame, n, cache_dir, cb, cancel, edl=plan.edl)
            else:
                fr = min(max(frame, f.start_frame), f.end_frame - 1)
                plan.face_controls[f.id] = generate_control(source, f, W, H, fps, fr, 1, cache_dir, warmup=8, edl=plan.edl)
            done += n
    if not keyers: return
    from .ml.matting import generate_matte, matte_key
    for k in keyers:
        n = k.end_frame - k.start_frame
        aid = k.params.get("clean_plate_asset_id") if k.params.get("mode") == "screen" else None
        plate = plan.blueprint.assets[aid].resolved if aid and aid in plan.blueprint.assets else None
        full = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, k, W, H, fps, k.start_frame, n, plan.edl, plate)}.mkv"
        if frame is None or full.is_file():
            cb = (lambda i, _n, d=done, op=k: progress(d + i, total, op.id, "AI matting" if op.params.get("mode") == "ai" else "Green-screen keying (processor)")) if progress else None
            plan.mattes[k.id] = generate_matte(source, k, W, H, fps, k.start_frame, n, cache_dir, cb, cancel, edl=plan.edl, plate=plate)
        else:
            f = min(max(frame, k.start_frame), k.end_frame - 1)
            plan.mattes[k.id] = generate_matte(source, k, W, H, fps, f, 1, cache_dir, warmup=6, edl=plan.edl, plate=plate)
        done += n


def free_space_ok(path: Path, needed: int) -> bool:
    return shutil.disk_usage(path).free > needed


def estimate_bytes(plan: Plan) -> int:
    o = plan.blueprint.output
    br = 20e6 if o.width * o.height > 2_000_000 else 8e6
    return int(plan.timeline_duration * br / 8 * 1.5) + 50_000_000


class Job:
    """One render. States: queued running paused done failed cancelled."""
    failure_hook: "Callable[[Job], None] | None" = None      # set by the server: automatic problem report

    def __init__(self, plan: Plan, source: str, out_path: str, opts: RenderOptions, work_dir: str, kind="render"):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.plan, self.source, self.out_path, self.opts, self.work_dir = plan, source, out_path, opts, work_dir
        self.state = "queued"
        self.progress = 0.0
        self.eta: float | None = None
        self.log: list[str] = []
        self.error: str | None = None
        self.current = "queued"
        self.fallback_used = False
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.started = 0.0
        self.compiled: Compiled | None = None
        self._p0, self._pw = 0.0, 1.0          # progress window of the encode phase
        self._mgr: "JobManager | None" = None

    def to_json(self) -> dict:
        return {"waiting_for": self._mgr.blocker_of(self) if self._mgr and self.state == "queued" else None, "id": self.id, "kind": self.kind, "state": self.state, "progress": round(self.progress, 4),
                "eta_seconds": self.eta, "current": self.current, "error": self.error, "output": self.out_path,
                "log": self.log[-200:], "hardware_fallback": self.fallback_used, "encoder": self._encoder_name(),
                "operations": [o.id for o in self.plan.steps]}

    def _encoder_name(self) -> str | None:
        a = self.compiled.args if self.compiled else None
        try: return a[a.index("-c:v") + 1] if a and "-c:v" in a else None
        except (IndexError, ValueError): return None

    def _log(self, s: str):
        self.log.append(s)
        if len(self.log) > 2000: del self.log[:500]

    # --- control
    def cancel(self):
        self._cancel.set()
        if self.state == "queued": self.state = self.current = "cancelled"          # still waiting in line: it is cancelled now, not when its turn would have come
        with self._lock:
            for sp in list(getattr(self, "_seg_procs", [])):
                try:
                    if sp.poll() is None: sp.terminate()
                except Exception: pass
            if self._proc and self._proc.poll() is None:
                if self.state == "paused" and hasattr(signal, "SIGCONT"):
                    os.kill(self._proc.pid, signal.SIGCONT)
                self._proc.terminate()

    def pause(self) -> bool:
        if not hasattr(signal, "SIGSTOP"): return False
        with self._lock:
            if self._proc and self._proc.poll() is None and self.state == "running":
                os.kill(self._proc.pid, signal.SIGSTOP); self.state = "paused"; return True
        return False

    def resume(self) -> bool:
        with self._lock:
            if self._proc and self._proc.poll() is None and self.state == "paused":
                os.kill(self._proc.pid, signal.SIGCONT); self.state = "running"; return True
        return False

    # --- execution
    def run(self):
        self.started = time.time()
        self.state = "running"
        try:
            try:
                same = Path(self.out_path).resolve() == Path(self.source).resolve() or (
                    Path(self.out_path).exists() and Path(self.source).exists() and os.path.samefile(self.out_path, self.source))
            except OSError:
                same = False
            if same:
                raise RuntimeError("Refusing to overwrite the original video: the output path is the source file.")
            Path(self.out_path).parent.mkdir(parents=True, exist_ok=True)
            if not free_space_ok(Path(self.out_path).parent, estimate_bytes(self.plan)):
                raise RuntimeError("Insufficient disk space for the export (need roughly "
                                   f"{estimate_bytes(self.plan) // 1_000_000} MB free).")
            tmp_out = self.out_path + ".part" + Path(self.out_path).suffix
            if self.plan.ai_keyers() or self.plan.face_ops() or self.plan.upscale_ops() or self.plan.restore_ops():
                from .ml.matting import Cancelled
                from .ml.restore import Cancelled as RestoreCancelled
                from .ml.face import Cancelled as FaceCancelled
                from .ml.upscale import Cancelled as UpCancelled
                self._p0, self._pw = 0.0, 0.75
                def prog(i, n, op_id, what):
                    self.current = f"{what} '{op_id}': frame {i}/{n}"
                    self.progress = min(0.75, 0.75 * i / max(1, n))
                    el = time.time() - self.started
                    if self.progress > 0.02: self.eta = el * (1 - self.progress) / self.progress
                try:
                    ensure_auto(self.plan, self.source, self.work_dir)          # measuring renders first, then the real (full-range) analyses
                    ensure_references(self.plan, self.source, self.work_dir)
                    ensure_floor(self.plan, self.source, self.work_dir); ensure_light_box(self.plan, self.source, self.work_dir); ensure_enhance(self.plan, self.source, self.work_dir)
                    ensure_mattes(self.plan, self.source, self.work_dir, prog, self._cancel)
                except (Cancelled, FaceCancelled, UpCancelled, RestoreCancelled):
                    self.state = self.current = "cancelled"; return
                if self.plan.mattes: self._log(f"AI mattes ready: {', '.join(self.plan.mattes)}")
                if self.plan.upscales:
                    from .ml.models import providers
                    self._log(f"AI upscaling done on {providers()[0].replace('ExecutionProvider', '')}: {', '.join(self.plan.upscales)}")
                    for uid, ref in self.plan.upscales.items():
                        if ref.gpu_fallback:
                            self.fallback_used = True
                            self._log(f"WARNING: GPU failed during AI upscaling of '{uid}', finished on CPU: {ref.gpu_fallback}")
                for rid, ref in self.plan.restorations.items():
                    self._log(f"AI face restoration '{rid}': faces restored in {ref.faces_found}/{ref.frames} frames")
                    if ref.faces_found == 0: self._log(f"WARNING: no face large enough was found for '{rid}' - the operation has no visible effect")
                    if ref.gpu_fallback:
                        self.fallback_used = True
                        self._log(f"WARNING: GPU failed during face restoration of '{rid}', finished on CPU: {ref.gpu_fallback}")
                for fid, ref in self.plan.face_controls.items():
                    self._log(f"Face analysis '{fid}': face found in {ref.faces_found}/{ref.frames} frames")
                    if ref.faces_found == 0:
                        self._log(f"WARNING: no face detected for '{fid}' - the operation has no visible effect")
                self._p0, self._pw = 0.75, 0.25
            nseg = self._segment_count(self.opts)
            ok = self._attempt_segments(tmp_out, self.opts, nseg) if nseg > 1 else False
            if nseg > 1 and not ok and not self._cancel.is_set():
                self._log("Parallel render did not complete; rendering in one piece instead.")
                self.progress = self._p0
            if not ok and not self._cancel.is_set():
                ok = self._attempt(tmp_out, self.opts)
            if not ok and not self._cancel.is_set() and "-hwaccel" in (self.compiled.args if self.compiled else []):
                self._log("Hardware decoding failed; retrying with the processor decoding the source.")
                self.fallback_used = True
                self.opts = RenderOptions(**{**self.opts.__dict__, "hwdecode": False})
                self.progress = 0.0
                ok = self._attempt(tmp_out, self.opts)
            if not ok and not self._cancel.is_set() and self.opts.encoder != "cpu":
                self._log("Hardware encoder failed; retrying on CPU (libx264/libx265).")
                self.fallback_used = True
                o2 = RenderOptions(**{**self.opts.__dict__, "encoder": "cpu"})
                self.progress = 0.0
                ok = self._attempt(tmp_out, o2)
            if self._cancel.is_set():
                self.state = "cancelled"; self.current = "cancelled"
                _rm(tmp_out)
            elif ok:
                fsutil.replace(tmp_out, self.out_path)
                self.progress, self.state, self.current, self.eta = 1.0, "done", "finished", 0
            else:
                self.state = "failed"
                _rm(tmp_out)
                self.error = self.error or "ffmpeg failed"
        except Exception as e:  # noqa
            self.state, self.error = "failed", str(e)
            self._log(f"ERROR: {e}")
        finally:
            for f in (self.compiled.work_files if self.compiled else []): _rm(f)
            if self.state == "failed" and Job.failure_hook:
                try: Job.failure_hook(self)
                except Exception: pass                                  # reporting must never break a job

    # ---- parallel render: the timeline in time segments, one ffmpeg each (a single ffmpeg keeps only ~2 cores busy), joined without
    #      re-encoding; the sound is rendered once over the whole range and added, so it has no joins at all.
    def _segment_count(self, opts: RenderOptions) -> int:
        env = os.environ.get("EVP_RENDER_SEGMENTS")
        out, fps = self.plan.blueprint.output, self.plan.blueprint.output.fps
        if opts.frame_only or opts.audio_only: return 1
        if self.plan.transparent_key is not None and not opts.scale_width: return 1          # see-through exports (ProRes 4444 / WebM) stay in one piece
        if out.codec not in ("h264", "h265", "prores"): return 1
        for o in self.plan.steps:                                                            # effects that look at neighbouring frames
            if o.type == "video_enhancement" and (o.params.get("stabilize") or o.params.get("denoise", 0) > 0): return 1
        t0 = opts.t_start or 0.0
        t1 = opts.t_end if opts.t_end is not None else self.plan.total_frames / fps
        dur = max(0.0, t1 - t0)
        if env: return max(1, min(int(env), int(dur // 1) or 1))
        if dur < 6: return 1
        n = max(1, (os.cpu_count() or 2) // 2)
        if opts.encoder not in ("cpu", "libx264", "libx265") and "prores" not in out.codec: n = min(n, 2)     # graphics cards allow few encoder sessions
        return max(1, min(n, 6, int(dur // 3)))

    def _attempt_segments(self, tmp_out: str, opts: RenderOptions, n: int) -> bool:
        from dataclasses import replace
        fps = self.plan.blueprint.output.fps
        f0 = round((opts.t_start or 0.0) * fps)
        f1 = round((opts.t_end if opts.t_end is not None else self.plan.total_frames / fps) * fps)
        f1 = min(f1, self.plan.total_frames)
        bounds = [f0 + (f1 - f0) * i // n for i in range(n + 1)]
        ext = Path(tmp_out).suffix
        parts = [f"{tmp_out}.seg{i}{ext}" for i in range(n)]
        compiled = []
        self._seg_procs = []
        try:
            for i in range(n):
                o = replace(opts, t_start=bounds[i] / fps, t_end=bounds[i + 1] / fps, audio=False)
                compiled.append(compile_plan(self.plan, self.source, parts[i], o, self.work_dir))
            self.compiled = compiled[0]
            self._log(f"rendering in {n} parallel segments: " + ", ".join(f"{bounds[i] / fps:.2f}-{bounds[i + 1] / fps:.2f}s" for i in range(n)))
            self.current = f"encoding in {n} parallel parts: " + (", ".join(o.id for o in self.plan.steps) or "no operations (re-encode)")
            done = [0.0] * n
            total = max(1e-3, sum(c.out_duration for c in compiled))
            errs: list[list[str]] = [[] for _ in range(n)]

            def watch(i: int, pr):
                for line in pr.stdout:
                    line = line.strip()
                    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                        try: done[i] = min(compiled[i].out_duration, int(line.split("=")[1]) / 1_000_000)
                        except ValueError: continue
                        self.progress = min(0.97, self._p0 + self._pw * 0.97 * sum(done) / total)
                        el = time.time() - self.started
                        if self.progress > 0.03: self.eta = el * (1 - self.progress) / self.progress
            threads = []
            with self._lock:
                if self._cancel.is_set(): return False
                for i, c in enumerate(compiled):
                    pr = proc.Popen(c.args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
                    self._seg_procs.append(pr)
                    threads.append(threading.Thread(target=watch, args=(i, pr), daemon=True))
                    threading.Thread(target=lambda pr=pr, i=i: errs[i].extend(pr.stderr), daemon=True).start()
            for t_ in threads: t_.start()
            codes = [pr.wait() for pr in self._seg_procs]
            for t_ in threads: t_.join(timeout=2)
            if self._cancel.is_set(): return False
            if any(codes):
                k = next(i for i, c in enumerate(codes) if c)
                self._log(f"segment {k} failed: " + "".join(errs[k][-5:]).strip()[:600])
                return False
            # join the picture without re-encoding, then add the sound (rendered once, uninterrupted)
            self.current = "joining the parts"
            lst = tmp_out + ".segs.txt"
            Path(lst).write_text("".join(f"file '{Path(x).resolve().as_posix()}'\n" for x in parts), encoding="utf-8")
            joined = f"{tmp_out}.joined{ext}"
            r = proc.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined], capture_output=True, text=True)
            if r.returncode != 0: self._log("joining failed: " + r.stderr[-400:]); return False
            out = self.plan.blueprint.output
            mux = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", joined]
            sound = None
            if self.plan.video.has_audio and opts.audio:
                self.current = "sound"
                sound = f"{tmp_out}.sound.mka"
                ac = compile_plan(self.plan, self.source, sound, replace(opts, audio_only=True), self.work_dir)
                compiled.append(ac)
                r = proc.run(ac.args, capture_output=True, text=True)
                if r.returncode != 0: self._log("sound failed: " + r.stderr[-400:]); return False
                mux += ["-i", sound, "-map", "0:v:0", "-map", "1:a:0"]
            else:
                mux += ["-map", "0:v:0"]
            if sound:
                prores = out.codec == "prores" and not opts.scale_width
                mux += ["-c:v", "copy"] + (["-c:a", "pcm_s16le"] if prores else ["-c:a", "aac", "-b:a", out.audio_bitrate])
            else:
                mux += ["-c", "copy"]
            mux += (["-tag:v", "hvc1"] if out.codec == "h265" and not opts.scale_width else []) + (["-movflags", "+faststart"] if ext.lower() in (".mp4", ".mov") else []) + [tmp_out]
            r = proc.run(mux, capture_output=True, text=True)
            if r.returncode != 0: self._log("final join failed: " + r.stderr[-400:]); return False
            for x in [joined, lst] + ([sound] if sound else []): _rm(x)
            return True
        except Exception as e:  # noqa
            self._log(f"parallel render error: {e}")
            return False
        finally:
            for pr in self._seg_procs:
                try:
                    if pr.poll() is None: pr.kill()
                except Exception: pass
            self._seg_procs = []
            for c in compiled:
                for f in c.work_files: _rm(f)
            for x in parts + [f"{tmp_out}.joined{ext}", tmp_out + ".segs.txt", f"{tmp_out}.sound.mka"]: _rm(x)

    def _attempt(self, tmp_out: str, opts: RenderOptions) -> bool:
        self.compiled = compile_plan(self.plan, self.source, tmp_out, opts, self.work_dir)
        self._log("filter_complex: " + self.compiled.filtergraph)
        self.current = "encoding: " + (", ".join(o.id for o in self.plan.steps) or "no operations (re-encode)")
        with self._lock:
            if self._cancel.is_set(): return False
            self._proc = proc.Popen(self.compiled.args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
        err_lines: list[str] = []
        t_err = threading.Thread(target=lambda: err_lines.extend(self._proc.stderr), daemon=True); t_err.start()
        dur = max(self.compiled.out_duration, 0.001)
        for line in self._proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    sec = int(line.split("=")[1]) / 1_000_000
                    self.progress = min(0.999, self._p0 + self._pw * max(0.0, min(1.0, sec / dur)))
                    el = time.time() - self.started
                    if self.progress > 0.03: self.eta = el * (1 - self.progress) / self.progress
                except ValueError: pass
        rc = self._proc.wait(); t_err.join(timeout=2)
        for l in err_lines[-50:]: self._log(l.rstrip())
        if rc != 0 and not self._cancel.is_set():
            self.error = "ffmpeg exited with code %s: %s" % (rc, "".join(err_lines[-5:]).strip()[:600])
        return rc == 0


def _rm(p: str):
    try: os.remove(p)
    except OSError: pass


# parameters given in pixels of the OUTPUT picture (not "px at 1080p", which the compiler already scales by the picture height)
DRAFT_PX_PARAMS = ("edge_erode", "edge_feather", "edge_shrink", "edge_soften", "blur")


def draft_plan(plan: Plan, width: int = 960) -> Plan:
    """Fast draft of a live frame: the same project processed at preview size (the key included), instead of full size and then shrunk.
    Pixel-sized settings are scaled with the picture so it looks the same; fine edges are less exact than the export. Exports never use this."""
    import copy
    from .keyframes import Keyframe
    out = plan.blueprint.output
    if out.width <= width: return plan
    f = width / out.width
    d = copy.deepcopy(plan)
    d.blueprint.output.width = max(2, int(width) // 2 * 2)
    d.blueprint.output.height = max(2, int(round(out.height * f)) // 2 * 2)
    seen: set[int] = set()
    for op in list(d.blueprint.operations) + list(d.steps):
        if id(op) in seen: continue                      # the execution order usually holds the same objects: scale each once
        seen.add(id(op))
        for k in DRAFT_PX_PARAMS:
            v = op.params.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                op.params[k] = int(round(v * f)) if isinstance(v, int) else v * f
            if k in op.keyframes:
                op.keyframes[k] = [Keyframe(kf.time, kf.value * f, kf.easing) for kf in op.keyframes[k]]
    return d


def render_frame(plan: Plan, source: str, t: float, scale_width: int | None = 960, work_dir: str | None = None, _measuring: bool = False, view: str | None = None) -> bytes:
    """Single processed frame as PNG (same pipeline as export, so preview == render)."""
    t = min(max(0.0, t), max(0.0, plan.timeline_duration - 1.0 / plan.blueprint.output.fps))
    wd = work_dir or tempfile.mkdtemp(prefix="evp_f_")
    if not _measuring:
        # measuring renders look at another frame and leave THEIR mattes/analyses in the plan, so they run first and this frame's go last
        ensure_auto(plan, source, wd)
        ensure_references(plan, source, wd)
        ensure_floor(plan, source, wd); ensure_light_box(plan, source, wd); ensure_enhance(plan, source, wd)
    ensure_mattes(plan, source, wd, frame=round(t * plan.blueprint.output.fps))
    if view and not any(o.type in ("chroma_key", "background_removal") for o in plan.steps):
        raise ValueError("There is no keying (chroma key / background removal) in this project, so there is no matte to show")
    c = compile_plan(plan, source, "-", RenderOptions(scale_width=scale_width, t_start=t, frame_only=True, view=view), wd)
    try:
        r = proc.run(c.args, capture_output=True, timeout=120)
    finally:
        for f in c.work_files: _rm(f)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("frame render failed: " + r.stderr.decode(errors="replace")[-500:])
    from .pngtools import strip_colour_tags
    return strip_colour_tags(r.stdout)


class JobManager:
    def __init__(self, max_parallel: int = 1):
        self.jobs: dict[str, Job] = {}
        self._sem = threading.Semaphore(max_parallel)
        self._running: list[Job] = []                  # the jobs that hold a slot right now

    def blocker_of(self, job: "Job") -> dict | None:
        """the render a queued job is waiting for (so the editor can say so and offer to stop it)"""
        for j in list(self._running):
            if j is not job and j.state in ("running", "paused", "queued"):
                return {"id": j.id, "kind": j.kind, "state": j.state, "current": j.current, "progress": round(j.progress, 4), "eta_seconds": j.eta}
        return None

    def submit(self, job: Job) -> Job:
        self.jobs[job.id] = job
        job._mgr = self
        def worker():
            with self._sem:
                self._running.append(job)
                try:
                    if job._cancel.is_set(): job.state = "cancelled"; return
                    job.run()
                finally:
                    if job in self._running: self._running.remove(job)
        threading.Thread(target=worker, daemon=True).start()
        return job

    def retry(self, job_id: str) -> Job:
        old = self.jobs[job_id]
        if old.state not in ("failed", "cancelled"): raise ValueError("only failed/cancelled jobs can be retried")
        return self.submit(Job(old.plan, old.source, old.out_path, old.opts, old.work_dir, old.kind))
