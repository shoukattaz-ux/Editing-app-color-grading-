"""Local HTTP API (127.0.0.1 only, per-launch secret token). Thin layer over the engine."""
import hashlib, json, os, secrets, sys, tempfile, argparse, threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __version__, mediabin
from .blueprint import VideoInfo
from .compiler import RenderOptions
from . import photo
from .engine import prepare, render_frame, draft_plan, Job, JobManager
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
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["content-range", "accept-ranges", "x-evp-plate", "x-evp-camera", "x-evp-hair", "x-evp-floor", "x-evp-enhance"])

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
                "restored": p.restored_at is not None, "restored_at": p.restored_at, "saved_blueprint": p.saved_blueprint,
                "photo": p.data.get("photo")}

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
        if photo.is_photo(path):                                    # photo mode: the picture becomes a lossless one-second clip inside the project
            clip, pinfo = photo.ingest(path, p.root / "photo")
            p.data["photo"] = pinfo
            path = str(clip)
        else:
            p.data.pop("photo", None)
        info = probe(path)
        p.update(source=str(Path(path).resolve()))
        mediabin.remember_source(p, str(Path(path).resolve()))      # stays in the Media bin after another video replaces it
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
        view: str | None = None          # key QC: alpha | edge | red | white | grey | checker
        draft: bool = False              # fast draft: process at preview size (the key included); never used for exports

    from collections import OrderedDict
    _FRAMES: "OrderedDict[str, Response]" = OrderedDict()        # the last 32 single-frame previews, keyed by everything that affects the picture

    def _frame_key(plan, source, b) -> str:
        import hashlib
        files = []
        for a in plan.blueprint.assets.values():
            r = getattr(a, "resolved", None)
            try: files.append((r, os.stat(r).st_mtime_ns))
            except (OSError, TypeError): files.append((r, 0))
        blob = json.dumps([b.blueprint if not b.original else None, round(b.t, 4), b.width, b.view, bool(b.original), bool(b.draft), str(source), os.stat(source).st_mtime_ns, files], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    @app.post("/api/preview/frame", dependencies=[api()])
    def frame(b: FrameReq):
        st.need_project()
        if not st.source: raise HTTPException(409, "No source video")
        bp = {"blueprint_version": "1.0", "operations": []} if b.original else b.blueprint
        prep = _prep(bp)
        if not prep.plan:
            raise HTTPException(422, {"errors": [i.message for i in prep.report.errors]})
        try:
            if b.view is not None and b.view not in ("alpha", "edge", "red", "white", "grey", "checker"): raise HTTPException(422, {"errors": ["unknown view"]})
            ckey = _frame_key(prep.plan, st.source, b)
            if ckey in _FRAMES:                                                  # the same picture again (scrubbing back, undo, toggling): instant
                _FRAMES.move_to_end(ckey); return _FRAMES[ckey]
            plan = draft_plan(prep.plan, min(960, b.width or 960)) if b.draft else prep.plan
            png = render_frame(plan, st.source, b.t, b.width, str(st.project.cache_dir), view=b.view)
        except ValueError as e:
            raise HTTPException(422, {"errors": [str(e)]})
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        notes = {k: ref.plate for k, ref in plan.mattes.items() if getattr(ref, "plate", None)}      # clean plate use per key: {used, rejected}
        cams = {k: v["camera"] for k, v in plan.look_auto.items() if isinstance(v, dict) and v.get("camera")}   # camera match measurements per studio look
        hairs = {k: ref.hair for k, ref in plan.mattes.items() if getattr(ref, "hair", None)}                  # hair detail use per key: {used, skipped}
        floors = dict(plan.floor)
        from .enhance import corrections as _enh_corr
        enh = {k: {"measured": m_, "full": _enh_corr(m_, next((o.params.get("enhance_style") or "crisp" for o in plan.steps if o.id == k), "crisp"))} for k, m_ in plan.enhance.items()}                                                                              # floor line per background replacement: {y, cut}
        hdr = {**({"x-evp-enhance": json.dumps(enh)} if enh else {}), **({"x-evp-floor": json.dumps(floors)} if floors else {}), **({"x-evp-plate": json.dumps(notes)} if notes else {}), **({"x-evp-camera": json.dumps(cams)} if cams else {}), **({"x-evp-hair": json.dumps(hairs)} if hairs else {})}
        resp = Response(png, media_type="image/png", headers=hdr or None)
        _FRAMES[ckey] = resp
        while len(_FRAMES) > 32: _FRAMES.popitem(last=False)
        return resp

    class PhotoExport(BaseModel):
        blueprint: Any
        filename: str = "photo"
        format: str = "png"                 # png | jpg | webp
        quality: int = 95                   # jpg / webp
        out_dir: str | None = None          # save into this folder instead of the project's exports folder

    @app.post("/api/photo/export", dependencies=[api()])
    def photo_export(b: PhotoExport):
        """render the (single) frame of a photo project at the output size and save it as a picture; never overwrites a file"""
        p = st.need_project()
        if not p.data.get("photo"): raise HTTPException(409, "This project is not a photo")
        prep = _prep(b.blueprint)
        if not prep.plan: raise HTTPException(422, {"errors": [i.message for i in prep.report.errors]})
        fmt = (b.format or "png").lower()
        if fmt not in photo.FORMATS: raise HTTPException(422, {"errors": ["format must be png, jpg or webp"]})
        name = "".join(c for c in b.filename if c.isalnum() or c in " _-").strip()[:80] or "photo"
        if b.out_dir and b.out_dir.strip():
            d = Path(b.out_dir.strip()).expanduser()
            if not d.is_dir(): raise HTTPException(422, {"errors": [f"The folder does not exist: {d}"]})
            if not os.access(d, os.W_OK): raise HTTPException(422, {"errors": [f"Cannot write to this folder: {d}"]})
            d = d.resolve()
        else:
            d = p.exports_dir
        out = photo.unique_path(d, name, photo.FORMATS[fmt], {p.data["photo"]["original"], st.source or ""})
        try:
            png = render_frame(prep.plan, st.source, 0.0, None, str(p.cache_dir))             # the full output size, the same engine as every preview and export
        except ValueError as e: raise HTTPException(422, {"errors": [str(e)]})
        except RuntimeError as e: raise HTTPException(500, str(e))
        data = photo.encode(png, fmt, b.quality)
        out.write_bytes(data)
        return {"output": str(out), "bytes": len(data), "format": fmt}

    # ---- batch: one look on every photo of a folder (runs in the background; the editor polls it)
    _BATCHES: dict[str, dict] = {}

    class PhotoBatch(BaseModel):
        blueprint: Any
        folder: str
        out_dir: str | None = None          # default: a new "edited" folder inside the photos' folder
        format: str = "jpg"
        quality: int = 95

    def _run_batch(bid: str, files: list[Path], out: Path, bp: Any, fmt: str, quality: int, same_dir: bool, p, policy) -> None:
        bt = _BATCHES[bid]
        try:
            for f in files:
                if bt["cancel"]: bt["state"] = "cancelled"; return
                bt["current"] = f.name
                try:
                    clip, _info = photo.ingest(str(f), p.root / "photo")
                    prep = prepare(bp, str(clip), policy, p.root, probe(str(clip)))
                    if not prep.plan: raise ValueError("; ".join(i.message for i in prep.report.errors) or "the blueprint has no usable effects")
                    png = render_frame(prep.plan, str(clip), 0.0, None, str(p.cache_dir))
                    dest = photo.unique_path(out, f.stem + (" edited" if same_dir else ""), photo.FORMATS[fmt], {str(f.resolve())})
                    dest.write_bytes(photo.encode(png, fmt, quality))
                    bt["done"] += 1; bt["outputs"].append(str(dest))
                except Exception as e:                                       # one bad photo never stops the others
                    bt["failed"].append({"file": f.name, "error": str(e)[:300]})
            bt["state"] = "done"
        except Exception as e:
            bt["state"] = "failed"; bt["error"] = str(e)[:300]
        finally:
            bt["current"] = ""

    @app.post("/api/photo/batch", dependencies=[api()])
    def photo_batch(b: PhotoBatch):
        p = st.need_project()
        fmt = (b.format or "jpg").lower()
        if fmt not in photo.FORMATS: raise HTTPException(422, {"errors": ["format must be png, jpg or webp"]})
        folder = Path(b.folder.strip()).expanduser()
        if not folder.is_dir(): raise HTTPException(422, {"errors": [f"The folder does not exist: {folder}"]})
        folder = folder.resolve()
        files = sorted((f for f in folder.iterdir() if f.is_file() and photo.is_photo(str(f))), key=lambda f: f.name.lower())[:500]
        if not files: raise HTTPException(422, {"errors": ["There are no photos (JPG, PNG, WebP, BMP) in this folder."]})
        out = Path(b.out_dir.strip()).expanduser() if b.out_dir and b.out_dir.strip() else folder / "edited"
        try: out.mkdir(parents=True, exist_ok=True)
        except OSError as e: raise HTTPException(422, {"errors": [f"Cannot create the output folder: {e}"]})
        if not os.access(out, os.W_OK): raise HTTPException(422, {"errors": [f"Cannot write to this folder: {out}"]})
        out = out.resolve()
        first = files[0]
        try:                                                              # check the blueprint once, on the first photo, before starting
            clip, _ = photo.ingest(str(first), p.root / "photo")
            prep = prepare(b.blueprint, str(clip), st.policy(), p.root, probe(str(clip)))
        except MediaError as e: raise HTTPException(422, {"errors": [str(e)]})
        if not prep.plan: raise HTTPException(422, {"errors": [i.message for i in prep.report.errors] or ["Add at least one effect first."]})
        bid = secrets.token_hex(6)
        _BATCHES[bid] = {"id": bid, "state": "running", "total": len(files), "done": 0, "failed": [], "outputs": [], "current": "", "out_dir": str(out), "cancel": False, "error": None}
        threading.Thread(target=_run_batch, args=(bid, files, out, b.blueprint, fmt, b.quality, out == folder, p, st.policy()), daemon=True).start()
        return _BATCHES[bid]

    @app.get("/api/photo/batch/{bid}", dependencies=[api()])
    def photo_batch_status(bid: str):
        if bid not in _BATCHES: raise HTTPException(404, "Unknown batch")
        return _BATCHES[bid]

    @app.post("/api/photo/batch/{bid}/cancel", dependencies=[api()])
    def photo_batch_cancel(bid: str):
        if bid not in _BATCHES: raise HTTPException(404, "Unknown batch")
        _BATCHES[bid]["cancel"] = True
        return _BATCHES[bid]

    class RenderReq(BaseModel):
        blueprint: Any
        preset: str = "proxy"            # proxy | export
        filename: str = "export"
        encoder: str = "auto"
        t_start: float | None = None
        t_end: float | None = None
        out_dir: str | None = None       # export into this folder instead of the project's exports folder

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
            if b.out_dir and b.out_dir.strip():
                d = Path(b.out_dir.strip()).expanduser()
                if not d.is_dir(): raise HTTPException(422, {"errors": [f"The folder does not exist: {d}"]})
                if not os.access(d, os.W_OK): raise HTTPException(422, {"errors": [f"Cannot write to this folder: {d}"]})
                d = d.resolve()
                out = d / f"{name}.{ext}"
                n = 1
                while out.exists() or (st.source and os.path.normcase(str(out)) == os.path.normcase(str(Path(st.source).resolve()))):
                    n += 1; out = d / f"{name} ({n}).{ext}"          # a file in YOUR folder is never overwritten (and never the original video)
                out = safe_output_path(out, st.source, d)
            else:
                out = safe_output_path(p.exports_dir / f"{name}.{ext}", st.source, p.exports_dir)
            opts = RenderOptions(encoder=b.encoder, t_start=b.t_start, t_end=b.t_end)
            kind = "render"
        job = Job(prep.plan, st.source, str(out), opts, str(p.cache_dir), kind)
        st.jobs.submit(job)
        return {"cached": False, "job": job.to_json(), "output": (str(out) if kind == "render" and b.out_dir and b.out_dir.strip() else f"{'cache' if kind == 'proxy' else 'exports'}/{out.name}")}

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

    # ---------------- key doctor
    class DoctorReq(BaseModel):
        blueprint: Any
        op_id: str

    @app.post("/api/key/doctor", dependencies=[api()])
    def key_doctor(b: DoctorReq):
        """measure the footage and the key of one keying operation: what is wrong and how to fix it (see evp/keydoctor.py)"""
        st.need_project()
        if not st.source: raise HTTPException(409, "No source video")
        prep = _prep(b.blueprint)
        if not prep.plan:
            raise HTTPException(422, {"errors": [i.message for i in prep.report.errors]})
        op = next((o for o in prep.plan.steps if o.id == b.op_id), None)
        if op is None: raise HTTPException(404, "unknown operation")
        if op.type not in ("chroma_key", "background_removal"): raise HTTPException(422, {"errors": ["The key doctor checks chroma key / background removal operations"]})
        from .blueprint import apply_params
        from .keydoctor import diagnose
        base = apply_params("background_removal", {})[0]
        params = {**base, **{k: v for k, v in op.params.items() if k in base}}
        o = prep.plan.blueprint.output
        res = diagnose(st.source, params, op.start_frame, op.end_frame, o.fps, o.width, o.height, prep.plan.edl)
        def covered(add: dict) -> bool:                              # an existing garbage rectangle already covers (>= 90 %) the strip the doctor would add
            q = add["params"]
            for mk in prep.plan.masks.get(op.id, []):
                m = mk.params
                if m["mode"] != "garbage" or m["shape"] != "rect" or m.get("invert"): continue
                ix = max(0.0, min(q["x"] + q["width"], m["x"] + m["width"]) - max(q["x"], m["x"]))
                iy = max(0.0, min(q["y"] + q["height"], m["y"] + m["height"]) - max(q["y"], m["y"]))
                if q["width"] * q["height"] > 0 and ix * iy / (q["width"] * q["height"]) >= 0.9: return True
            return False
        kept = []
        for f in res["findings"]:
            fx = f.get("fix") or {}
            adds = fx.get("add_ops") or []
            if adds and all(covered(a) for a in adds): continue        # already handled by a mask you drew
            if fx.get("set"):                                           # only offer settings this operation really has
                had_mode = "mode" in fx["set"]
                fx["set"] = {k: v for k, v in fx["set"].items() if k in op.params}
                if not fx["set"]:
                    f["fix"] = ({"label": "Add a Background Removal operation in AI mode", "advice": "use_ai_removal"} if had_mode
                                else {"label": "Use Background Removal in mode 'screen' for this control", "advice": "use_screen_key"})
            kept.append(f)
        res["findings"] = kept
        if not any(f["severity"] != "info" for f in kept):
            res["summary"] = "The key looks healthy: nothing needs fixing."
            res["score"] = max(res["score"], 90)
        return res

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

    @app.get("/api/models/{name}", dependencies=[api()])
    def model_status(name: str):
        from .ml import models
        if name not in models.MODELS: raise HTTPException(404, "unknown model")
        return models.status(name)

    @app.post("/api/models/{name}/install", dependencies=[api()])
    def model_install(name: str):
        """start downloading a model in the background (the UI polls GET /api/models/{name})"""
        from .ml import models
        if name not in models.MODELS: raise HTTPException(404, "unknown model")
        return models.install_async(name)

    @app.get("/api/gpu", dependencies=[api()])
    def gpu_status():
        """is the graphics card working right now? (measured on Windows; configured device elsewhere)"""
        from .ml.device import describe
        from .ml.gpumon import monitor
        from .media import gpu_diagnosis
        snap = monitor().snapshot(describe(), [{"state": j.state, "encoder": j._encoder_name(), "current": j.current} for j in st.jobs.jobs.values()])
        try: snap["diagnosis"] = gpu_diagnosis()
        except Exception as e: snap["diagnosis"] = [{"topic": "Diagnosis", "status": "info", "text": f"could not run the checks: {e}"}]
        return snap

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

    # ---------------- media bin: everything ever imported into this project
    @app.get("/api/project/media", dependencies=[api()])
    def media_bin():
        p = st.need_project()
        return mediabin.scan(p, st.source)

    @app.get("/api/media/thumb", dependencies=[api()])
    def media_thumb(src: str, path: str, w: int = 192):
        p = st.need_project()
        w = max(48, min(512, w))
        if src == "asset":
            f = st.policy().resolve(path, p.root)
            if f.parent.resolve() != p.assets_dir.resolve(): raise HTTPException(404, "not a project asset")
        elif src == "source":                                      # only videos this project has used
            known = {os.path.normcase(os.path.abspath(x)) for x in [st.source, *p.data.get("sources_history", [])] if x}
            if os.path.normcase(os.path.abspath(path)) not in known: raise HTTPException(404, "unknown source")
            f = Path(path)
        else:
            raise HTTPException(404, "unknown kind")
        if not f.is_file() or mediabin.kind_of(f) not in ("image", "video"): raise HTTPException(404, "no picture for this file")
        t = mediabin.make_thumb(p, f, w)
        if t is None: raise HTTPException(404, "could not read a frame")
        return FileResponse(t, media_type="image/png", headers={"cache-control": "private, max-age=86400"})

    class AssetPath(BaseModel):
        path: str

    @app.post("/api/project/asset/remove", dependencies=[api()])
    def remove_asset(b: AssetPath):
        """delete the project's own COPY of an imported file (the original you imported is never touched)"""
        p = st.need_project()
        f = st.policy().resolve(b.path, p.root)
        if f.parent.resolve() != p.assets_dir.resolve() or not f.is_file(): raise HTTPException(404, "not a project asset")
        f.unlink()
        return {"ok": True}

    @app.post("/api/project/source/forget", dependencies=[api()])
    def forget_source(b: AssetPath):
        """remove an earlier source video from the Media bin (the reference only; the video file is never touched)"""
        p = st.need_project()
        if st.source and os.path.normcase(os.path.abspath(b.path)) == os.path.normcase(os.path.abspath(st.source)):
            raise HTTPException(409, "This is the video you are editing now. Switch to another video first, or start a new project.")
        if not mediabin.forget_source(p, b.path): raise HTTPException(404, "not in this project's media")
        p.autosave()
        return {"ok": True}

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
