"""Blueprint Execution Engine - planning: ordering, dependency resolution, overlap handling."""
from __future__ import annotations
import heapq
from dataclasses import dataclass, field

from .blueprint import Blueprint, Operation, Report, VideoInfo
from .registry import OPERATIONS


@dataclass
class Plan:
    blueprint: Blueprint
    video: VideoInfo
    steps: list[Operation] = field(default_factory=list)          # execution order (enabled ops only)
    alpha_source: dict[str, str] = field(default_factory=dict)    # replacement id -> keyer id
    consumed_keyers: set[str] = field(default_factory=set)
    look_auto: dict = field(default_factory=dict)                 # studio_look id -> measured settings (filled by the engine)
    measuring: bool = False                                       # engine is measuring the composite: studio looks are skipped
    look_curves: dict = field(default_factory=dict)               # studio_look id -> [r, g, b] curve points matched to its reference picture (filled by the engine)
    look_for: dict[str, Operation] = field(default_factory=dict)  # replacement id -> the studio_look that integrates the subject into it
    total_frames: int = 0
    edl: list | None = None                                       # [(src_a, src_b)] pieces on the output grid; None = uncut source
    mattes: dict = field(default_factory=dict)                    # op id -> ml.matting.MatteRef (filled by the engine)
    face_controls: dict = field(default_factory=dict)             # op id -> ml.face.ControlRef (filled by the engine)
    upscales: dict = field(default_factory=dict)                  # op id -> ml.upscale.UpscaleRef (filled by the engine)

    def upscale_ops(self) -> list[Operation]:
        return [o for o in self.steps if o.type == "ai_upscale"]

    def face_ops(self) -> list[Operation]:
        return [o for o in self.steps if o.type in ("face_tone_correction", "face_relighting")]

    def ai_keyers(self) -> list[Operation]:
        return [o for o in self.steps if o.type == "background_removal" and o.params.get("mode") == "ai"]

    @property
    def timeline_duration(self) -> float:
        return self.total_frames / self.blueprint.output.fps

    def describe(self) -> list[dict]:
        return [{"order": i + 1, "id": o.id, "type": o.type, "start": o.start, "end": o.end,
                 "stage": OPERATIONS[o.type].stage,
                 "alpha_source": self.alpha_source.get(o.id), "auto": self.look_auto.get(o.id)} for i, o in enumerate(self.steps)]


def _overlap(a: Operation, b: Operation) -> bool:
    return a.start_frame < b.end_frame and b.start_frame < a.end_frame


def build_plan(bp: Blueprint, video: VideoInfo, rep: Report) -> Plan | None:
    """Resolve dependencies and ordering. Appends to `rep`; returns None if errors prevent execution."""
    plan = Plan(bp, video, total_frames=round(video.duration * bp.output.fps))
    if bp.clips:
        from . import edl
        plan.edl = [(c.start_frame, c.end_frame) for c in bp.clips]
        plan.total_frames = edl.total_frames(plan.edl)
    ops = [o for o in bp.operations if o.enabled]
    byid = {o.id: o for o in ops}
    index = {o.id: i for i, o in enumerate(bp.operations)}

    # --- bind background replacements to an alpha source
    for r in ops:
        if r.type != "background_replacement":
            continue
        cands: list[Operation] = []
        declared = [byid[d] for d in r.depends_on if d in byid and OPERATIONS[byid[d].type].provides_alpha]
        pool = declared or [k for k in ops if OPERATIONS[k.type].provides_alpha]
        for k in pool:
            if k.start_frame <= r.start_frame and k.end_frame >= r.end_frame:
                cands.append(k)
        if not cands:
            rep.err("MISSING_ALPHA_SOURCE",
                    f"'{r.id}' needs a covering chroma_key/background_removal "
                    f"({r.start:g}-{r.end:g}s) but none is enabled" +
                    (" among its dependencies" if declared else ""), r.id)
            continue
        k = sorted(cands, key=lambda c: (c.end_frame - c.start_frame, index[c.id]))[0]
        plan.alpha_source[r.id] = k.id
        plan.consumed_keyers.add(k.id)
        # implicit dependency so the ordering honours it
        if k.id not in r.depends_on:
            r.depends_on = r.depends_on + [k.id]

    # --- studio looks: bind to the background replacement they integrate the subject into (if any)
    for lk in (o for o in ops if o.type == "studio_look"):
        reps = [byid[d] for d in lk.depends_on if d in byid and byid[d].type == "background_replacement"] or \
               [r for r in ops if r.type == "background_replacement"]
        reps = [r for r in reps if _overlap(r, lk)]
        if not reps:
            rep.warn("STUDIO_NO_BACKGROUND", f"'{lk.id}' has no background_replacement to integrate the subject into; only its global grade (contrast, tint, glow, vignette, clarity, grain) is applied", lk.id)
            continue
        r = sorted(reps, key=lambda c: (not (c.start_frame <= lk.start_frame and c.end_frame >= lk.end_frame), index[c.id]))[0]
        if r.id in plan.look_for:
            rep.err("OVERLAP_CONFLICT", f"Studio looks '{plan.look_for[r.id].id}' and '{lk.id}' both integrate into background '{r.id}'; use one", lk.id)
            continue
        plan.look_for[r.id] = lk
        if (r.start_frame, r.end_frame) != (lk.start_frame, lk.end_frame):
            rep.warn("STUDIO_RANGE_DIFFERS", f"'{lk.id}': subject integration (ambient_match, light_wrap, depth) follows background '{r.id}' ({r.start:g}-{r.end:g}s); "
                     f"the global grade follows the look's own range ({lk.start:g}-{lk.end:g}s)", lk.id)
        if r.id not in lk.depends_on:
            lk.depends_on = lk.depends_on + [r.id]

    # --- overlap conflicts
    repl = [o for o in ops if o.type == "background_replacement"]
    for i, a in enumerate(repl):
        for b in repl[i + 1:]:
            if _overlap(a, b):
                rep.err("OVERLAP_CONFLICT", f"Background replacements '{a.id}' and '{b.id}' overlap in time; only one background can be active", a.id)
    ups = [o for o in ops if o.type == "ai_upscale"]
    for i, a in enumerate(ups):
        for b in ups[i + 1:]:
            if _overlap(a, b):
                rep.err("OVERLAP_CONFLICT", f"AI upscale operations '{a.id}' and '{b.id}' overlap; use one per time range", a.id)
    keyers = [o for o in ops if OPERATIONS[o.type].provides_alpha]
    for i, a in enumerate(keyers):
        for b in keyers[i + 1:]:
            if _overlap(a, b):
                rep.warn("OVERLAP_STACKED", f"Keying operations '{a.id}' and '{b.id}' overlap; they are applied one after another (second keys the result of the first)", a.id)
    for t in ("color_grading", "video_enhancement", "studio_look"):
        same = [o for o in ops if o.type == t]
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                if _overlap(a, b):
                    rep.warn("OVERLAP_STACKED", f"{t} operations '{a.id}' and '{b.id}' overlap; their effects are cumulative", a.id)
    for o in ops:
        if o.type == "background_replacement" and o.params.get("asset_id") and o.params["fit"] == "cover":
            ext = lambda n: [k.value for k in o.keyframes[n]] if n in o.keyframes else [o.params[n]]
            z = min(ext("scale")); off = max(abs(v) for n in ("offset_x", "offset_y") for v in ext(n))
            if z < 1 + 2 * off - 1e-9:
                rep.warn("BG_EDGE_VISIBLE", f"Background '{o.id}': with scale {z:g} and an offset up to {off:g} of the frame, the background image may not cover the frame "
                         f"and the background colour can show at the edges. Use scale >= {1 + 2 * off:g}" + (" (at every keyframe)" if o.keyframes else "") + ".", o.id)
        if o.type == "background_replacement":
            sc, sx, sy = o.params["subject_scale"], o.params["subject_x"], o.params["subject_y"]
            side = sc < 1 or (sc - 1) / 2 < abs(sx) - 1e-9
            bottom = (o.params["subject_anchor"] == "bottom" and (sc < 1 or sy < -(sc - 1) - 1e-9)) or \
                     (o.params["subject_anchor"] == "center" and (sc < 1 or abs(sy) > (sc - 1) / 2 + 1e-9))
            if side or bottom:
                rep.warn("SUBJECT_EDGE_VISIBLE", f"Background '{o.id}': with subject_scale {sc:g}, subject_x {sx:g}, subject_y {sy:g} the footage's own frame edge becomes visible "
                         f"({'left/right' if side else 'bottom'}) where the person touches it (shoulders, torso). Use subject_scale >= {1 + 2 * abs(sx):g} or reframe.", o.id)
        if o.type == "chroma_key" or o.type == "background_removal":
            kc = o.params["key_color"]
            if o.params.get("mode", "chroma") == "chroma" and o.params["spill_suppression"] and _dominant(kc) is None:
                rep.warn("SPILL_UNSUPPORTED_COLOR", f"Spill suppression only supports green/blue-dominant key colours ({kc}); it is skipped", o.id)

    if rep.errors:
        return None

    # --- ordering: Kahn with priority (stage, start, blueprint order); dependencies always win
    indeg = {o.id: 0 for o in ops}
    out_edges: dict[str, list[str]] = {o.id: [] for o in ops}
    for o in ops:
        for d in o.depends_on:
            if d in byid:
                indeg[o.id] += 1
                out_edges[d].append(o.id)
    heap = [(OPERATIONS[o.type].stage, o.start_frame, index[o.id], o.id) for o in ops if indeg[o.id] == 0]
    heapq.heapify(heap)
    while heap:
        _, _, _, oid = heapq.heappop(heap)
        o = byid[oid]
        plan.steps.append(o)
        for n in out_edges[oid]:
            indeg[n] -= 1
            if indeg[n] == 0:
                m = byid[n]
                heapq.heappush(heap, (OPERATIONS[m.type].stage, m.start_frame, index[m.id], m.id))
    if len(plan.steps) != len(ops):
        rep.err("DEPENDENCY_CYCLE", "Operations form a dependency cycle")
        return None
    return plan


def _dominant(hexcol: str) -> str | None:
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    if g > r * 1.2 and g > b * 1.2: return "green"
    if b > r * 1.2 and b > g * 1.2: return "blue"
    return None
