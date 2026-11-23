// Pure document model: blueprint editing operations, undo/redo history, timeline math.
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export interface Op {
  id: string; type: string; start: number; end?: number;
  parameters?: Record<string, any>; depends_on?: string[]; enabled?: boolean; label?: string;
}
export interface Asset { id: string; type: string; path: string }
export interface Blueprint {
  blueprint_version: string; project?: { name?: string; source_video?: string };
  output?: Record<string, any>; assets: Asset[]; operations: Op[]; [k: string]: any;
}
export interface ParamSpec { type: string; default?: any; minimum?: number; maximum?: number; enum?: string[]; description?: string; unit?: string; asset_types?: string[]; required?: boolean }
export interface OpSpec { type: string; title: string; category: string; track: string; description: string; supported: boolean; unsupported_reason: string; provides_alpha: boolean; parameters: Record<string, ParamSpec> }
export type Schema = { operations: Record<string, OpSpec> };

export const emptyBlueprint = (name = "Untitled"): Blueprint => ({
  blueprint_version: "1.0", project: { name }, output: { resolution: "source", fps: "source" }, assets: [], operations: [],
});

export function parseBlueprintText(text: string): { bp?: Blueprint; error?: string } {
  try {
    const v = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Blueprint root must be a JSON object" };
    if (!Array.isArray(v.operations)) v.operations = v.operations === undefined ? [] : v.operations;
    if (!Array.isArray(v.assets)) v.assets = v.assets === undefined ? [] : v.assets;
    return { bp: v };
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
