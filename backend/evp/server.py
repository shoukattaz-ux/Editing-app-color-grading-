"""Local HTTP API (127.0.0.1 only, per-launch secret token). Thin layer over the engine."""
import hashlib, json, os, secrets, sys, tempfile, argparse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .blueprint import VideoInfo
from .compiler import RenderOptions
from .engine import prepare, render_frame, Job, JobManager
from .media import probe, hardware_report, MediaError
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
                "missing": p.missing(), "recovery_available": p.recovery_available()}

    @app.post("/api/project/new", dependencies=[api()])
    def new_project(b: NewProject):
        st.project = Project.create(b.parent, b.name)
        if b.source: _set_source(b.source)
        st.recent.add(str(st.project.root), b.name)
        return _project_json()

    class PathBody(BaseModel):
        path: str

    @app.post("/api/project/open", dependencies=[api()])
    def open_project(b: PathBody):
        st.project = Project.open(b.path)
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

    @app.put("/api/project/blueprint", dependencies=[api()])
    def put_blueprint(b: BP):
        p = st.need_project()
        p.update(blueprint=b.blueprint, ui=b.ui)
        p.autosave()
        return {"ok": True}

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
