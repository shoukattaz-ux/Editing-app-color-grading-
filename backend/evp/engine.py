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
from .media import probe, MediaError
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
    looks = [o for o in plan.steps if o.type == "studio_look" and o.params.get("auto_match", 0) > 0]
    if not looks: return
    import hashlib, json as _json, cv2
    import numpy as np
    from .studio import analyze
    plan.look_auto = {}
    for lk in looks:
        repl = next((o for o in plan.steps if o.type == "background_replacement" and plan.look_for.get(o.id) is lk), None)
        mtimes = [Path(a.resolved).stat().st_mtime_ns for a in plan.blueprint.assets.values() if a.resolved and Path(a.resolved).is_file()]
        ops_sig = [(o.id, o.type, o.start_frame, o.end_frame, repr(sorted(o.params.items())), repr(sorted(o.keyframes))) for o in plan.steps if o.type != "studio_look"]
        key = hashlib.sha256(_json.dumps([str(source), Path(source).stat().st_mtime_ns, mtimes, lk.id, ops_sig, plan.edl, "auto1"], default=str).encode()).hexdigest()[:24]
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
        if mask.any():
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
    keyers, faces, ups = plan.ai_keyers(), plan.face_ops(), plan.upscale_ops()
    if not keyers and not faces and not ups: return
    out = plan.blueprint.output
    W, H, fps = out.width, out.height, out.fps
    total = sum(k.end_frame - k.start_frame for k in keyers + faces + ups)
    done = 0
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

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state, "progress": round(self.progress, 4),
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
        with self._lock:
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
            if self.plan.ai_keyers() or self.plan.face_ops() or self.plan.upscale_ops():
                from .ml.matting import Cancelled
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
                    ensure_mattes(self.plan, self.source, self.work_dir, prog, self._cancel)
                except (Cancelled, FaceCancelled, UpCancelled):
                    self.state = self.current = "cancelled"; return
                if self.plan.mattes: self._log(f"AI mattes ready: {', '.join(self.plan.mattes)}")
                if self.plan.upscales:
                    from .ml.models import providers
                    self._log(f"AI upscaling done on {providers()[0].replace('ExecutionProvider', '')}: {', '.join(self.plan.upscales)}")
                    for uid, ref in self.plan.upscales.items():
                        if ref.gpu_fallback:
                            self.fallback_used = True
                            self._log(f"WARNING: GPU failed during AI upscaling of '{uid}', finished on CPU: {ref.gpu_fallback}")
                for fid, ref in self.plan.face_controls.items():
                    self._log(f"Face analysis '{fid}': face found in {ref.faces_found}/{ref.frames} frames")
                    if ref.faces_found == 0:
                        self._log(f"WARNING: no face detected for '{fid}' - the operation has no visible effect")
                self._p0, self._pw = 0.75, 0.25
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


def render_frame(plan: Plan, source: str, t: float, scale_width: int | None = 960, work_dir: str | None = None, _measuring: bool = False, view: str | None = None) -> bytes:
    """Single processed frame as PNG (same pipeline as export, so preview == render)."""
    t = min(max(0.0, t), max(0.0, plan.timeline_duration - 1.0 / plan.blueprint.output.fps))
    wd = work_dir or tempfile.mkdtemp(prefix="evp_f_")
    if not _measuring:
        # measuring renders look at another frame and leave THEIR mattes/analyses in the plan, so they run first and this frame's go last
        ensure_auto(plan, source, wd)
        ensure_references(plan, source, wd)
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

    def submit(self, job: Job) -> Job:
        self.jobs[job.id] = job
        def worker():
            with self._sem:
                if job._cancel.is_set(): job.state = "cancelled"; return
                job.run()
        threading.Thread(target=worker, daemon=True).start()
        return job

    def retry(self, job_id: str) -> Job:
        old = self.jobs[job_id]
        if old.state not in ("failed", "cancelled"): raise ValueError("only failed/cancelled jobs can be retried")
        return self.submit(Job(old.plan, old.source, old.out_path, old.opts, old.work_dir, old.kind))
