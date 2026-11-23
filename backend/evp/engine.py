"""Blueprint Execution Engine: validate -> plan -> compile -> run (progress/pause/resume/cancel/retry)."""
from __future__ import annotations
import os, shutil, signal, subprocess, tempfile, threading, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .blueprint import parse_blueprint, Report, VideoInfo
from .compiler import compile_plan, RenderOptions, Compiled
from .media import probe, MediaError
from .planner import build_plan, Plan
from .security import PathPolicy, safe_output_path, PathError


@dataclass
class Prepared:
    report: Report
    plan: Plan | None
    video: VideoInfo | None

    def to_json(self) -> dict:
        d = self.report.to_json()
        d["plan"] = self.plan.describe() if self.plan else []
        d["video"] = self.video.__dict__ if self.video else None
        d["ok"] = bool(self.report.ok and self.plan)
        return d


def prepare(blueprint: Any, source: str | None, policy: PathPolicy, base_dir: Path | None = None,
            video: VideoInfo | None = None) -> Prepared:
    """Steps 1-6 of the engine: parse, validate, resolve dependencies, order."""
    if video is None and source:
        try:
            video = probe(source)
        except MediaError as e:
            rep = Report(); rep.err("BAD_SOURCE", str(e))
            return Prepared(rep, None, None)
    rep = parse_blueprint(blueprint, video, policy, base_dir)
    plan = None
    if rep.ok and video:
        plan = build_plan(rep.blueprint, video, rep)
    return Prepared(rep, plan, video)


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
            cached = (Path(cache_dir) / "upscale" / f"up_{upscale_key(source, u, W, H, fps, u.start_frame, n)}.mkv").is_file()
            if frame is None or cached:
                cb = (lambda i, _n, d=done, op=u: progress(d + i, total, op.id, "AI upscaling")) if progress else None
                plan.upscales[u.id] = generate_upscale(source, u, v.width, v.height, W, H, fps, u.start_frame, n, cache_dir, cb, cancel)
            else:
                fr = min(max(frame, u.start_frame), u.end_frame - 1)
                plan.upscales[u.id] = generate_upscale(source, u, v.width, v.height, W, H, fps, fr, 1, cache_dir)
            done += n
    if not keyers and not faces: return
    if faces:
        from .ml.face import generate_control, control_key
        for f in faces:
            n = f.end_frame - f.start_frame
            from .ml.face import ANALYSIS_LONG_SIDE  # noqa
            cached = (Path(cache_dir) / "face" / f"ctrl_{control_key(f.type, source, f, W, H, fps, f.start_frame, n)}.json").is_file()
            if frame is None or cached:
                cb = (lambda i, _n, d=done, op=f: progress(d + i, total, op.id, "Face analysis")) if progress else None
                plan.face_controls[f.id] = generate_control(source, f, W, H, fps, f.start_frame, n, cache_dir, cb, cancel)
            else:
                fr = min(max(frame, f.start_frame), f.end_frame - 1)
                plan.face_controls[f.id] = generate_control(source, f, W, H, fps, fr, 1, cache_dir, warmup=8)
            done += n
    if not keyers: return
    from .ml.matting import generate_matte, matte_key
    for k in keyers:
        n = k.end_frame - k.start_frame
        full = Path(cache_dir) / "mattes" / f"matte_{matte_key(source, k, W, H, fps, k.start_frame, n)}.mkv"
        if frame is None or full.is_file():
            cb = (lambda i, _n, d=done, op=k: progress(d + i, total, op.id, "AI matting")) if progress else None
            plan.mattes[k.id] = generate_matte(source, k, W, H, fps, k.start_frame, n, cache_dir, cb, cancel)
        else:
            f = min(max(frame, k.start_frame), k.end_frame - 1)
            plan.mattes[k.id] = generate_matte(source, k, W, H, fps, f, 1, cache_dir, warmup=6)
        done += n


def free_space_ok(path: Path, needed: int) -> bool:
    return shutil.disk_usage(path).free > needed


def estimate_bytes(plan: Plan) -> int:
    o = plan.blueprint.output
    br = 20e6 if o.width * o.height > 2_000_000 else 8e6
    return int(plan.video.duration * br / 8 * 1.5) + 50_000_000


class Job:
    """One render. States: queued running paused done failed cancelled."""

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
                "log": self.log[-200:], "hardware_fallback": self.fallback_used,
                "operations": [o.id for o in self.plan.steps]}

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
                os.replace(tmp_out, self.out_path)
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

    def _attempt(self, tmp_out: str, opts: RenderOptions) -> bool:
        self.compiled = compile_plan(self.plan, self.source, tmp_out, opts, self.work_dir)
        self._log("filter_complex: " + self.compiled.filtergraph)
        self.current = "encoding: " + (", ".join(o.id for o in self.plan.steps) or "no operations (re-encode)")
        with self._lock:
            if self._cancel.is_set(): return False
            self._proc = subprocess.Popen(self.compiled.args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
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


def render_frame(plan: Plan, source: str, t: float, scale_width: int | None = 960, work_dir: str | None = None) -> bytes:
    """Single processed frame as PNG (same pipeline as export, so preview == render)."""
    t = min(max(0.0, t), max(0.0, plan.video.duration - 1.0 / plan.blueprint.output.fps))
    wd = work_dir or tempfile.mkdtemp(prefix="evp_f_")
    ensure_mattes(plan, source, wd, frame=round(t * plan.blueprint.output.fps))
    c = compile_plan(plan, source, "-", RenderOptions(scale_width=scale_width, t_start=t, frame_only=True), wd)
    try:
        r = subprocess.run(c.args, capture_output=True, timeout=120)
    finally:
        for f in c.work_files: _rm(f)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("frame render failed: " + r.stderr.decode(errors="replace")[-500:])
    return r.stdout


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
