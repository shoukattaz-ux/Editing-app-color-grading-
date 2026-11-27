"""Blueprint parsing + strict validation -> normalised, frame-accurate model."""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .registry import OPERATIONS, Param
from .security import PathPolicy, PathError

SUPPORTED_VERSIONS = ("1.0",)
ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
NAMED_COLORS = {"green": "#00ff00", "blue": "#0000ff", "black": "#000000", "white": "#ffffff",
                "red": "#ff0000", "gray": "#808080", "grey": "#808080"}
ASSET_EXT = {"image": {".png", ".jpg", ".jpeg", ".webp", ".bmp"},
             "video": {".mp4", ".mov", ".mkv", ".webm", ".avi"},
             "lut": {".cube", ".3dl"}}
MAX_OPS = 2000
MAX_CLIPS = 500
MAX_REORDERED_PIECES = 48     # clips out of source order / overlapping each need their own decoder
MAX_BYTES = 5_000_000


@dataclass
class Issue:
    level: str              # error | warning
    code: str
    message: str
    op_id: str | None = None
    path: str | None = None


@dataclass
class VideoInfo:
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool = True
    codec: str = ""
    color_space: str = ""      # ffprobe's tag (bt709, smpte170m, ...); empty = untagged
    color_range: str = ""      # tv | pc | empty


@dataclass
class Asset:
    id: str
    type: str
    path: str                 # as written in the blueprint
    resolved: str | None = None
    missing: bool = False


@dataclass
class Operation:
    id: str
    type: str
    start: float              # seconds, snapped to frame grid
    end: float
    start_frame: int
    end_frame: int
    params: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    enabled: bool = True
    label: str = ""
    keyframes: dict = field(default_factory=dict)       # param name -> [evp.keyframes.Keyframe], times relative to `start`

    def param_at(self, name: str, t_rel: float):
        """value of a parameter at time t_rel (seconds after this operation's start), honouring keyframes"""
        from .keyframes import value_at
        kf = self.keyframes.get(name)
        return value_at(kf, t_rel) if kf else self.params[name]


@dataclass
class Clip:
    id: str
    start: float              # source seconds, snapped to the output frame grid
    end: float
    start_frame: int          # half-open source frame range on the output-fps grid
    end_frame: int
    label: str = ""


@dataclass
class OutputSettings:
    width: int
    height: int
    fps: float
    format: str = "mp4"
    codec: str = "h264"
    crf: int = 20
    video_bitrate: str | None = None
    audio_bitrate: str = "192k"
    preset: str = "medium"
    prores_profile: str = "hq"


@dataclass
class Blueprint:
    version: str
    name: str
    source_video: str
    output: OutputSettings
    assets: dict[str, Asset]
    operations: list[Operation]
    clips: list[Clip] = field(default_factory=list)     # empty = the whole source, uncut
    timeline_duration: float | None = None              # seconds; operations are timed against this


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    blueprint: Blueprint | None = None

    @property
    def errors(self): return [i for i in self.issues if i.level == "error"]
    @property
    def warnings(self): return [i for i in self.issues if i.level == "warning"]
    @property
    def ok(self): return not self.errors and self.blueprint is not None

    def err(self, code, msg, op_id=None, path=None): self.issues.append(Issue("error", code, msg, op_id, path))
    def warn(self, code, msg, op_id=None, path=None): self.issues.append(Issue("warning", code, msg, op_id, path))

    def to_json(self):
        return {"ok": self.ok, "errors": [asdict(i) for i in self.errors],
                "warnings": [asdict(i) for i in self.warnings]}


def parse_time(v: Any) -> float:
    """Seconds as number, '12.5', '1:02.250' or '00:01:02.250'."""
    if isinstance(v, bool): raise ValueError("boolean is not a timestamp")
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            f = float(s)
        elif re.fullmatch(r"(\d+:)?\d{1,2}:\d{1,2}(\.\d+)?", s):
            parts = [float(p) for p in s.split(":")]
            f = 0.0
            for p in parts: f = f * 60 + p
        else:
            raise ValueError(f"unrecognised timestamp {v!r}")
    else:
        raise ValueError(f"unrecognised timestamp {v!r}")
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        raise ValueError("timestamp must be a finite non-negative number")
    return f


def normalize_color(v: Any) -> str:
    if not isinstance(v, str): raise ValueError("colour must be a string")
    s = v.strip().lower()
    s = NAMED_COLORS.get(s, s)
    if not HEX_RE.match(s): raise ValueError(f"invalid colour {v!r} (use '#rrggbb' or a name: {', '.join(NAMED_COLORS)})")
    return "#" + s.lstrip("#")


def _coerce(name: str, spec: Param, v: Any) -> Any:
    t = spec.type
    if t == "number" or t == "integer":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"'{name}' must be a number")
        if v != v or abs(v) == float("inf"): raise ValueError(f"'{name}' must be finite")
        if t == "integer":
            if float(v) != int(v): raise ValueError(f"'{name}' must be an integer")
            v = int(v)
        if spec.min is not None and v < spec.min: raise ValueError(f"'{name}'={v} below minimum {spec.min}")
        if spec.max is not None and v > spec.max: raise ValueError(f"'{name}'={v} above maximum {spec.max}")
        return v
    if t == "boolean":
        if not isinstance(v, bool): raise ValueError(f"'{name}' must be true/false")
        return v
    if t == "string":
        if not isinstance(v, str): raise ValueError(f"'{name}' must be a string")
        if len(v) > 500: raise ValueError(f"'{name}' longer than 500 characters")
        return v
    if t == "color": return normalize_color(v)
    if t == "enum":
        if v not in spec.enum: raise ValueError(f"'{name}'={v!r} not one of {list(spec.enum)}")
        return v
    if t == "asset":
        if not isinstance(v, str) or not ID_RE.match(v): raise ValueError(f"'{name}' must be an asset id")
        return v
    if t == "boxes":
        if not isinstance(v, list) or len(v) > 6: raise ValueError(f"'{name}' must be a list of at most 6 boxes")
        out = []
        for i, z in enumerate(v):
            if not isinstance(z, dict): raise ValueError(f"'{name}'[{i}] must be an object")
            def num(k, d, lo, hi):
                x = z.get(k, d)
                if isinstance(x, bool) or not isinstance(x, (int, float)) or not (lo <= x <= hi): raise ValueError(f"'{name}'[{i}].{k} must be a number from {lo} to {hi}")
                return float(x)
            x0, y0, x1, y1 = num("x0", 0.25, 0, 1), num("y0", 0.1, 0, 1), num("x1", 0.75, 0, 1), num("y1", 0.9, 0, 1)
            if x1 - x0 < 0.02 or y1 - y0 < 0.02: raise ValueError(f"'{name}'[{i}] must be larger than 2% of the frame in both directions (x1 > x0, y1 > y0)")
            side = z.get("side", "top")
            if side not in ("left", "right", "top", "bottom"): raise ValueError(f"'{name}'[{i}].side must be left, right, top or bottom")
            follow = z.get("follow", False)
            if not isinstance(follow, bool): raise ValueError(f"'{name}'[{i}].follow must be true or false")
            scale = z.get("scale", False)
            if not isinstance(scale, bool): raise ValueError(f"'{name}'[{i}].scale must be true or false")
            out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": normalize_color(z.get("color", "#19b8ff")), "side": side,
                        "reach": num("reach", 0.5, 0, 1), "intensity": num("intensity", 1.0, 0, 1.5), "soft": num("soft", 0.3, 0, 1),
                        "follow": follow, "scale": scale})
        return out
    if t == "points" and name == "polygon":
        if not isinstance(v, list) or not (3 <= len(v) <= 64): raise ValueError(f"'{name}' needs 3 to 64 points [x, y]")
        out = []
        for p_ in v:
            if not (isinstance(p_, list) and len(p_) == 2 and all(isinstance(q, (int, float)) and not isinstance(q, bool) and 0 <= q <= 1 for q in p_)):
                raise ValueError(f"'{name}' points must be [x, y] in 0..1 (fractions of the frame)")
            out.append([float(p_[0]), float(p_[1])])
        return out
    if t == "points":
        if not isinstance(v, list) or not (2 <= len(v) <= 16 if name.startswith("curve") else len(v) == 3):
            raise ValueError(f"'{name}' has wrong length")
        if name.startswith("curve"):
            out = []
            for p in v:
                if not (isinstance(p, list) and len(p) == 2 and all(isinstance(q, (int, float)) and not isinstance(q, bool) and 0 <= q <= 1 for q in p)):
                    raise ValueError(f"'{name}' points must be [x,y] in 0..1")
                out.append([float(p[0]), float(p[1])])
            xs = [p[0] for p in out]
            if xs != sorted(xs) or len(set(xs)) != len(xs): raise ValueError(f"'{name}' x values must be strictly increasing")
            return out
        if not all(isinstance(q, (int, float)) and not isinstance(q, bool) and -1 <= q <= 1 for q in v):
            raise ValueError(f"'{name}' values must be within -1..1")
        return [float(q) for q in v]
    raise ValueError(f"internal: unknown param type {t}")


def apply_params(op_type: str, raw: Any) -> tuple[dict, list[str]]:
    """Validate raw params against registry; returns (params with defaults, error messages)."""
    spec = OPERATIONS[op_type]
    errs: list[str] = []
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for k in raw:
        if k not in spec.params: errs.append(f"unknown parameter '{k}' for {op_type}")
    for name, p in spec.params.items():
        if name in raw and raw[name] is not None:
            try: out[name] = _coerce(name, p, raw[name])
            except ValueError as e: errs.append(str(e))
        elif p.required: errs.append(f"missing required parameter '{name}'")
        else: out[name] = p.default
    return out, errs


def apply_keyframes(op_type: str, raw: Any) -> tuple[dict, list[tuple[str, str]], list[tuple[str, str]]]:
    """Validate an operation's `keyframes` object. Returns (parsed, errors, warnings) with (code, message) pairs."""
    from .keyframes import Keyframe, EASINGS, MAX_KEYFRAMES
    spec = OPERATIONS[op_type]
    errs: list[tuple[str, str]] = []
    warns: list[tuple[str, str]] = []
    out: dict[str, list] = {}
    if not isinstance(raw, dict):
        return out, [("INVALID_STRUCTURE", "'keyframes' must be an object mapping parameter names to keyframe arrays")], warns
    animatable = [k for k, p in spec.params.items() if p.animatable]
    for name, items in raw.items():
        ps = spec.params.get(name)
        if ps is None:
            errs.append(("UNKNOWN_PARAMETER", f"keyframes: '{name}' is not a parameter of {op_type}")); continue
        if not ps.animatable:
            why = ps.static_reason or ("only single numbers can be animated" if ps.type not in ("number", "integer") else "not supported")
            errs.append(("PARAM_NOT_ANIMATABLE", f"'{name}' cannot be keyframed: {why}. Animatable for {op_type}: {', '.join(animatable) or 'none'}")); continue
        if not isinstance(items, list) or not items:
            errs.append(("INVALID_KEYFRAME", f"keyframes.{name} must be a non-empty array")); continue
        if len(items) > MAX_KEYFRAMES:
            errs.append(("TOO_MANY_KEYFRAMES", f"keyframes.{name} has {len(items)} keyframes (at most {MAX_KEYFRAMES})")); continue
        parsed, bad = [], False
        for j, it in enumerate(items):
            where = f"keyframes.{name}[{j}]"
            if not isinstance(it, dict):
                errs.append(("INVALID_KEYFRAME", f"{where} must be an object {{time, value, easing}}")); bad = True; continue
            for k in it:
                if k not in ("time", "value", "easing"):
                    warns.append(("UNKNOWN_FIELD", f"{where}: unknown field '{k}' ignored"))
            tm = it.get("time")
            if isinstance(tm, str):
                try: tm = parse_time(tm)
                except ValueError: tm = None
            if isinstance(tm, bool) or not isinstance(tm, (int, float)) or tm != tm or abs(tm) == float("inf"):
                errs.append(("INVALID_KEYFRAME", f"{where}.time must be a finite number of seconds after the operation's start")); bad = True; continue
            v = it.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) == float("inf"):
                errs.append(("INVALID_KEYFRAME", f"{where}.value must be a number")); bad = True; continue
            if (ps.min is not None and v < ps.min) or (ps.max is not None and v > ps.max):
                errs.append(("INVALID_KEYFRAME", f"{where}.value {v} for '{name}' is outside {ps.min} .. {ps.max}")); bad = True; continue
            ea = it.get("easing", "linear")
            if ea not in EASINGS:
                errs.append(("INVALID_KEYFRAME", f"{where}.easing {ea!r} is not one of {list(EASINGS)}")); bad = True; continue
            parsed.append(Keyframe(float(tm), float(v), ea))
        if bad: continue
        parsed.sort(key=lambda k: k.time)
        if any(b.time - a.time < 1e-6 for a, b in zip(parsed, parsed[1:])):
            errs.append(("INVALID_KEYFRAME", f"keyframes.{name} has two keyframes at the same time")); continue
        out[name] = parsed
    return out, errs, warns


def parse_blueprint(text_or_obj: Any, video: VideoInfo | None, policy: PathPolicy | None = None,
                    base_dir: Path | None = None) -> Report:
    rep = Report()
    if isinstance(text_or_obj, (str, bytes)):
        if len(text_or_obj) > MAX_BYTES:
            rep.err("TOO_LARGE", "Blueprint exceeds 5 MB"); return rep
        try: data = json.loads(text_or_obj)
        except json.JSONDecodeError as e:
            rep.err("INVALID_JSON", f"Not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})"); return rep
    else:
        data = text_or_obj
    if not isinstance(data, dict):
        rep.err("INVALID_STRUCTURE", "Blueprint root must be a JSON object"); return rep

    ver = data.get("blueprint_version")
    if ver not in SUPPORTED_VERSIONS:
        rep.err("UNSUPPORTED_VERSION", f"blueprint_version {ver!r} not supported (supported: {', '.join(SUPPORTED_VERSIONS)})"); return rep
    known = {"blueprint_version", "project", "output", "assets", "operations", "clips", "$schema", "video", "strict"}
    for k in data:
        if k not in known: rep.warn("UNKNOWN_FIELD", f"Unknown top-level field '{k}' ignored", path=k)

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    if "project" in data and not isinstance(data["project"], dict):
        rep.err("INVALID_STRUCTURE", "'project' must be an object", path="project")
    name = str(project.get("name", "Untitled"))[:200]
    source_video = str(project.get("source_video", ""))[:500]

    # ---- video & output
    vid = video
    if vid is None:
        rep.warn("NO_VIDEO", "No source video loaded: timestamps cannot be checked against the video duration.")
    out_in = data.get("output") if isinstance(data.get("output"), dict) else {}
    if "output" in data and not isinstance(data["output"], dict):
        rep.err("INVALID_STRUCTURE", "'output' must be an object", path="output")
    w = h = None
    res = out_in.get("resolution", "source")
    if res == "source":
        if vid: w, h = vid.width, vid.height
    elif isinstance(res, str) and re.fullmatch(r"\d{2,5}x\d{2,5}", res):
        w, h = (int(x) for x in res.split("x"))
        if w % 2 or h % 2 or w > 7680 or h > 4320 or w < 64 or h < 64:
            rep.err("INVALID_PARAMETER", f"output.resolution {res}: sides must be even, between 64 and 7680x4320", path="output.resolution")
    else:
        rep.err("INVALID_PARAMETER", "output.resolution must be 'source' or 'WIDTHxHEIGHT'", path="output.resolution")
    fps = out_in.get("fps", "source")
    if fps == "source": fps_v = vid.fps if vid else 30.0
    elif isinstance(fps, (int, float)) and not isinstance(fps, bool) and 1 <= fps <= 120: fps_v = float(fps)
    else:
        rep.err("INVALID_PARAMETER", "output.fps must be 'source' or a number 1..120", path="output.fps"); fps_v = vid.fps if vid else 30.0
    fmt = out_in.get("format", "mp4")
    if fmt not in ("mp4", "mov", "webm"): rep.err("INVALID_PARAMETER", "output.format must be 'mp4', 'mov' or 'webm'", path="output.format"); fmt = "mp4"
    codec = out_in.get("codec", "h264")
    if codec not in ("h264", "h265", "prores", "prores4444", "vp9_alpha"):
        rep.err("INVALID_PARAMETER", "output.codec must be 'h264', 'h265', 'prores', 'prores4444' (transparent) or 'vp9_alpha' (transparent)", path="output.codec"); codec = "h264"
    if codec in ("prores", "prores4444") and fmt != "mov":
        rep.err("INVALID_PARAMETER", "ProRes needs output.format 'mov'", path="output.format")
    if codec == "vp9_alpha" and fmt != "webm":
        rep.err("INVALID_PARAMETER", "codec 'vp9_alpha' needs output.format 'webm'", path="output.format")
    if codec in ("h264", "h265") and fmt == "webm":
        rep.err("INVALID_PARAMETER", "output.format 'webm' needs codec 'vp9_alpha'", path="output.format")
    prores_profile = out_in.get("prores_profile", "hq")
    if prores_profile not in ("proxy", "lt", "standard", "hq"):
        rep.err("INVALID_PARAMETER", "output.prores_profile must be proxy, lt, standard or hq", path="output.prores_profile"); prores_profile = "hq"
    crf = out_in.get("crf", 20)
    if not isinstance(crf, int) or isinstance(crf, bool) or not 0 <= crf <= 51:
        rep.err("INVALID_PARAMETER", "output.crf must be an integer 0..51", path="output.crf"); crf = 20
    vb = out_in.get("video_bitrate")
    if vb is not None and not (isinstance(vb, str) and re.fullmatch(r"\d{2,6}[kKmM]", vb)):
        rep.err("INVALID_PARAMETER", "output.video_bitrate must look like '8M' or '5000k'", path="output.video_bitrate"); vb = None
    ab = out_in.get("audio_bitrate", "192k")
    if not (isinstance(ab, str) and re.fullmatch(r"\d{2,4}k", ab)):
        rep.err("INVALID_PARAMETER", "output.audio_bitrate must look like '192k'", path="output.audio_bitrate"); ab = "192k"
    preset = out_in.get("preset", "medium")
    if preset not in ("ultrafast", "veryfast", "fast", "medium", "slow"):
        rep.err("INVALID_PARAMETER", "output.preset invalid", path="output.preset"); preset = "medium"
    output = OutputSettings(w or 1920, h or 1080, fps_v, fmt, codec, crf, vb, ab, preset, prores_profile)
    if vid and w and h and abs(w / h - vid.width / vid.height) > 0.01:
        rep.warn("ASPECT_CHANGE", f"Output aspect {w}x{h} differs from source {vid.width}x{vid.height}; frames will be letterboxed.")
    if vid and fps_v > vid.fps * 1.01:
        rep.warn("FPS_UPCONVERT", f"Output fps {fps_v:g} exceeds source {vid.fps:g}: frames are duplicated, no real smoothness gained.")

    # ---- assets
    assets: dict[str, Asset] = {}
    raw_assets = data.get("assets", [])
    if not isinstance(raw_assets, list):
        rep.err("INVALID_STRUCTURE", "'assets' must be an array", path="assets"); raw_assets = []
    for i, a in enumerate(raw_assets):
        p = f"assets[{i}]"
        if not isinstance(a, dict): rep.err("INVALID_STRUCTURE", f"{p} must be an object", path=p); continue
        aid, atype, apath = a.get("id"), a.get("type"), a.get("path")
        if not isinstance(aid, str) or not ID_RE.match(aid):
            rep.err("INVALID_ID", f"{p}.id must match [A-Za-z0-9_.-]{{1,64}}", path=p); continue
        if aid in assets: rep.err("DUPLICATE_ID", f"Duplicate asset id '{aid}'", path=p); continue
        if atype not in ASSET_EXT:
            rep.err("INVALID_PARAMETER", f"Asset '{aid}': type must be one of {sorted(ASSET_EXT)}", path=p); continue
        if not isinstance(apath, str):
            rep.err("INVALID_PARAMETER", f"Asset '{aid}': path must be a string", path=p); continue
        if Path(apath).suffix.lower() not in ASSET_EXT[atype]:
            rep.err("INVALID_ASSET_EXT", f"Asset '{aid}': '{Path(apath).suffix}' is not a valid {atype} file ({', '.join(sorted(ASSET_EXT[atype]))})", path=p); continue
        asset = Asset(aid, atype, apath)
        if policy:
            try:
                r = policy.resolve(apath, base_dir)
                asset.resolved = str(r)
                if not r.is_file():
                    asset.missing = True
                    rep.err("MISSING_ASSET", f"Asset '{aid}' not found: {apath}", path=p)
            except PathError as e:
                asset.missing = True
                rep.err("UNSAFE_PATH", f"Asset '{aid}': {e}", path=p)
        assets[aid] = asset

    # ---- clips (edit decision list on the source video)
    from . import edl as _edl
    fps_grid = output.fps if output.fps else 30.0
    clips: list[Clip] = []
    raw_clips = data.get("clips")
    if raw_clips is not None and not isinstance(raw_clips, list):
        rep.err("INVALID_STRUCTURE", "'clips' must be an array", path="clips"); raw_clips = None
    if raw_clips is not None and len(raw_clips) > MAX_CLIPS:
        rep.err("TOO_MANY_CLIPS", f"At most {MAX_CLIPS} clips"); raw_clips = None
    if raw_clips is not None and len(raw_clips) == 0:
        rep.err("EMPTY_TIMELINE", "'clips' is empty: nothing would be left to render (omit 'clips' to use the whole video)", path="clips")
    src_frames = round(vid.duration * fps_grid) if vid else None
    clip_ids: set[str] = set()
    for i, c in enumerate(raw_clips or []):
        p = f"clips[{i}]"
        if not isinstance(c, dict): rep.err("INVALID_STRUCTURE", f"{p} must be an object", path=p); continue
        cid = c.get("id")
        if not isinstance(cid, str) or not ID_RE.match(cid):
            rep.err("INVALID_ID", f"{p}.id missing or invalid (use [A-Za-z0-9_.-], max 64)", path=p); continue
        if cid in clip_ids: rep.err("DUPLICATE_ID", f"Duplicate clip id '{cid}'", cid, p); continue
        clip_ids.add(cid)
        for k in c:
            if k not in {"id", "start", "end", "label"}:
                rep.warn("UNKNOWN_FIELD", f"Unknown field '{k}' on clip '{cid}' ignored", cid, f"{p}.{k}")
        try: cs = parse_time(c.get("start", 0))
        except ValueError as e: rep.err("INVALID_TIMESTAMP", f"clip start: {e}", cid, f"{p}.start"); continue
        if c.get("end") is None:
            if vid is None: rep.err("INVALID_TIMESTAMP", "clip end omitted but video duration unknown", cid, f"{p}.end"); continue
            ce = vid.duration
        else:
            try: ce = parse_time(c["end"])
            except ValueError as e: rep.err("INVALID_TIMESTAMP", f"clip end: {e}", cid, f"{p}.end"); continue
        if ce <= cs: rep.err("INVALID_TIMESTAMP", f"clip end ({ce:g}s) must be greater than start ({cs:g}s)", cid, p); continue
        csf, cef = round(cs * fps_grid), round(ce * fps_grid)
        if src_frames is not None:
            if csf >= src_frames: rep.err("TIME_OUT_OF_RANGE", f"clip start {cs:g}s is at/after the end of the source video ({vid.duration:.3f}s)", cid, f"{p}.start"); continue
            if cef > src_frames + 1: rep.err("TIME_OUT_OF_RANGE", f"clip end {ce:g}s exceeds the source video ({vid.duration:.3f}s)", cid, f"{p}.end"); continue
            cef = min(cef, src_frames)
        if cef <= csf: rep.err("INVALID_TIMESTAMP", "clip is shorter than one frame", cid, p); continue
        clips.append(Clip(cid, csf / fps_grid, cef / fps_grid, csf, cef, str(c.get("label", ""))[:120]))
    pieces = [(c.start_frame, c.end_frame) for c in clips]
    if clips and not _edl.is_in_order(pieces) and len(clips) > MAX_REORDERED_PIECES:
        rep.err("TOO_MANY_REORDERED_CLIPS", f"Clips that are reordered or overlap in the source need a separate decoder each; at most "
                f"{MAX_REORDERED_PIECES} are supported in that case (you have {len(clips)}). Keep clips in source order or reduce them.", path="clips")
    timeline_dur: float | None = None
    if clips: timeline_dur = _edl.total_frames(pieces) / fps_grid
    elif vid: timeline_dur = vid.duration
    if clips and vid and _edl.is_identity(pieces, src_frames):
        clips = []; timeline_dur = vid.duration              # a single clip covering everything is just the video

    # ---- operations
    raw_ops = data.get("operations")
    if not isinstance(raw_ops, list):
        rep.err("INVALID_STRUCTURE", "'operations' must be an array"); return rep
    if len(raw_ops) > MAX_OPS:
        rep.err("TOO_MANY_OPERATIONS", f"At most {MAX_OPS} operations"); return rep
    if not raw_ops: rep.warn("NO_OPERATIONS", "Blueprint contains no operations")
    ops: list[Operation] = []
    seen: set[str] = set()
    fps_for_grid = fps_grid
    for i, o in enumerate(raw_ops):
        p = f"operations[{i}]"
        if not isinstance(o, dict): rep.err("INVALID_STRUCTURE", f"{p} must be an object", path=p); continue
        oid = o.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid):
            rep.err("INVALID_ID", f"{p}.id missing or invalid (use [A-Za-z0-9_.-], max 64)", path=p); continue
        if oid in seen: rep.err("DUPLICATE_ID", f"Duplicate operation id '{oid}'", oid, p); continue
        seen.add(oid)
        otype = o.get("type")
        spec = OPERATIONS.get(otype) if isinstance(otype, str) else None
        if spec is None:
            rep.err("UNKNOWN_OPERATION", f"Operation type {otype!r} does not exist. Supported: {', '.join(k for k, v in OPERATIONS.items() if v.supported)}", oid, p); continue
        if not spec.supported:
            rep.err("UNSUPPORTED_OPERATION", f"'{otype}' is recognised but not available: {spec.unsupported_reason}", oid, p); continue
        for k in o:
            if k not in {"id", "type", "start", "end", "parameters", "depends_on", "enabled", "label", "note", "keyframes"}:
                rep.warn("UNKNOWN_FIELD", f"Unknown field '{k}' on operation '{oid}' ignored", oid, f"{p}.{k}")
        # timestamps
        try: s = parse_time(o.get("start", 0))
        except ValueError as e: rep.err("INVALID_TIMESTAMP", f"start: {e}", oid, f"{p}.start"); continue
        dur = timeline_dur
        if o.get("end") is None:
            if dur is None: rep.err("INVALID_TIMESTAMP", "end omitted but video duration unknown", oid, f"{p}.end"); continue
            e_ = dur
        else:
            try: e_ = parse_time(o["end"])
            except ValueError as e: rep.err("INVALID_TIMESTAMP", f"end: {e}", oid, f"{p}.end"); continue
        if e_ <= s: rep.err("INVALID_TIMESTAMP", f"end ({e_:g}s) must be greater than start ({s:g}s)", oid, p); continue
        if dur is not None:
            tol = 0.5 / fps_for_grid
            if s >= dur - tol: rep.err("TIME_OUT_OF_RANGE", f"start {s:g}s is at/after the end of the timeline ({dur:.3f}s)", oid, f"{p}.start"); continue
            if e_ > dur + 1.0 / fps_for_grid:
                rep.err("TIME_OUT_OF_RANGE", f"end {e_:g}s exceeds the timeline duration {dur:.3f}s" + (" (operation times are on the cut timeline, not the source video)" if clips else ""), oid, f"{p}.end"); continue
            e_ = min(e_, dur)
        sf, ef = round(s * fps_for_grid), round(e_ * fps_for_grid)
        if ef <= sf: rep.err("INVALID_TIMESTAMP", "operation is shorter than one frame", oid, p); continue
        if abs(sf / fps_for_grid - s) > 1e-3 or abs(ef / fps_for_grid - e_) > 1e-3:
            rep.warn("TIME_SNAPPED", f"Timestamps snapped to frame grid ({sf / fps_for_grid:.4f}s-{ef / fps_for_grid:.4f}s)", oid, p)
        # params
        if "parameters" in o and not isinstance(o["parameters"], dict):
            rep.err("INVALID_STRUCTURE", "'parameters' must be an object", oid, f"{p}.parameters"); continue
        params, perrs = apply_params(otype, o.get("parameters"))
        for m in perrs: rep.err("INVALID_PARAMETER", m, oid, f"{p}.parameters")
        if perrs: continue
        kfs: dict = {}
        if o.get("keyframes") is not None:
            kfs, kerrs, kwarns = apply_keyframes(otype, o["keyframes"])
            for code, msg in kerrs: rep.err(code, msg, oid, f"{p}.keyframes")
            for code, msg in kwarns: rep.warn(code, msg, oid, f"{p}.keyframes")
            if kerrs: continue
        # asset refs
        for pn, ps in spec.params.items():
            if ps.type == "asset" and params.get(pn):
                a = assets.get(params[pn])
                if a is None:
                    rep.err("UNKNOWN_ASSET", f"Parameter '{pn}' references unknown asset '{params[pn]}'", oid, f"{p}.parameters.{pn}"); continue
                if a.type not in ps.asset_types:
                    rep.err("ASSET_TYPE_MISMATCH", f"Asset '{a.id}' is a {a.type}; '{pn}' needs {'/'.join(ps.asset_types)}", oid, f"{p}.parameters.{pn}")
        hair_on = otype == "background_removal" and params["mode"] == "screen" and params.get("hair_detail", 0.0) > 0
        if otype == "background_removal" and (params["mode"] == "ai" or hair_on):
            from .ml.models import runtime_available, is_installed, MODELS
            ok, why = runtime_available()
            if not ok:
                rep.err("MODEL_RUNTIME_MISSING", ("Hair detail needs the AI matting runtime: " if hair_on else "AI background removal unavailable: ") + why, oid,
                        f"{p}.parameters.{'hair_detail' if hair_on else 'mode'}"); continue
            if not is_installed(params["model"]):
                m = MODELS[params["model"]]
                rep.warn("MODEL_DOWNLOAD", f"Model '{m.name}' ({m.size // 1_000_000} MB, {m.license}) will be downloaded on first use", oid)
        if otype in ("face_tone_correction", "face_relighting"):
            from .ml.models import is_installed, MODELS
            if not hasattr(__import__("cv2"), "FaceDetectorYN"):
                rep.err("MODEL_RUNTIME_MISSING", "Face operations need OpenCV >= 4.8 with the DNN module (pip install opencv-python-headless)", oid, p); continue
            if not is_installed("yunet"):
                rep.warn("MODEL_DOWNLOAD", f"Face detector model ({MODELS['yunet'].size // 1000} KB, MIT) will be downloaded on first use", oid)
        if otype == "face_restoration":
            from .ml.models import runtime_available, is_installed, MODELS, providers
            ok, why = runtime_available()
            try:
                import onnx  # noqa: F401  (needed once, to convert the official weights)
            except Exception:
                ok, why = False, "the 'onnx' package is not installed (pip install onnx)"
            if not hasattr(__import__("cv2"), "FaceDetectorYN"): ok, why = False, "OpenCV >= 4.8 with the DNN module is needed (pip install opencv-python-headless)"
            if not ok:
                rep.err("MODEL_RUNTIME_MISSING", f"AI face restoration unavailable: {why}", oid, p); continue
            if not is_installed("gfpgan"):
                rep.warn("MODEL_DOWNLOAD", f"The face restoration model ({MODELS['gfpgan'].size // 1_000_000} MB, {MODELS['gfpgan'].license}) is downloaded once and converted before first use (button in the effect's Properties)", oid)
            from .ml.restore import estimate_seconds_per_frame as _est
            gpu = providers()[:1] != ["CPUExecutionProvider"]
            est = _est(gpu, 1) * (ef - sf)
            if est > 60:
                rep.warn("AI_SLOW", f"AI face restoration of {ef - sf} frames is estimated at ~{est / 60:.0f} min for one face per frame on "
                         f"{'the graphics card' if gpu else 'the processor (no GPU in use; see Settings > AI device)'}; the result is cached for later previews and exports.", oid)
        if otype == "ai_upscale":
            from .ml.models import runtime_available, is_installed, MODELS, providers
            ok, why = runtime_available()
            try:
                import onnx  # noqa: F401  (needed once, to convert the official weights)
            except Exception:
                ok, why = False, "the 'onnx' package is not installed (pip install onnx)"
            if not ok:
                rep.err("MODEL_RUNTIME_MISSING", f"AI upscaling unavailable: {why}", oid, p); continue
            from .ml.upscale import UPSCALE_MODELS
            wname = UPSCALE_MODELS[params["model"]][0]
            if not is_installed(wname):
                rep.warn("MODEL_DOWNLOAD", f"Real-ESRGAN weights ({MODELS[wname].size / 1e6:.0f} MB, BSD-3) will be downloaded and converted on first use", oid)
            if params["model"] != "general" and params["denoise"] != 0.5:
                rep.warn("PARAM_IGNORED", "'denoise' only applies to the 'general' model", oid)
            if vid:
                from .ml.upscale import fit_geometry, estimate_seconds_per_frame
                fw, fh, _, _ = fit_geometry(vid.width, vid.height, output.width, output.height)
                need = max(fw / vid.width, fh / vid.height)
                if need <= 1.0:
                    rep.warn("UPSCALE_NOT_NEEDED", f"Output ({output.width}x{output.height}) is not larger than the source ({vid.width}x{vid.height}); "
                             "AI upscaling will only re-draw detail. Set output.resolution higher (e.g. 1920x1080 or 3840x2160).", oid)
                gpu = providers()[:1] != ["CPUExecutionProvider"]
                est = estimate_seconds_per_frame(vid.width, vid.height, params["quality"], need, gpu, params["model"]) * (ef - sf)
                if est > 60:
                    rep.warn("AI_SLOW", f"AI upscaling of {ef - sf} frames is estimated at ~{est / 60:.0f} min on "
                             f"{'GPU' if gpu else 'CPU (no GPU in use: on Windows it needs the DirectML runtime; see Settings > AI device)'}; the result is cached for later previews/exports.", oid)
        if spec.needs_audio and vid and not vid.has_audio:
            rep.warn("NO_AUDIO", "Source has no audio track; this operation has no effect", oid, p)
        deps = o.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            rep.err("INVALID_STRUCTURE", "'depends_on' must be an array of operation ids", oid, f"{p}.depends_on"); continue
        en = o.get("enabled", True)
        if not isinstance(en, bool): rep.err("INVALID_PARAMETER", "'enabled' must be boolean", oid, p); continue
        if otype == "face_relighting" and params.get("light_master", 1.0) != 1.0:          # master light: the three relighting strengths together (each capped at its own maximum)
            for pn in ("key_intensity", "fill", "shadow_depth"):
                if isinstance(params.get(pn), (int, float)): params[pn] = min(float(spec.params[pn].max), float(params[pn]) * float(params["light_master"]))
        ops.append(Operation(oid, otype, sf / fps_for_grid, ef / fps_for_grid, sf, ef, params, list(deps), en,
                             str(o.get("label", ""))[:120], kfs))

    bp = Blueprint(ver, name, source_video, output, assets, ops, clips, timeline_dur)
    _check_graph(bp, rep, set(seen))
    rep.blueprint = bp
    return rep


def _check_graph(bp: Blueprint, rep: Report, declared_ids: set[str]) -> None:
    byid = {o.id: o for o in bp.operations}
    for o in bp.operations:
        for d in o.depends_on:
            if d == o.id: rep.err("DEPENDENCY_CYCLE", "Operation depends on itself", o.id)
            elif d not in byid:
                rep.err("UNKNOWN_DEPENDENCY",
                        f"depends_on '{d}' " + ("failed validation above" if d in declared_ids else "does not exist"), o.id)
    # cycles (DFS)
    state: dict[str, int] = {}
    def visit(n: str, stack: list[str]) -> bool:
        if state.get(n) == 2: return False
        if state.get(n) == 1:
            rep.err("DEPENDENCY_CYCLE", "Dependency cycle: " + " -> ".join(stack[stack.index(n):] + [n]), n); return True
        state[n] = 1
        for d in byid[n].depends_on:
            if d in byid and visit(d, stack + [n]): state[n] = 2; return True
        state[n] = 2; return False
    for n in byid: visit(n, [])
    # dependency must cover dependant's range when it supplies pixels/alpha
    for o in bp.operations:
        for d in o.depends_on:
            dep = byid.get(d)
            if dep and OPERATIONS[dep.type].provides_alpha and (o.start_frame < dep.start_frame or o.end_frame > dep.end_frame):
                rep.err("DEPENDENCY_RANGE", f"'{o.id}' ({o.start:g}-{o.end:g}s) is not fully covered by its dependency '{d}' ({dep.start:g}-{dep.end:g}s)", o.id)
            if dep and not dep.enabled and o.enabled:
                rep.warn("DEPENDENCY_DISABLED", f"'{o.id}' depends on disabled operation '{d}'", o.id)
