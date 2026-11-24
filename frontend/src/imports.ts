import type { Ctx } from "./Sidebar";
import * as m from "./model";

const VIDEO_EXT = ["mp4", "mov", "mkv", "webm", "avi", "m4v"];
const ext = (p: string) => (p.split(".").pop() ?? "").toLowerCase();
const stem = (p: string) => (p.split(/[\\/]/).pop() ?? "asset").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 40) || "asset";

/** copy the file into the project and register it as an asset; returns its id (unique) or null if cancelled / failed */
export async function importAssetFile(ctx: Ctx, title: string, kind: "image" | "any" = "any"): Promise<string | null> {
  const p = await ctx.askPath(title, kind);
  if (!p) return null;
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
export function addBackground(ctx: Ctx, assetId?: string) {
  const spec = ctx.schema.operations["background_replacement"];
  const d = ctx.duration || ctx.video?.duration || 5;
  const a = m.snap(Math.min(ctx.t, Math.max(0, d - 1)), ctx.video?.fps ?? 30), b = Math.min(d, m.snap(a + Math.min(5, d), ctx.video?.fps ?? 30));
  let id = "";
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
