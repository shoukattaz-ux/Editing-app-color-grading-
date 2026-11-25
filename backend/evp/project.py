"""Non-destructive project storage. The original video is only ever referenced, never modified."""
from __future__ import annotations
from . import fsutil
import json, os, re, shutil, time
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
EMPTY_BLUEPRINT = {"blueprint_version": "1.0", "project": {"name": "Untitled"}, "output": {"resolution": "source", "fps": "source"},
                   "assets": [], "operations": []}


class ProjectError(RuntimeError):
    pass


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    # flush to disk through the SAME writable handle: Windows refuses fsync on a read-only handle ("Bad file descriptor")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    fsutil.replace(tmp, path)


def safe_name(n: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.\-]", "_", n).strip(" .")[:80]
    return s or "Untitled"


class Project:
    def __init__(self, root: Path, data: dict):
        self.root, self.data = root, data

    # --- layout
    @property
    def project_file(self): return self.root / "project.json"
    @property
    def autosave_file(self): return self.root / "autosave.json"
    @property
    def assets_dir(self): return self.root / "assets"
    @property
    def exports_dir(self): return self.root / "exports"
    @property
    def cache_dir(self): return self.root / "cache"
    @property
    def versions_dir(self): return self.root / "versions"

    # --- lifecycle
    @classmethod
    def create(cls, parent: str | Path, name: str, source_video: str | None = None) -> "Project":
        root = Path(parent).resolve() / (safe_name(name) + ".evproj")
        if root.exists(): raise ProjectError(f"Project already exists: {root}")
        for d in ("assets", "exports", "cache", "versions"): (root / d).mkdir(parents=True)
        bp = json.loads(json.dumps(EMPTY_BLUEPRINT)); bp["project"]["name"] = name
        data = {"format_version": FORMAT_VERSION, "name": name, "created": time.time(), "modified": time.time(),
                "version": 0, "source": {"path": source_video}, "blueprint": bp, "ui": {}}
        p = cls(root, data); p.save(snapshot=False)
        return p

    @classmethod
    def open(cls, root: str | Path, restore: bool = False) -> "Project":
        """restore=True: if the last session ended with edits that were auto-saved but never saved, open THOSE (the exact state you left).
        The auto-save file is kept until you save or revert, so a second crash cannot lose the work either."""
        root = Path(root).resolve()
        pf = root / "project.json"
        if not pf.is_file(): raise ProjectError(f"Not a project folder (no project.json): {root}")
        try: data = json.loads(pf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e: raise ProjectError(f"project.json is corrupted: {e}. Try restoring a version from versions/.")
        if data.get("format_version", 0) > FORMAT_VERSION:
            raise ProjectError("Project was created by a newer version of this application")
        p = cls(root, data)
        p.saved_blueprint = data.get("blueprint")
        p.restored_at = None
        if restore and p.recovery_available():
            try:
                a = json.loads(p.autosave_file.read_text(encoding="utf-8"))
                if isinstance(a, dict) and isinstance(a.get("blueprint"), dict):
                    p.data = a; p.restored_at = p.autosave_file.stat().st_mtime
            except (json.JSONDecodeError, OSError):
                pass                                            # a half-written auto-save (power cut mid-write): the last saved state is used
        ui = p.read_ui()
        if ui is not None: p.data["ui"] = ui
        return p

    saved_blueprint: dict | None = None
    restored_at: float | None = None

    # --- view state (playhead, tab, zoom, selection ...) is kept apart from the document so it can be saved often and cheaply
    @property
    def ui_file(self): return self.root / "ui.json"

    def read_ui(self) -> dict | None:
        try:
            d = json.loads(self.ui_file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def write_ui(self, ui: dict) -> None:
        self.data["ui"] = ui
        _atomic_write(self.ui_file, ui)

    def revert_to_saved(self) -> None:
        """drop the auto-saved edits and go back to the last saved version"""
        self.autosave_file.unlink(missing_ok=True)
        self.data = json.loads(self.project_file.read_text(encoding="utf-8"))
        self.saved_blueprint = self.data.get("blueprint"); self.restored_at = None

    def recovery_available(self) -> bool:
        a, p = self.autosave_file, self.project_file
        return a.is_file() and a.stat().st_mtime >= p.stat().st_mtime      # save() deletes the auto-save, so a surviving file newer-or-equal is real unsaved work

    def recover(self) -> None:
        self.data = json.loads(self.autosave_file.read_text(encoding="utf-8"))
        self.autosave_file.unlink(missing_ok=True)

    def discard_autosave(self) -> None:
        self.autosave_file.unlink(missing_ok=True)

    def update(self, blueprint: dict | None = None, source: str | None = None, ui: dict | None = None, name: str | None = None) -> None:
        if blueprint is not None: self.data["blueprint"] = blueprint
        if source is not None: self.data["source"] = {"path": source}
        if ui is not None: self.data["ui"] = ui
        if name: self.data["name"] = name
        self.data["modified"] = time.time()

    def save(self, snapshot: bool = True) -> int:
        self.data["version"] = int(self.data.get("version", 0)) + 1
        self.data["modified"] = time.time()
        _atomic_write(self.project_file, self.data)
        if snapshot:
            self.versions_dir.mkdir(exist_ok=True)
            _atomic_write(self.versions_dir / f"v{self.data['version']:05d}.json", self.data)
            for old in sorted(self.versions_dir.glob("v*.json"))[:-50]: old.unlink()
        self.autosave_file.unlink(missing_ok=True)
        self.saved_blueprint = self.data.get("blueprint"); self.restored_at = None
        return self.data["version"]

    def autosave(self) -> None:
        _atomic_write(self.autosave_file, {**self.data, "modified": time.time()})

    def save_as(self, parent: str | Path, name: str) -> "Project":
        new = Project.create(parent, name)
        for sub in ("assets",):
            for f in (self.root / sub).glob("*"):
                if f.is_file(): shutil.copy2(f, new.root / sub / f.name)
        new.data.update({"source": self.data.get("source"), "blueprint": self.data["blueprint"], "ui": self.data.get("ui", {}), "name": name})
        new.save()
        return new

    # --- versions
    def versions(self) -> list[dict]:
        out = []
        for f in sorted(self.versions_dir.glob("v*.json"), reverse=True):
            try: d = json.loads(f.read_text(encoding="utf-8")); out.append({"file": f.name, "version": d.get("version"), "modified": d.get("modified"),
                                                          "operations": len(d.get("blueprint", {}).get("operations", []))})
            except Exception: out.append({"file": f.name, "corrupt": True})
        return out

    def restore_version(self, filename: str) -> None:
        if not re.fullmatch(r"v\d{5}\.json", filename): raise ProjectError("invalid version name")
        f = self.versions_dir / filename
        if not f.is_file(): raise ProjectError("version not found")
        d = json.loads(f.read_text(encoding="utf-8"))
        self.data["blueprint"], self.data["source"] = d["blueprint"], d.get("source", self.data.get("source"))
        self.save()

    # --- assets
    def import_asset(self, src: str | Path) -> str:
        src = Path(src)
        if not src.is_file(): raise ProjectError(f"Asset not found: {src}")
        dest = self.assets_dir / safe_name(src.name)
        i = 1
        while dest.exists():
            dest = self.assets_dir / f"{src.stem}_{i}{src.suffix}"; i += 1
        shutil.copy2(src, dest)
        return f"assets/{dest.name}"

    def missing(self) -> dict:
        miss = {"source": None, "assets": []}
        sp = (self.data.get("source") or {}).get("path")
        if sp and not Path(sp).is_file(): miss["source"] = sp
        for a in self.data["blueprint"].get("assets", []):
            pth = a.get("path", "")
            cand = Path(pth) if os.path.isabs(pth) else self.root / pth
            if not cand.is_file(): miss["assets"].append({"id": a.get("id"), "path": pth})
        return miss

    def relink(self, kind: str, new_path: str, asset_id: str | None = None) -> None:
        if not Path(new_path).is_file(): raise ProjectError("relink target does not exist")
        if kind == "source":
            self.data["source"] = {"path": str(Path(new_path).resolve())}
        elif kind == "asset":
            for a in self.data["blueprint"].get("assets", []):
                if a.get("id") == asset_id:
                    a["path"] = self.import_asset(new_path); return
            raise ProjectError(f"unknown asset {asset_id}")
        else:
            raise ProjectError("kind must be 'source' or 'asset'")


class RecentProjects:
    def __init__(self, config_file: Path):
        self.f = Path(config_file)

    def _raw(self) -> list[dict]:
        try: items = json.loads(self.f.read_text(encoding="utf-8"))
        except Exception: return []
        return [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]

    def list(self) -> list[dict]:
        """newest activity first; each entry says when it was last touched and whether it holds work that was never saved"""
        out = []
        for i in self._raw():
            root = Path(i["path"])
            if not root.is_dir(): continue
            pf, af = root / "project.json", root / "autosave.json"
            try: pm = pf.stat().st_mtime
            except OSError: continue
            try: am = af.stat().st_mtime
            except OSError: am = 0.0
            out.append({**i, "modified": max(pm, am, float(i.get("opened", 0))), "unsaved": am > 0 and am >= pm})
        out.sort(key=lambda r: r["modified"], reverse=True)
        return out

    def touch_if_stale(self, path: str, name: str, every: float = 30.0) -> None:
        """a project being worked on stays in Recent (and at the top) even if it was never 'opened' this session; cheap: at most every 30 s"""
        cur = next((i for i in self._raw() if i["path"] == str(path)), None)
        if cur is None or time.time() - float(cur.get("opened", 0)) > every: self.add(path, name)

    def add(self, path: str, name: str) -> None:
        items = [i for i in self._raw() if i["path"] != str(path)]
        items.insert(0, {"path": str(path), "name": name, "opened": time.time()})
        self.f.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.f.with_suffix(self.f.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(items[:15])); fh.flush(); os.fsync(fh.fileno())
        fsutil.replace(tmp, self.f)
