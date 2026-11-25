import type { Blueprint, Op, Schema } from "./model";

export interface MergeOptions {
  /** also take the output settings (codec, quality, resolution ...) of the incoming blueprint; default: keep the project's own */
  applyOutput: boolean;
  /** run the blueprint only on this part of the timeline (seconds): its start/end are then relative to the clip's start, clipped to the clip */
  clip?: { tStart: number; tEnd: number } | null;
}
/** how the Blueprint tab applies what was pasted */
export interface ExecOptions { how: "add" | "replace"; applyOutput: boolean; clip: MergeOptions["clip"] }
export interface MergeResult { bp: Blueprint; added: string[]; notes: string[] }

const rangeOf = (o: Op, duration: number): [number, number] => [typeof o.start === "number" ? o.start : 0, typeof o.end === "number" ? o.end : duration];
const overlaps = (a: [number, number], b: [number, number]) => a[0] < b[1] - 1e-6 && b[0] < a[1] - 1e-6;

/**
 * Add a pasted blueprint to the project WITHOUT touching what is already there: existing effects, background removal, background layers,
 * cuts, assets and output settings all stay. The incoming effects are appended (ids / assets renamed on a clash, links kept), optionally
 * limited to the selected clip. A second background layer over an existing one would be refused by the engine, so that part is left out and reported.
 */
export function mergeBlueprint(existing: Blueprint, incoming: Blueprint, schema: Schema | null, opts: MergeOptions, duration: number): MergeResult {
  const notes: string[] = [];
  let ops: Op[] = (incoming.operations ?? []).map(o => JSON.parse(JSON.stringify(o)));

  // ---- 1. which part of the timeline
  if (opts.clip) {
    const { tStart, tEnd } = opts.clip;
    ops = ops.filter(o => {
      const s = (typeof o.start === "number" ? o.start : 0), e = typeof o.end === "number" ? o.end : Infinity;
      const ns = tStart + s, ne = Math.min(tStart + e, tEnd);
      if (ns >= tEnd - 1e-6) { notes.push(`'${o.id}' starts after the selected clip ends - left out`); return false; }
      o.start = ns; o.end = ne; return true;
    });
  }

  // ---- 2. background layers: only one background may be active at a time
  const exRepl = existing.operations.filter(o => o.type === "background_replacement" && o.enabled !== false).map(o => rangeOf(o, duration));
  const drop = new Set<string>();
  for (const o of ops) if (o.type === "background_replacement" && exRepl.some(r => overlaps(rangeOf(o, duration), r))) drop.add(o.id);
  if (drop.size) {
    let grew = true;                                               // everything built on a dropped background goes with it (looks bound to it) ...
    while (grew) { grew = false; for (const o of ops) if (!drop.has(o.id) && (o.depends_on ?? []).some(d => drop.has(d))) { drop.add(o.id); grew = true; } }
    const needed = new Set(ops.filter(o => !drop.has(o.id)).flatMap(o => o.depends_on ?? []));
    const fed = new Set(ops.filter(o => drop.has(o.id)).flatMap(o => o.depends_on ?? []));
    for (const o of ops) if (!drop.has(o.id) && fed.has(o.id) && !needed.has(o.id) && (o.type === "chroma_key" || o.type === "background_removal")) drop.add(o.id);   // ... and keys that only fed it
    notes.push(`this timeline already has a background layer on that part, so the blueprint's own background (${[...drop].join(", ")}) was not added. Delete the existing one first if you want to replace it`);
    ops = ops.filter(o => !drop.has(o.id));
  }

  // ---- 3. assets: same id + same file = reuse; same id, other file = rename the incoming one
  const assets = [...(existing.assets ?? [])];
  const assetMap = new Map<string, string>();
  for (const a of incoming.assets ?? []) {
    const same = assets.find(x => x.id === a.id);
    if (!same) { assets.push({ ...a }); continue; }
    if (same.path === a.path) continue;
    let n = 2; while (assets.some(x => x.id === `${a.id}_${n}`)) n++;
    assetMap.set(a.id, `${a.id}_${n}`); assets.push({ ...a, id: `${a.id}_${n}` });
    notes.push(`asset '${a.id}' already exists with a different file; the blueprint's file was added as '${a.id}_${n}'`);
  }

  // ---- 4. operation ids: never collide with the existing ones (links between the incoming operations follow the rename)
  const used = new Set(existing.operations.map(o => o.id));
  const idMap = new Map<string, string>();
  for (const o of ops) {
    let id = o.id;
    if (used.has(id)) { let n = 2; while (used.has(`${o.id}_${n}`) || ops.some(x => x !== o && x.id === `${o.id}_${n}`)) n++; id = `${o.id}_${n}`; idMap.set(o.id, id); }
    used.add(id);
  }
  for (const o of ops) {
    o.depends_on = (o.depends_on ?? []).map(d => idMap.get(d) ?? d);
    if (!o.depends_on.length) delete o.depends_on;
    const spec = schema?.operations[o.type];
    if (spec && o.parameters) for (const [k, v] of Object.entries(o.parameters)) if (spec.parameters[k]?.type === "asset" && typeof v === "string" && assetMap.has(v)) o.parameters[k] = assetMap.get(v);
  }
  for (const o of ops) if (idMap.has(o.id)) o.id = idMap.get(o.id)!;

  // ---- 5. keep everything that was there
  if (!opts.applyOutput && incoming.output && JSON.stringify(incoming.output) !== JSON.stringify(existing.output ?? {}) && Object.keys(incoming.output).length)
    notes.push("the blueprint's output settings were not applied (your project's own are kept)");
  if (incoming.clips?.length) notes.push("the blueprint's cuts (clips) were not applied (your timeline's cuts are kept)");
  const bp: Blueprint = { ...existing, operations: [...existing.operations, ...ops], assets,
    ...(opts.applyOutput && incoming.output ? { output: { ...(existing.output ?? {}), ...incoming.output } } : {}) };
  return { bp, added: ops.map(o => o.id), notes };
}
