import type { Ctx } from "./Sidebar";
import * as m from "./model";

const VIDEO_EXT = ["mp4", "mov", "mkv", "webm", "avi", "m4v"];
const ext = (p: string) => (p.split(".").pop() ?? "").toLowerCase();
const stem = (p: string) => (p.split(/[\\/]/).pop() ?? "asset").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 40) || "asset";

/** copy the file into the project and register it as an asset; returns its id (unique) or null if cancelled / failed */
export async function importAssetFile(ctx: Ctx, title: string, kind: "image" | "any" = "any"): Promise<string | null> {
  const p = await ctx.askPath(title, kind);
  if (!p) return null;
  return importAssetPath(ctx, p);
}

/** same, for a path you already have (drag and drop from the computer) */
export async function importAssetPath(ctx: Ctx, p: string): Promise<string | null> {
  const e = ext(p);
  const type = ["cube", "3dl"].includes(e) ? "lut" : VIDEO_EXT.includes(e) ? "video" : "image";
  let id = stem(p), n = 1;
  while (ctx.bp.assets.some(a => a.id === id)) id = `${stem(p)}_${++n}`;
  try {
    const r = await ctx.api.importAsset(p);
    ctx.edit(bp => m.addAsset(bp, { id, type, path: r.path }));
    ctx.toast(`Imported '${id}'`);
    return id;
  } catch (err: any) { ctx.toast(err.message, "err"); return null; }
}

/** add a background_replacement over the current selection range using an asset (or a colour) */
export function addBackground(ctx: Ctx, assetId?: string, at?: number) {
  const spec = ctx.schema.operations["background_replacement"];
  const d = ctx.duration || ctx.video?.duration || 5;
  const a = m.snap(Math.min(at ?? ctx.t, Math.max(0, d - 1)), ctx.video?.fps ?? 30), b = Math.min(d, m.snap(a + Math.min(5, d), ctx.video?.fps ?? 30));
  let id = "";
  // only ONE background can be active at a time: if one already covers this time, change it instead of stacking a second one (which would be a conflict)
  const here = ctx.bp.operations.find(o => o.type === "background_replacement" && o.enabled !== false && o.start < b && (o.end ?? d) > a);
  const fpsv = ctx.video?.fps ?? 30;
  if (here && at !== undefined && a > here.start + 1 / fpsv) {
    // dropped at a later point: the old background ends there and the new one takes over from that point (never two at once)
    const end = Math.max(b, here.end ?? d);
    ctx.edit(bp => { const cut = m.setOpTime(bp, here.id, here.start, a); const r = m.addOp(cut, spec, a, end); id = r.id; return m.setParam(r.bp, r.id, assetId ? "asset_id" : "color", assetId ?? "#101820"); });
    setTimeout(() => ctx.setSel(id), 0);
    ctx.toast("New background from this point on; the previous one ends here");
    return;
  }
  if (here) {
    ctx.edit(bp => m.setParam(m.setParam(bp, here.id, "asset_id", assetId ?? undefined), here.id, "color", assetId ? undefined : (here.parameters?.color ?? "#101820")), `bg:${here.id}`);
    setTimeout(() => ctx.setSel(here.id), 0);
    ctx.toast(assetId ? `Background changed to ${assetId}` : "Background set to a solid colour (pick it in Properties)");
    return;
  }
  ctx.edit(bp => { const r = m.addOp(bp, spec, a, b); id = r.id; return m.setParam(r.bp, r.id, assetId ? "asset_id" : "color", assetId ?? "#101820"); });
  setTimeout(() => ctx.setSel(id), 0);
}

export async function importVideoFile(ctx: Ctx) {
  const p = await ctx.askPath("Import original video (MP4, MOV, MKV, AVI, WebM)", "video");
  if (!p) return;
  try { ctx.reload(await ctx.api.setSource(p)); ctx.toast("Video imported (original is never modified)"); } catch (e: any) { ctx.toast(e.message, "err"); }
}

/** pick a background picture or video, store it in the project and use it straight away */
export async function importBackground(ctx: Ctx) {
  const id = await importAssetFile(ctx, "Import a background (image or video)", "image");
  if (id && ctx.video) addBackground(ctx, id);
  else if (id) ctx.toast("Add a video first, then choose this background in the Backgrounds panel");
}


// ---------------------------------------------------------------------------------------------- Media bin helpers
/** the blueprint id of a project file (registering it first if the bin lists a file the blueprint does not know, e.g. after an Undo) */
export function ensureAsset(ctx: Ctx, item: { path: string; kind: string; name: string }): string {
  const have = ctx.bp.assets.find(a => a.path === item.path);
  if (have) return have.id;
  let id = stem(item.name), n = 1;
  while (ctx.bp.assets.some(a => a.id === id)) id = `${stem(item.name)}_${++n}`;
  ctx.edit(bp => m.addAsset(bp, { id, type: item.kind, path: item.path }));
  return id;
}

/** colour LUT from the bin -> a colour grading effect using it */
export function applyLut(ctx: Ctx, item: { path: string; kind: string; name: string }, at?: number) {
  const spec = ctx.schema.operations["color_grading"];
  const id = ensureAsset(ctx, item);
  const d = ctx.duration || ctx.video?.duration || 5;
  const a = m.snap(Math.min(at ?? 0, Math.max(0, d - 1)), ctx.video?.fps ?? 30);
  let opId = "";
  ctx.edit(bp => { const r = m.addOp(bp, spec, a, d); opId = r.id; return m.setParam(r.bp, r.id, "lut_asset_id", id); });
  setTimeout(() => ctx.setSel(opId), 0);
}

/** a stored background (picture or video) from the bin -> a background layer at `at` seconds */
export function useBackgroundItem(ctx: Ctx, item: { path: string; kind: string; name: string }, at?: number) {
  if (!ctx.video) { ctx.toast("Import a source video first, then use this background", "err"); return; }
  addBackground(ctx, ensureAsset(ctx, item), at);
}
