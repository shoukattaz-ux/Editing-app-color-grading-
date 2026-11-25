"""Local HTTP API (127.0.0.1 only, per-launch secret token). Thin layer over the engine."""
import hashlib, json, os, secrets, sys, tempfile, argparse, threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __version__
from .blueprint import VideoInfo
from .compiler import RenderOptions
from .engine import prepare, render_frame, Job, JobManager
from .media import probe, hardware_report, MediaError
from .reporter import Reporter
from .project import Project, ProjectError, RecentProjects
from .registry import json_schema
from .security import PathPolicy, safe_output_path, PathError


class State:
    def __init__(self, token: str, data_dir: Path):
        self.token = token
        self.data_dir = data_dir
        self.project: Project | None = None
        self.video: VideoInfo | None = None
        self.jobs = JobManager()
        self.recent = RecentProjects(data_dir / "recent.json")
        from .ml import device as _device
        _device.configure(data_dir)
        self.reporter = Reporter(data_dir, __version__, allow_local=bool(os.environ.get("EVP_REPORT_ALLOW_LOCAL")))

    @property
    def source(self) -> str | None:
        return ((self.project.data.get("source") or {}).get("path")) if self.project else None

    def policy(self) -> PathPolicy:
        return PathPolicy([self.project.root] if self.project else [])

    def need_project(self) -> Project:
        if not self.project: raise HTTPException(409, "No project open")
        return self.project

    def load_video(self):
        self.video = None
        if self.source and Path(self.source).is_file():
            try: self.video = probe(self.source)
            except MediaError: self.video = None


def create_app(token: str | None = None, data_dir: str | None = None) -> FastAPI:
    st = State(token or secrets.token_urlsafe(24), Path(data_dir or Path.home() / ".evp"))
    app = FastAPI(title="EVP backend", version=__version__)
    app.state.evp = st
    # The UI is served from file:// or a dev server; every route except /health requires the per-launch secret token.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["content-range", "accept-ranges"])

    def auth(request: Request):
        t = request.headers.get("x-evp-token") or request.query_params.get("token")
        if not t or not secrets.compare_digest(t, st.token):
            raise HTTPException(401, "bad token")

    api = lambda: Depends(auth)  # noqa

    @app.exception_handler(ProjectError)
    async def _pe(_, e): return Response(json.dumps({"detail": str(e)}), 400, media_type="application/json")

    @app.exception_handler(MediaError)
    async def _me(_, e): return Response(json.dumps({"detail": str(e)}), 400, media_type="application/json")

    @app.exception_handler(PathError)
    async def _pa(_, e): return Response(json.dumps({"detail": str(e)}), 400, media_type="application/json")

    def _failure_report(job: Job) -> None:
        bp = st.project.data.get("blueprint") if st.project else None
        st.reporter.auto("render_failed", job.error or "render failed", {"job_kind": job.kind, "current": job.current, "fallback_used": job.fallback_used},
                         bp, "\n".join(job.log))
    Job.failure_hook = _failure_report

    @app.on_event("startup")
    def _flush_outbox():
        threading.Thread(target=st.reporter.flush_outbox, daemon=True).start()

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        import traceback
        st.reporter.auto("server_error", f"{type(exc).__name__}: {exc}", {"path": request.url.path}, None, "".join(traceback.format_exception(exc))[-8000:])
        tb = "".join(traceback.format_exception(exc))
        try:                                              # keep the full traceback next to the other app data, for support
            with open(st.data_dir / "engine-errors.log", "a", encoding="utf-8") as f: f.write(f"--- {request.method} {request.url.path}\n{tb}\n")
        except OSError: pass
        return Response(json.dumps({"detail": f"Unexpected error ({type(exc).__name__}): {str(exc)[:300]}"}), status_code=500, media_type="application/json")

    @app.get("/api/health")
    def health(): return {"ok": True, "version": __version__}

    @app.get("/api/system", dependencies=[api()])
    def system(): return hardware_report()

    @app.get("/api/schema", dependencies=[api()])
    def schema(): return json_schema()

    # ---------------- project
    class NewProject(BaseModel):
        parent: str
        name: str
        source: str | None = None

    def _project_json():
        p = st.need_project()
        return {"path": str(p.root), "name": p.data["name"], "version": p.data["version"], "source": p.data.get("source"),
                "video": st.video.__dict__ if st.video else None, "blueprint": p.data["blueprint"], "ui": p.data.get("ui", {}),
                "missing": p.missing(), "recovery_available": p.recovery_available(),
                "restored": p.restored_at is not None, "restored_at": p.restored_at, "saved_blueprint": p.saved_blueprint}

    @app.post("/api/project/new", dependencies=[api()])
    def new_project(b: NewProject):
        st.project = Project.create(b.parent, b.name)
        if b.source: _set_source(b.source); st.project.save(snapshot=False)      # the video link is on disk from the first second
        st.recent.add(str(st.project.root), b.name)
        return _project_json()

    class QuickProject(BaseModel):
        source: str | None = None
        name: str | None = None

    def _projects_dir() -> Path:
        d = os.environ.get("EVP_PROJECTS_DIR")
        if d: return Path(d)
        docs = Path.home() / "Documents"
        return (docs if docs.is_dir() else Path.home()) / "EVP Projects"

    @app.get("/api/defaults", dependencies=[api()])
    def defaults(): return {"projects_dir": str(_projects_dir())}

    @app.post("/api/project/quick", dependencies=[api()])
    def quick_project(b: QuickProject):
        """one step: a project in the default projects folder, named after the video (made unique), source attached"""
        import re
        base = (b.name or (Path(b.source).stem if b.source else "My Project")).strip()
        base = re.sub(r"[^\w .-]+", " ", base).strip(" .") or "My Project"
        parent = _projects_dir()
        name, n = base, 1
        while (parent / (name + ".evproj")).exists() and n < 500:
            n += 1; name = f"{base} {n}"
        st.project = Project.create(parent, name)
        if b.source: _set_source(b.source); st.project.save(snapshot=False)
        st.recent.add(str(st.project.root), name)
        return _project_json()

    class PathBody(BaseModel):
        path: str

    @app.post("/api/project/open", dependencies=[api()])
    def open_project(b: PathBody):
        st.project = Project.open(b.path, restore=True)       # reopens exactly where the last session ended, even after a crash
        st.load_video()
        st.recent.add(str(st.project.root), st.project.data["name"])
        return _project_json()

    @app.get("/api/project", dependencies=[api()])
    def get_project(): return _project_json()

    @app.get("/api/project/recent", dependencies=[api()])
    def recent(): return st.recent.list()

    def _set_source(path: str):
        p = st.need_project()
        info = probe(path)
        p.update(source=str(Path(path).resolve()))
        st.video = info

    @app.post("/api/project/source", dependencies=[api()])
    def set_source(b: PathBody):
        _set_source(b.path)
        st.project.autosave()
        return _project_json()

    class BP(BaseModel):
        blueprint: dict[str, Any]
        ui: dict[str, Any] | None = None
        clean: bool = False          # the editor is back at the saved state (e.g. after Undo): drop the auto-save so it is not restored later

    @app.put("/api/project/blueprint", dependencies=[api()])
    def put_blueprint(b: BP):
        p = st.need_project()
        p.update(blueprint=b.blueprint, ui=b.ui)
        if b.clean: p.discard_autosave()
        else: p.autosave()
        if b.ui is not None: p.write_ui(b.ui)
        st.recent.touch_if_stale(str(p.root), p.data["name"])
        return {"ok": True}

    @app.put("/api/project/ui", dependencies=[api()])
    def put_ui(ui: dict[str, Any]):
        """playhead, open tab, zoom, selection ...: tiny, saved often, never counts as an unsaved edit"""
        st.need_project().write_ui(ui)
        return {"ok": True}

    @app.post("/api/project/revert", dependencies=[api()])
    def revert():
        st.need_project().revert_to_saved(); st.load_video(); return _project_json()

    @app.post("/api/project/save", dependencies=[api()])
    def save(b: BP | None = None):
        p = st.need_project()
        if b: p.update(blueprint=b.blueprint, ui=b.ui)
        return {"version": p.save()}

    class SaveAs(BaseModel):
        parent: str
        name: str

    @app.post("/api/project/save_as", dependencies=[api()])
    def save_as(b: SaveAs):
        st.project = st.need_project().save_as(b.parent, b.name)
        st.recent.add(str(st.project.root), b.name)
        return _project_json()

    @app.post("/api/project/recover", dependencies=[api()])
    def recover():
        st.need_project().recover(); return _project_json()

    @app.post("/api/project/discard_autosave", dependencies=[api()])
    def discard():
        st.need_project().discard_autosave(); return {"ok": True}

    @app.get("/api/project/versions", dependencies=[api()])
    def versions(): return st.need_project().versions()

    class Ver(BaseModel):
        file: str

    @app.post("/api/project/restore", dependencies=[api()])
    def restore(b: Ver):
        st.need_project().restore_version(b.file); return _project_json()

    class Relink(BaseModel):
        kind: str
        path: str
        asset_id: str | None = None

    @app.post("/api/project/relink", dependencies=[api()])
    def relink(b: Relink):
        p = st.need_project()
        p.relink(b.kind, b.path, b.asset_id)
        if b.kind == "source": st.load_video()
        return _project_json()

    @app.post("/api/project/asset", dependencies=[api()])
    def import_asset(b: PathBody):
        return {"path": st.need_project().import_asset(b.path)}

    # ---------------- blueprint
    class Validate(BaseModel):
        blueprint: Any

    def _prep(bp: Any):
        p = st.need_project()
        return prepare(bp, st.source, st.policy(), p.root, st.video)

    @app.post("/api/blueprint/validate", dependencies=[api()])
    def validate(b: Validate):
        return _prep(b.blueprint).to_json()

    # ---------------- preview / render
    class FrameReq(BaseModel):
        blueprint: Any
        t: float = 0
        width: int | None = 960
        original: bool = False

    @app.post("/api/preview/frame", dependencies=[api()])
    def frame(b: FrameReq):
        st.need_project()
        if not st.source: raise HTTPException(409, "No source video")
        bp = {"blueprint_version": "1.0", "operations": []} if b.original else b.blueprint
        prep = _prep(bp)
        if not prep.plan:
            raise HTTPException(422, {"errors": [i.message for i in prep.report.errors]})
        try:
            png = render_frame(prep.plan, st.source, b.t, b.width, str(st.project.cache_dir))
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        return Response(png, media_type="image/png")

    class RenderReq(BaseModel):
        blueprint: Any
        preset: str = "proxy"            # proxy | export
        filename: str = "export"
        encoder: str = "auto"
        t_start: float | None = None
        t_end: float | None = None

    @app.post("/api/render", dependencies=[api()])
    def render(b: RenderReq):
        p = st.need_project()
        if not st.source: raise HTTPException(409, "No source video")
        prep = _prep(b.blueprint)
        if not prep.plan:
            raise HTTPException(422, {"errors": [{"code": i.code, "message": i.message, "op_id": i.op_id} for i in prep.report.errors]})
        if b.preset == "proxy":
            key = hashlib.sha1(json.dumps([b.blueprint, st.source, os.path.getmtime(st.source), b.t_start, b.t_end], sort_keys=True).encode()).hexdigest()[:16]
            out = p.cache_dir / f"proxy_{key}.mp4"
            if out.is_file():
                return {"cached": True, "output": f"cache/{out.name}", "job": None}
            opts = RenderOptions(scale_width=640, crf=30, preset="ultrafast", encoder="cpu", t_start=b.t_start, t_end=b.t_end)
            kind = "proxy"
        else:
            ext = prep.plan.blueprint.output.format
            name = "".join(c for c in b.filename if c.isalnum() or c in " _-").strip()[:80] or "export"
            out = safe_output_path(p.exports_dir / f"{name}.{ext}", st.source, p.exports_dir)
            opts = RenderOptions(encoder=b.encoder, t_start=b.t_start, t_end=b.t_end)
            kind = "render"
        job = Job(prep.plan, st.source, str(out), opts, str(p.cache_dir), kind)
        st.jobs.submit(job)
        return {"cached": False, "job": job.to_json(), "output": f"{'cache' if kind == 'proxy' else 'exports'}/{out.name}"}

    def _job(i: str) -> Job:
        j = st.jobs.jobs.get(i)
        if not j: raise HTTPException(404, "unknown job")
        return j

    @app.get("/api/jobs/{job_id}", dependencies=[api()])
    def job(job_id: str): return _job(job_id).to_json()

    @app.post("/api/jobs/{job_id}/{action}", dependencies=[api()])
    def job_action(job_id: str, action: str):
        j = _job(job_id)
        if action == "cancel": j.cancel()
        elif action == "pause":
            if not j.pause(): raise HTTPException(409, "cannot pause (not running or unsupported on this OS)")
        elif action == "resume":
            if not j.resume(): raise HTTPException(409, "not paused")
        elif action == "retry":
            try: return st.jobs.retry(job_id).to_json()
            except ValueError as e: raise HTTPException(409, str(e))
        else: raise HTTPException(404, "unknown action")
        return j.to_json()

    # ---------------- AI device (which graphics card / processor runs the AI models)
    class AiDevice(BaseModel):
        device: str

    @app.get("/api/ai-device", dependencies=[api()])
    def ai_device():
        from .ml.device import describe
        return describe()

    @app.put("/api/ai-device", dependencies=[api()])
    def ai_device_put(b: AiDevice):
        from .ml import device
        try: device.set_choice(b.device)
        except ValueError as e: raise HTTPException(422, str(e))
        return device.describe()

    @app.get("/api/gpu", dependencies=[api()])
    def gpu_status():
        """is the graphics card working right now? (measured on Windows; configured device elsewhere)"""
        from .ml.device import describe
        from .ml.gpumon import monitor
        return monitor().snapshot(describe(), [{"state": j.state, "encoder": j._encoder_name(), "current": j.current} for j in st.jobs.jobs.values()])

    # ---------------- problem reports (Cloudflare R2)
    class ReportCfg(BaseModel):
        endpoint: str | None = None
        bucket: str | None = None
        access_key_id: str | None = None
        secret_access_key: str | None = None
        enabled: bool | None = None
        auto_send: bool | None = None

    @app.get("/api/report/config", dependencies=[api()])
    def report_cfg(): return st.reporter.public_config()

    @app.put("/api/report/config", dependencies=[api()])
    def report_cfg_put(b: ReportCfg):
        try: st.reporter.save_config(b.endpoint, b.bucket, b.access_key_id, b.secret_access_key, b.enabled, b.auto_send)
        except ValueError as e: raise HTTPException(422, str(e))
        return st.reporter.public_config()

    @app.post("/api/report/test", dependencies=[api()])
    def report_test(): return st.reporter.send("test", "Test report from Settings", {"note": "connection test"})

    class ReportSend(BaseModel):
        message: str = ""
        include_blueprint: bool = True

    @app.post("/api/report/send", dependencies=[api()])
    def report_send(b: ReportSend):
        bp = st.project.data.get("blueprint") if (st.project and b.include_blueprint) else None
        jobs = list(st.jobs.jobs.values())[-1:]
        log = "\n".join(jobs[0].log) if jobs else ""
        return st.reporter.send("user_report", b.message or "(no message)", {"last_job_state": jobs[0].state if jobs else None}, bp, log)

    # ---------------- media (range requests supported by FileResponse)
    @app.get("/api/media/source", dependencies=[api()])
    def media_source():
        if not st.source or not Path(st.source).is_file(): raise HTTPException(404, "no source")
        return FileResponse(st.source)

    @app.get("/api/media/project", dependencies=[api()])
    def media_project(path: str):
        p = st.need_project()
        f = st.policy().resolve(path, p.root)
        if not f.is_file(): raise HTTPException(404, "not found")
        return FileResponse(f)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--data-dir", default=None)
    a = ap.parse_args()
    import uvicorn, socket
    token = secrets.token_urlsafe(24)
    if a.port == 0:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); a.port = s.getsockname()[1]; s.close()
    print(json.dumps({"port": a.port, "token": token}), flush=True)    # parent process (Electron) reads this line
    uvicorn.run(create_app(token, a.data_dir), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
