// Pure document model: blueprint editing operations, undo/redo history, timeline math.
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export interface Op {
  id: string; type: string; start: number; end?: number;
  parameters?: Record<string, any>; depends_on?: string[]; enabled?: boolean; label?: string;
}
export interface Asset { id: string; type: string; path: string }
export interface Clip { id: string; start: number; end: number; label?: string }
export interface Blueprint {
  blueprint_version: string; project?: { name?: string; source_video?: string };
  output?: Record<string, any>; assets: Asset[]; operations: Op[]; clips?: Clip[]; [k: string]: any;
}
export interface ParamSpec { type: string; default?: any; minimum?: number; maximum?: number; enum?: string[]; description?: string; unit?: string; asset_types?: string[]; required?: boolean }
export interface OpSpec { type: string; title: string; category: string; track: string; description: string; supported: boolean; unsupported_reason: string; provides_alpha: boolean; parameters: Record<string, ParamSpec> }
export type Schema = { operations: Record<string, OpSpec> };

export const emptyBlueprint = (name = "Untitled"): Blueprint => ({
  blueprint_version: "1.0", project: { name }, output: { resolution: "source", fps: "source" }, assets: [], operations: [],
});

/** seconds as a number, "12.5", "1:02.250" or "00:01:02.250" (same rules as the backend) */
export function parseTime(v: unknown): number | undefined {
  if (typeof v === "number") return Number.isFinite(v) && v >= 0 ? v : undefined;
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  if (/^(\d+:)?\d{1,2}:\d{1,2}(\.\d+)?$/.test(s)) return s.split(":").reduce((a, p) => a * 60 + parseFloat(p), 0);
  return undefined;
}
/** AI-written blueprints may use timecode strings; the editor works in numbers (unparseable values are left for the validator) */
export function normalizeTimes(bp: Blueprint): Blueprint {
  const fix = (x: any) => { const o = { ...x }; for (const k of ["start", "end"]) if (typeof o[k] === "string") { const n = parseTime(o[k]); if (n !== undefined) o[k] = n; } return o; };
  return { ...bp, operations: bp.operations.map(fix), ...(Array.isArray(bp.clips) ? { clips: bp.clips.map(fix) } : {}) };
}

export function parseBlueprintText(text: string): { bp?: Blueprint; error?: string } {
  try {
    const v = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Blueprint root must be a JSON object" };
    if (!Array.isArray(v.operations)) v.operations = v.operations === undefined ? [] : v.operations;
    if (!Array.isArray(v.assets)) v.assets = v.assets === undefined ? [] : v.assets;
    return { bp: Array.isArray(v.operations) ? normalizeTimes(v) : v };
  } catch (e: any) { return { error: e.message }; }
}

// ---------- operation edits (all return a NEW blueprint; inputs are never mutated)
export const withOps = (bp: Blueprint, ops: Op[]): Blueprint => ({ ...bp, operations: ops });
const mapOp = (bp: Blueprint, id: string, f: (o: Op) => Op) => withOps(bp, bp.operations.map(o => (o.id === id ? f(o) : o)));

export function uniqueId(bp: Blueprint, base: string): string {
  const used = new Set(bp.operations.map(o => o.id));
  const stem = base.replace(/_\d+$/, "");
  for (let i = 1; ; i++) { const id = `${stem}_${String(i).padStart(3, "0")}`; if (!used.has(id)) return id; }
}
export function defaults(spec: OpSpec): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, p] of Object.entries(spec.parameters)) if (p.default !== undefined && p.default !== null) out[k] = p.default;
  return out;
}
export function addOp(bp: Blueprint, spec: OpSpec, start: number, end: number): { bp: Blueprint; id: string } {
  const id = uniqueId(bp, spec.type);
  const params: Record<string, any> = {};
  if (spec.type === "text_overlay") params.text = "Text";
  return { bp: withOps(bp, [...bp.operations, { id, type: spec.type, start, end, parameters: params }]), id };
}
export const deleteOp = (bp: Blueprint, id: string): Blueprint =>
  withOps(bp, bp.operations.filter(o => o.id !== id).map(o => o.depends_on?.includes(id) ? { ...o, depends_on: o.depends_on.filter(d => d !== id) } : o));
export function duplicateOp(bp: Blueprint, id: string): { bp: Blueprint; id?: string } {
  const src = bp.operations.find(o => o.id === id); if (!src) return { bp };
  const nid = uniqueId(bp, src.type);
  const i = bp.operations.indexOf(src);
  const copy: Op = JSON.parse(JSON.stringify({ ...src, id: nid, depends_on: src.depends_on }));
  const ops = [...bp.operations]; ops.splice(i + 1, 0, copy);
  return { bp: withOps(bp, ops), id: nid };
}
export const toggleOp = (bp: Blueprint, id: string) => mapOp(bp, id, o => ({ ...o, enabled: o.enabled === false }));
export const setOpTime = (bp: Blueprint, id: string, start: number, end: number) => mapOp(bp, id, o => ({ ...o, start, end }));
export const setParam = (bp: Blueprint, id: string, key: string, value: any) =>
  mapOp(bp, id, o => { const p = { ...(o.parameters ?? {}) }; if (value === undefined) delete p[key]; else p[key] = value; return { ...o, parameters: p }; });
export const resetParams = (bp: Blueprint, id: string) => mapOp(bp, id, o => ({ ...o, parameters: o.type === "text_overlay" ? { text: o.parameters?.text ?? "Text" } : {} }));
export function splitOp(bp: Blueprint, id: string, t: number, fps: number): { bp: Blueprint; ids?: [string, string] } {
  const o = bp.operations.find(x => x.id === id);
  const end = o?.end;
  if (!o || end === undefined) return { bp };
  const s = snap(t, fps);
  if (s <= snap(o.start, fps) || s >= snap(end, fps)) return { bp };         // must fall strictly inside
  const nid = uniqueId(bp, o.type);
  const second: Op = { ...JSON.parse(JSON.stringify(o)), id: nid, start: s, depends_on: [...(o.depends_on ?? [])] };
  const ops = bp.operations.map(x => (x.id === id ? { ...x, end: s } : x));
  ops.splice(ops.findIndex(x => x.id === id) + 1, 0, second);
  return { bp: withOps(bp, ops), ids: [id, nid] };
}
export const setOutput = (bp: Blueprint, patch: Record<string, any>) => ({ ...bp, output: { ...(bp.output ?? {}), ...patch } });
export const addAsset = (bp: Blueprint, a: Asset): Blueprint => ({ ...bp, assets: [...bp.assets.filter(x => x.id !== a.id), a] });

// ---------- timeline math
export const snap = (t: number, fps: number) => Math.round(t * fps) / fps;
export const timeToX = (t: number, pxPerSec: number) => t * pxPerSec;
export const xToTime = (x: number, pxPerSec: number, fps: number, duration: number) => Math.min(duration, Math.max(0, snap(x / pxPerSec, fps)));
export function formatTime(t: number, fps: number): string {
  const f = Math.round(t * fps), total = Math.floor(f / fps), fr = f - total * fps;
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)}:${p(Math.round(fr))}`;
}
export function dragOp(o: Op, mode: "move" | "start" | "end", deltaSec: number, fps: number, duration: number): { start: number; end: number } {
  const end0 = o.end ?? duration, minLen = 1 / fps;
  if (mode === "move") {
    const len = end0 - o.start;
    const s = Math.min(Math.max(0, snap(o.start + deltaSec, fps)), snap(duration - len, fps));
    return { start: s, end: snap(s + len, fps) };
  }
  if (mode === "start") return { start: Math.min(Math.max(0, snap(o.start + deltaSec, fps)), snap(end0 - minLen, fps)), end: end0 };
  return { start: o.start, end: Math.max(snap(o.start + minLen, fps), Math.min(duration, snap(end0 + deltaSec, fps))) };
}

// ---------- undo / redo history
export interface History<T> { past: T[]; present: T; future: T[]; lastKey?: string; lastAt?: number }
export const newHistory = <T,>(present: T): History<T> => ({ past: [], present, future: [] });
/** commit a change; edits with the same coalesce key within `windowMs` merge into one undo step (slider drags). */
export function commit<T>(h: History<T>, next: T, key?: string, now = Date.now(), windowMs = 800, limit = 200): History<T> {
  if (next === h.present) return h;
  if (key && h.lastKey === key && h.lastAt !== undefined && now - h.lastAt < windowMs)
    return { ...h, present: next, future: [], lastAt: now };
  return { past: [...h.past, h.present].slice(-limit), present: next, future: [], lastKey: key, lastAt: now };
}
export const undo = <T,>(h: History<T>): History<T> => h.past.length
  ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h;
export const redo = <T,>(h: History<T>): History<T> => h.future.length
  ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h;
/** replace present without recording history (e.g. after loading a project) */
export const reset = <T,>(present: T): History<T> => newHistory(present);


// ======================================================================= cutting (clips = edit decision list)
export interface LaidClip extends Clip { sf: number; ef: number; srcStart: number; srcEnd: number; tStart: number; tEnd: number; implicit?: boolean }
export interface Layout { clips: LaidClip[]; duration: number; frames: number; cut: boolean; fps: number }

/** output-grid fps used for all frame snapping (same rule as the backend: numeric output.fps, else the source's) */
export const gridFps = (bp: Blueprint, srcFps: number) => (typeof bp.output?.fps === "number" ? bp.output.fps : srcFps);

/** timeline layout: the clips back to back. No clips = one implicit clip covering the whole source. */
export function layoutClips(bp: Blueprint, srcDuration: number, fps: number): Layout {
  const srcFrames = Math.round(srcDuration * fps);
  const raw: Clip[] = bp.clips && bp.clips.length ? bp.clips : [{ id: "clip_001", start: 0, end: srcDuration }];
  let off = 0;
  const clips: LaidClip[] = raw.map(c => {
    const sf = Math.max(0, Math.round(c.start * fps)), ef = Math.min(srcFrames, Math.max(sf + 1, Math.round((c.end ?? srcDuration) * fps)));
    const n = ef - sf, lc: LaidClip = { ...c, sf, ef, srcStart: sf / fps, srcEnd: ef / fps, tStart: off / fps, tEnd: (off + n) / fps, implicit: !(bp.clips && bp.clips.length) };
    off += n; return lc;
  });
  const cut = !(clips.length === 1 && clips[0].sf === 0 && clips[0].ef >= srcFrames);
  return { clips, duration: off / fps, frames: off, cut, fps };
}
/** which clip is at timeline time t, and the source time it shows */
export function timelineToSource(l: Layout, t: number): { index: number; src: number } {
  const f = Math.min(l.frames - 1, Math.max(0, Math.round(t * l.fps)));
  let off = 0;
  for (let i = 0; i < l.clips.length; i++) {
    const n = l.clips[i].ef - l.clips[i].sf;
    if (f < off + n) return { index: i, src: (l.clips[i].sf + (f - off)) / l.fps };
    off += n;
  }
  const last = l.clips[l.clips.length - 1]; return { index: l.clips.length - 1, src: (last.ef - 1) / l.fps };
}

const toClips = (l: Layout): Clip[] => l.clips.map(c => ({ id: c.id, start: c.sf / l.fps, end: c.ef / l.fps, ...(c.label ? { label: c.label } : {}) }));
const withClips = (bp: Blueprint, clips: Clip[]): Blueprint => ({ ...bp, clips });
export function uniqueClipId(bp: Blueprint, l: Layout): string {
  const used = new Set([...(bp.clips ?? []).map(c => c.id), ...l.clips.map(c => c.id)]);
  for (let i = 1; ; i++) { const id = `clip_${String(i).padStart(3, "0")}`; if (!used.has(id)) return id; }
}

const r6 = (x: number) => Math.round(x * 1e6) / 1e6;       // clean float noise (the backend snaps to frames anyway)
/** shift/shrink effect times when `len` seconds are removed from [a, a+len). Effects wholly inside disappear. */
export function rippleRemove(ops: Op[], a: number, len: number, fps: number): { ops: Op[]; removed: string[] } {
  const b = a + len, eps = 0.5 / fps, removed: string[] = [], out: Op[] = [];
  for (const o of ops) {
    const end = o.end;
    if (end !== undefined && end <= a + eps) { out.push(o); continue; }
    if (o.start >= b - eps) { out.push({ ...o, start: r6(o.start - len), ...(end !== undefined ? { end: r6(end - len) } : {}) }); continue; }
    const ns = o.start < a ? o.start : a;
    const ne = end === undefined ? undefined : end > b ? end - len : a;
    if (ne !== undefined && ne - ns < 1 / fps - 1e-9) { removed.push(o.id); continue; }
    out.push({ ...o, start: r6(ns), ...(ne !== undefined ? { end: r6(ne) } : {}) });
  }
  return { ops: out, removed };
}
/** make room for `len` seconds at t. Effects after t move; effects spanning t grow; with `attach`, effects ending exactly at t grow too. */
export function rippleInsert(ops: Op[], t: number, len: number, fps: number, attach: boolean): Op[] {
  const eps = 0.5 / fps;
  return ops.map(o => {
    const end = o.end;
    if (o.start >= t - eps) return { ...o, start: r6(o.start + len), ...(end !== undefined ? { end: r6(end + len) } : {}) };
    if (end !== undefined && (end > t + eps || (attach && end >= t - eps))) return { ...o, end: r6(end + len) };
    return o;
  });
}

/** split the clip under timeline time t into two (no change to the picture; effects keep their times) */
export function cutAt(bp: Blueprint, t: number, srcDuration: number, fps: number): { bp: Blueprint; ids?: [string, string] } {
  const l = layoutClips(bp, srcDuration, fps);
  const f = Math.round(t * fps);
  let off = 0;
  for (const c of l.clips) {
    const n = c.ef - c.sf;
    if (f > off && f < off + n) {
      const cs = toClips(l), i = l.clips.indexOf(c), cut = c.sf + (f - off), nid = uniqueClipId(bp, l);
      cs.splice(i, 1, { ...cs[i], end: cut / fps }, { id: nid, start: cut / fps, end: c.ef / fps });
      return { bp: withClips(bp, cs), ids: [cs[i].id, nid] };
    }
    off += n;
  }
  return { bp };                                  // at a boundary/edge: nothing to cut
}
/** remove a clip (ripple: later footage and effects move earlier). The last clip cannot be removed. */
export function deleteClip(bp: Blueprint, id: string, srcDuration: number, fps: number): { bp: Blueprint; removedOps: string[] } {
  const l = layoutClips(bp, srcDuration, fps), c = l.clips.find(x => x.id === id);
  if (!c || l.clips.length < 2) return { bp, removedOps: [] };
  const r = rippleRemove(bp.operations, c.tStart, c.tEnd - c.tStart, fps);
  return { bp: { ...withClips(bp, toClips(l).filter(x => x.id !== id)), operations: r.ops }, removedOps: r.removed };
}
/** change a clip's source in- or out-point (frame-snapped, clamped to the source and to >= 1 frame). Effects ripple. */
export function trimClip(bp: Blueprint, id: string, side: "start" | "end", srcTime: number, srcDuration: number, fps: number): Blueprint {
  const l = layoutClips(bp, srcDuration, fps), c = l.clips.find(x => x.id === id);
  if (!c) return bp;
  const srcFrames = Math.round(srcDuration * fps);
  let sf = c.sf, ef = c.ef;
  if (side === "start") sf = Math.min(ef - 1, Math.max(0, Math.round(srcTime * fps)));
  else ef = Math.max(sf + 1, Math.min(srcFrames, Math.round(srcTime * fps)));
  const delta = (ef - sf) - (c.ef - c.sf);
  if (delta === 0) return bp;
  let ops = bp.operations;
  if (delta < 0) ops = rippleRemove(ops, side === "end" ? c.tEnd + delta / fps : c.tStart, -delta / fps, fps).ops;
  else ops = rippleInsert(ops, side === "end" ? c.tEnd : c.tStart, delta / fps, fps, true);
  const cs = toClips(l).map(x => x.id === id ? { ...x, start: sf / fps, end: ef / fps } : x);
  return { ...withClips(bp, cs), operations: ops };
}
/** duplicate a clip right after itself (a repeated take); later effects move later */
export function duplicateClip(bp: Blueprint, id: string, srcDuration: number, fps: number): { bp: Blueprint; id?: string } {
  const l = layoutClips(bp, srcDuration, fps), i = l.clips.findIndex(x => x.id === id);
  if (i < 0) return { bp };
  const nid = uniqueClipId(bp, l), cs = toClips(l), c = l.clips[i];
  cs.splice(i + 1, 0, { id: nid, start: c.sf / fps, end: c.ef / fps });
  return { bp: { ...withClips(bp, cs), operations: rippleInsert(bp.operations, c.tEnd, c.tEnd - c.tStart, fps, false) }, id: nid };
}
/** swap a clip with its neighbour. Effects keep their *timeline* times (they do not follow the footage). */
export function moveClip(bp: Blueprint, id: string, dir: -1 | 1, srcDuration: number, fps: number): Blueprint {
  const l = layoutClips(bp, srcDuration, fps), i = l.clips.findIndex(x => x.id === id), j = i + dir;
  if (i < 0 || j < 0 || j >= l.clips.length) return bp;
  const cs = toClips(l); [cs[i], cs[j]] = [cs[j], cs[i]];
  return withClips(bp, cs);
}
/** a project whose only clip is the whole source is stored without 'clips' */
export const simplifyClips = (bp: Blueprint, srcDuration: number, fps: number): Blueprint => {
  if (!bp.clips) return bp;
  const l = layoutClips(bp, srcDuration, fps);
  if (l.cut) return bp;
  const { clips: _c, ...rest } = bp; return rest as Blueprint;
};

export function labelClip(bp: Blueprint, id: string, label: string, srcDuration: number, fps: number): Blueprint {
  const l = layoutClips(bp, srcDuration, fps);
  if (!l.clips.some(c => c.id === id)) return bp;
  return withClips(bp, toClips(l).map(c => c.id === id ? (label ? { ...c, label } : { id: c.id, start: c.start, end: c.end }) : c));
}
