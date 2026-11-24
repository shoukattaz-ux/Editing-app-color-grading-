"""Lenient blueprint intake.

parse_blueprint() is strict on purpose: it is the one place that decides what may run. This module sits IN FRONT of it
and turns a blueprint written by another AI (wrong version label, invented parameters, nested option groups, 0-100 scales,
out-of-range numbers, unknown operation names) into the closest blueprint that strict validation accepts - and tells the
user, one warning per change, exactly what was changed or left out. Nothing is silently skipped or faked.

  1. repair()        fixes what can be fixed without guessing (clamp, drop unknown, translate known foreign shapes)
  2. parse_blueprint the unchanged strict validator
  3. quarantine      an operation / asset / clip that still has an error is set aside (with the reason) and the rest goes on

Only problems that make the whole file unreadable stay fatal (not JSON, root is not an object, over the size limit).
Security is untouched: unsafe asset paths are still refused by the strict validator; the asset (and what needs it) is dropped.
"""
from __future__ import annotations
import copy, json, re
from typing import Any
from .blueprint import (Issue, Report, VideoInfo, parse_blueprint, parse_time, apply_keyframes, _coerce, ID_RE, SUPPORTED_VERSIONS, MAX_BYTES)
from .registry import OPERATIONS

# operation names other tools / AIs commonly use -> the real operation
OP_ALIASES = {
    "color_grade": "color_grading", "colour_grading": "color_grading", "colour_grade": "color_grading", "color_correction": "color_grading",
    "colour_correction": "color_grading", "grade": "color_grading", "grading": "color_grading", "professional_color_grade": "color_grading",
    "sharpen": "video_enhancement", "denoise": "video_enhancement", "enhance": "video_enhancement", "enhancement": "video_enhancement",
    "face_correction": "face_tone_correction", "skin_tone_correction": "face_tone_correction", "skin_tone": "face_tone_correction", "face_tone": "face_tone_correction",
    "relight": "face_relighting", "face_relight": "face_relighting",
    "remove_background": "background_removal", "bg_removal": "background_removal", "background_remove": "background_removal",
    "green_screen_removal": "background_removal", "green_screen": "chroma_key", "greenscreen": "chroma_key", "chromakey": "chroma_key", "key": "chroma_key",
    "replace_background": "background_replacement", "bg_replacement": "background_replacement", "background_replace": "background_replacement",
    "text": "text_overlay", "title": "text_overlay", "caption": "text_overlay", "subtitle": "text_overlay",
    "volume": "audio_gain", "gain": "audio_gain", "upscale": "ai_upscale", "super_resolution": "ai_upscale", "studio": "studio_look", "look": "studio_look",
}

# nested option groups -> the colour-grading control they correspond to: name -> (target op, target param, scale rule)
_CG = {"exposure": "ev", "contrast": "unit", "saturation": "unit", "brightness": "p1", "highlights": "p1", "shadows": "p1", "whites": "p1", "blacks": "p1",
       "temperature": "raw", "tint": "raw", "vibrance": "vib", "texture": "p1", "clarity": "p1", "dehaze": "p1", "gamma": "gamma"}
_ALIAS_PARAM = {"temp": "temperature", "white_balance_temperature": "temperature", "sat": "saturation", "vibrancy": "vibrance", "exposure_ev": "exposure"}
_VE = {"sharpening": ("sharpen", "sharp"), "sharpen": ("sharpen", "sharp"), "noise_reduction": ("denoise", "den"), "denoise": ("denoise", "den")}
_COLOUR_GROUPS = {"tone", "white_balance", "color", "colour", "detail", "basic", "light", "presence", "effects"}
_FACE_GROUPS = {"skin_tone_protection", "face_lighting", "subject_adjustments", "face", "skin"}


def _num(v) -> bool: return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _scale(rule: str, v: float) -> float:
    if rule == "ev": return v if abs(v) <= 3 else v / 100 * 3         # +-3 EV, a 0-100 slider = fraction of that
    if rule == "unit": return 1 + v / 100 if (abs(v) > 3 or v < 0) else v    # 0..3 multipliers; bigger numbers are percent changes
    if rule == "p1": return v / 100 if abs(v) > 1 else v
    if rule == "vib": return v / 100 if abs(v) > 2 else v
    if rule == "gamma": return v
    if rule == "sharp": return v / 100 if v > 2 else v
    if rule == "den": return v / 100 if v > 1 else v
    return v


class _Notes:
    def __init__(self): self.items: list[Issue] = []; self._seen: set = set()
    def add(self, code: str, msg: str, op_id: str | None = None):
        k = (code, msg, op_id)
        if k in self._seen: return
        self._seen.add(k); self.items.append(Issue("warning", code, msg, op_id, None))


def _translate_groups(op: dict, notes: _Notes) -> list[dict]:
    """Nested option groups on studio_look / color_grading -> flat color_grading values (+ a face tone op, + sharpen/denoise).
    Returns extra operations; the operation's own parameters are cleaned in place."""
    params = op.get("parameters")
    if not isinstance(params, dict): return []
    oid = op.get("id", "?")
    cg: dict[str, float] = {}
    ve: dict[str, float] = {}
    face = False
    face_exposure = None
    dropped: list[str] = []
    for key in list(params):
        val = params[key]
        if key in _COLOUR_GROUPS and isinstance(val, dict):
            for sub, sv in val.items():
                name = _ALIAS_PARAM.get(sub, sub)
                if _num(sv) and name in _CG: cg[name] = _scale(_CG[name], float(sv))
                elif _num(sv) and sub in _VE: ve[_VE[sub][0]] = _scale(_VE[sub][1], float(sv))
                else: dropped.append(f"{key}.{sub}")
            del params[key]
        elif key in _FACE_GROUPS and isinstance(val, dict):
            if val.get("enabled", True) is not False: face = True
            fe = val.get("face_exposure")
            if _num(fe) and -2 <= fe <= 2: face_exposure = float(fe)
            for sub in val:
                if sub not in ("enabled", "face_exposure"): dropped.append(f"{key}.{sub}")
            del params[key]
    extra: list[dict] = []
    own = op.get("type")
    if cg:
        if own == "color_grading":
            for k, v in cg.items(): params.setdefault(k, v)                 # what the user set flat wins
        else:
            extra.append({"id": f"{oid}_grade", "type": "color_grading", "start": op.get("start", 0), **({"end": op["end"]} if "end" in op else {}), "parameters": cg})
            notes.add("TRANSLATED", f"'{oid}': the nested colour settings were moved into a real color_grading operation '{oid}_grade' ("
                      + ", ".join(f"{k} {v:+.3g}" if k not in ("contrast", "saturation") else f"{k} {v:.3g}" for k, v in cg.items()) + ")", oid)
    if ve:
        extra.append({"id": f"{oid}_detail", "type": "video_enhancement", "start": op.get("start", 0), **({"end": op["end"]} if "end" in op else {}), "parameters": ve})
        notes.add("TRANSLATED", f"'{oid}': sharpening / noise reduction became a real video_enhancement operation '{oid}_detail' ({', '.join(f'{k} {v:.3g}' for k, v in ve.items())})", oid)
    if face:
        fp: dict[str, float] = {"strength": 0.5, "auto_strength": 0.6, "max_auto_ev": 0.5}
        if face_exposure is not None: fp["exposure"] = face_exposure
        extra.append({"id": f"{oid}_face", "type": "face_tone_correction", "start": op.get("start", 0), **({"end": op["end"]} if "end" in op else {}), "parameters": fp})
        notes.add("TRANSLATED", f"'{oid}': the face / skin settings became a gentle face_tone_correction '{oid}_face' (automatic target, strength 0.5)", oid)
    if dropped:
        notes.add("NO_MATCHING_CONTROL", f"'{oid}': no real control exists for: {', '.join(dropped[:12])}{' …' if len(dropped) > 12 else ''} - left out", oid)
    return extra


def _sanitize_params(op: dict, notes: _Notes) -> None:
    oid, spec = op["id"], OPERATIONS[op["type"]]
    raw = op.get("parameters")
    if raw is None: op["parameters"] = {}; return
    if not isinstance(raw, dict):
        notes.add("DROPPED_PARAMETER", f"'{oid}': 'parameters' was not an object - ignored (defaults used)", oid); op["parameters"] = {}; return
    out: dict[str, Any] = {}
    skipped: list[str] = []
    for k, v in raw.items():
        k2 = _ALIAS_PARAM.get(k, k) if op["type"] == "color_grading" and k not in spec.params else k
        p = spec.params.get(k2)
        if p is None:
            skipped.append(k + (" (a group of settings)" if isinstance(v, (dict, list)) else "")); continue
        if v is None: continue
        if p.type in ("number", "integer") and isinstance(v, str):
            try: v = float(v.strip().rstrip("%"))
            except ValueError: pass
        if p.type == "boolean" and isinstance(v, str) and v.lower() in ("true", "false", "yes", "no", "on", "off"):
            v = v.lower() in ("true", "yes", "on")
        if p.type == "enum" and isinstance(v, str) and v not in p.enum and v.lower() in p.enum: v = v.lower()
        try:
            out[k2] = _coerce(k2, p, v)
        except ValueError as e:
            if p.type in ("number", "integer") and _num(v):
                if p.type == "integer": v = int(round(v))
                nv = min(max(v, p.min if p.min is not None else v), p.max if p.max is not None else v)
                out[k2] = int(nv) if p.type == "integer" else float(nv)
                notes.add("CLAMPED", f"'{oid}': {k2} = {v:g} is outside {p.min} … {p.max}; used {nv:g}", oid)
            else:
                notes.add("DROPPED_PARAMETER", f"'{oid}': {k2}: {e} - ignored (default used)", oid)
    if skipped:
        notes.add("DROPPED_PARAMETER", f"'{oid}' ({op['type']}): unknown settings ignored: {', '.join(skipped[:15])}{' …' if len(skipped) > 15 else ''}", oid)
    op["parameters"] = out


def _sanitize_keyframes(op: dict, notes: _Notes) -> None:
    kf = op.get("keyframes")
    if kf is None: return
    oid = op["id"]
    if not isinstance(kf, dict): notes.add("DROPPED_KEYFRAMES", f"'{oid}': keyframes were not an object - ignored", oid); del op["keyframes"]; return
    good = {}
    for name, items in kf.items():
        _, errs, _ = apply_keyframes(op["type"], {name: items})
        if errs: notes.add("DROPPED_KEYFRAMES", f"'{oid}': keyframes for '{name}' ignored: {errs[0][1]}", oid)
        else: good[name] = items
    if good: op["keyframes"] = good
    else: del op["keyframes"]


def repair(data: dict, video: VideoInfo | None, notes: _Notes) -> dict:
    data = copy.deepcopy(data)
    ver = data.get("blueprint_version")
    if ver not in SUPPORTED_VERSIONS:
        notes.add("VERSION_ASSUMED", f"blueprint_version {ver!r} is not one this program knows ({', '.join(SUPPORTED_VERSIONS)}); read as {SUPPORTED_VERSIONS[-1]}")
        data["blueprint_version"] = SUPPORTED_VERSIONS[-1]
    if "project" in data and not isinstance(data["project"], dict): data["project"] = {}
    if isinstance(data.get("project"), dict):
        for k in ("name", "source_video"):
            if k in data["project"] and not isinstance(data["project"][k], str): data["project"][k] = str(data["project"][k])
    if "output" in data and not isinstance(data["output"], dict): notes.add("DROPPED_PARAMETER", "'output' was not an object - defaults used"); data["output"] = {}
    out = data.get("output") if isinstance(data.get("output"), dict) else None
    if out is not None:
        crf = out.get("crf")
        if isinstance(crf, str) and crf.strip().isdigit(): out["crf"] = crf = int(crf)
        if _num(crf) and not 0 <= crf <= 51: out["crf"] = int(min(max(round(crf), 0), 51)); notes.add("CLAMPED", f"output.crf {crf} is outside 0 … 51; used {out['crf']}")
        elif _num(crf) and crf != int(crf): out["crf"] = int(round(crf))
        codec, fmt = out.get("codec"), out.get("format")
        want = {"prores": "mov", "prores4444": "mov", "vp9_alpha": "webm"}.get(codec)
        if want and fmt != want:
            out["format"] = want; notes.add("AUTO_FIXED", f"output.format set to '{want}' (required by codec '{codec}')")
        elif not want and fmt == "webm":
            out["format"] = "mp4"; notes.add("AUTO_FIXED", "output.format 'webm' needs codec 'vp9_alpha'; used 'mp4'")
    # assets: keep only objects; the strict validator checks ids / types / paths
    if "assets" in data and not isinstance(data["assets"], list): data["assets"] = []
    if isinstance(data.get("assets"), list): data["assets"] = [a for a in data["assets"] if isinstance(a, dict)]
    ops = data.get("operations")
    if isinstance(ops, dict): ops = [ops]
    if not isinstance(ops, list):
        if "operations" in data: notes.add("INVALID_STRUCTURE", "'operations' was not a list - nothing to run")
        ops = []
    result: list[dict] = []
    used: set[str] = set()
    dur = video.duration if video else None
    cut = bool(data.get("clips"))
    for i, op in enumerate(ops):
        if not isinstance(op, dict): notes.add("SKIPPED_OPERATION", f"operations[{i}] is not an object - skipped"); continue
        oid = op.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid):
            new = re.sub(r"[^A-Za-z0-9_.-]", "_", str(oid))[:50] if oid else f"operation_{i + 1}"
            notes.add("AUTO_FIXED", f"operations[{i}]: id {oid!r} is missing or invalid; used '{new}'"); oid = new
        base, n = oid, 2
        while oid in used: oid = f"{base}_{n}"; n += 1
        if oid != op.get("id") and isinstance(op.get("id"), str) and ID_RE.match(op["id"]): notes.add("AUTO_FIXED", f"duplicate id '{base}' renamed to '{oid}'")
        used.add(oid); op["id"] = oid
        t = op.get("type")
        if isinstance(t, str) and t not in OPERATIONS:
            key = t.strip().lower().replace(" ", "_").replace("-", "_")
            if key in OPERATIONS: op["type"] = t = key
            elif key in OP_ALIASES:
                notes.add("AUTO_FIXED", f"'{oid}': operation type '{t}' is not a real name; read as '{OP_ALIASES[key]}'", oid); op["type"] = t = OP_ALIASES[key]
        spec = OPERATIONS.get(t) if isinstance(t, str) else None
        if spec is None:
            notes.add("SKIPPED_OPERATION", f"'{oid}': operation type {t!r} does not exist in this program - skipped. Supported: {', '.join(k for k, v in OPERATIONS.items() if v.supported)}", oid); continue
        if not spec.supported:
            notes.add("SKIPPED_OPERATION", f"'{oid}': '{t}' is recognised but not available ({spec.unsupported_reason}) - skipped", oid); continue
        # fix times that run past the end of the video (only when there are no cuts - then the timeline length is not the video length)
        if dur is not None and not cut and "end" in op and op["end"] is not None:
            try:
                e = parse_time(op["end"])
                if e > dur + 1e-3:
                    op["end"] = dur; notes.add("CLAMPED", f"'{oid}': end {e:g}s is after the end of the video; used {dur:.3f}s", oid)
            except ValueError: pass
        extra = _translate_groups(op, notes) if t in ("studio_look", "color_grading") else []
        _sanitize_params(op, notes)
        _sanitize_keyframes(op, notes)
        if "enabled" in op and not isinstance(op["enabled"], bool): del op["enabled"]
        for e_ in extra:
            e_["id"] = (lambda b: next(c for c in (b, *(f"{b}_{k}" for k in range(2, 99))) if c not in used))(e_["id"])
            used.add(e_["id"])
            _sanitize_params(e_, notes)
            result.append(e_)
        if t == "studio_look" and extra:                 # the grade runs first, then the look (and keep the look bound to its replacement)
            pass
        result.append(op)
    data["operations"] = result
    return data


_DEP_RE = re.compile(r"depends_on '([^']+)'")


def _quarantine(data: dict, errs, notes: _Notes) -> bool:
    """Set aside what still fails strict validation. Returns False when nothing could be removed (the error is fatal)."""
    changed = False
    ops = data.get("operations") or []
    drop_ops: dict[str, str] = {}
    drop_assets: set[int] = set()
    drop_clips: set[int] = set()
    for e in errs:
        path = e.path or ""
        if e.code == "UNKNOWN_DEPENDENCY" and e.op_id:
            m = _DEP_RE.search(e.message)
            o = next((o for o in ops if o.get("id") == e.op_id), None)
            if m and o and isinstance(o.get("depends_on"), list) and m.group(1) in o["depends_on"]:
                o["depends_on"].remove(m.group(1)); changed = True
                notes.add("AUTO_FIXED", f"'{e.op_id}': dependency '{m.group(1)}' is not available, so the link was removed", e.op_id)
                continue
        if e.code.startswith("TRANSPARENT_"):
            data["output"] = {**(data.get("output") or {}), "codec": "h264", "format": "mp4"}; changed = True
            notes.add("AUTO_FIXED", f"transparent export not possible ({e.message}); exporting an ordinary mp4 (h264) instead")
        elif e.op_id and any(o.get("id") == e.op_id for o in ops):
            drop_ops.setdefault(e.op_id, f"{e.code}: {e.message}")
        elif path.startswith("assets["):
            drop_assets.add(int(re.match(r"assets\[(\d+)\]", path).group(1)))
        elif path.startswith("clips["):
            drop_clips.add(int(re.match(r"clips\[(\d+)\]", path).group(1)))
        elif e.code in ("EMPTY_TIMELINE", "TOO_MANY_CLIPS", "TOO_MANY_REORDERED_CLIPS") or path == "clips":
            data.pop("clips", None); changed = True
            notes.add("DROPPED_PARAMETER", f"cuts (clips) ignored: {e.message}")
        elif path.startswith("output"):
            k = path.split(".", 1)[1] if "." in path else None
            if k and isinstance(data.get("output"), dict) and k in data["output"]:
                del data["output"][k]
            else:
                data["output"] = {}
            changed = True
            notes.add("DROPPED_PARAMETER", f"{path}: {e.message} - default used")
        elif path == "project":
            data["project"] = {}; changed = True
    for oid, why in drop_ops.items():
        data["operations"] = [o for o in data["operations"] if o.get("id") != oid]
        for o in data["operations"]:
            if isinstance(o.get("depends_on"), list) and oid in o["depends_on"]: o["depends_on"].remove(oid)
        notes.add("SKIPPED_OPERATION", f"'{oid}' was skipped - {why}", oid); changed = True
    if drop_assets:
        keep = []
        for i, a in enumerate(data.get("assets") or []):
            if i in drop_assets: notes.add("DROPPED_ASSET", f"asset '{a.get('id')}' ignored - it cannot be used ({next((e.message for e in errs if (e.path or '').startswith(f'assets[{i}]')), '')})")
            else: keep.append(a)
        data["assets"] = keep; changed = True
    if drop_clips and isinstance(data.get("clips"), list):
        data["clips"] = [c for i, c in enumerate(data["clips"]) if i not in drop_clips]
        if not data["clips"]: data.pop("clips")
        notes.add("DROPPED_PARAMETER", "some cuts (clips) were invalid and ignored"); changed = True
    return changed


def parse_lenient(text_or_obj: Any, video: VideoInfo | None, policy=None, base_dir=None, plan_hook=None) -> tuple[Report, dict | None]:
    """Returns (report, repaired blueprint dict). Falls back to the strict result when the file cannot be read at all."""
    if isinstance(text_or_obj, (str, bytes)):
        if len(text_or_obj) > MAX_BYTES: return parse_blueprint(text_or_obj, video, policy, base_dir), None
        try: data = json.loads(text_or_obj)
        except json.JSONDecodeError: return parse_blueprint(text_or_obj, video, policy, base_dir), None   # reports INVALID_JSON
    else:
        data = text_or_obj
    if not isinstance(data, dict) or data.get("strict") is True:
        return parse_blueprint(data, video, policy, base_dir), None
    notes = _Notes()
    data = repair(data, video, notes)
    data.pop("strict", None)
    def check() -> Report:
        rp = parse_blueprint(data, video, policy, base_dir)
        if plan_hook and rp.ok: plan_hook(rp)        # planning rules (transparent export, overlaps ...) count as validation too
        return rp
    rep = check()
    for _ in range(80):
        if not rep.errors: break
        if not _quarantine(data, rep.errors, notes): break
        rep = check()
    skipped = sum(1 for n in notes.items if n.code == "SKIPPED_OPERATION")
    rep.issues = notes.items + rep.issues
    if rep.ok and (notes.items):
        rep.issues.insert(0, Issue("warning", "LENIENT_IMPORT",
                          f"This blueprint was not 100% valid. It was repaired automatically ({len(notes.items)} change(s)"
                          + (f", {skipped} operation(s) skipped" if skipped else "") + "). Read the notes below; the original text was not modified.", None, None))
    return rep, data
