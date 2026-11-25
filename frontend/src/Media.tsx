import React, { useEffect, useMemo, useState } from "react";
import type { MediaBin, MediaItem } from "./api";
import type { Ctx } from "./Sidebar";
import { Btn, Icon } from "./ui";
import { applyLut, importAssetFile, importAssetPath, importBackground, importVideoFile, useBackgroundItem } from "./imports";

export const MEDIA_MIME = "application/x-evp-media";
export interface MediaDrag { src: "asset" | "source"; kind: "image" | "video" | "lut"; path: string; name: string }

const fmtDur = (s?: number) => (s === undefined ? "" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);
const fmtSize = (n?: number) => (n === undefined ? "" : n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

/** the project's Media bin: every source video and every imported background / LUT stays here, usable again and again (click or drag onto the timeline) */
export function MediaBinPanel({ ctx }: { ctx: Ctx }) {
  const { api, project, bp, schema } = ctx;
  const [bin, setBin] = useState<MediaBin | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "video" | "image" | "lut">("all");
  const [tick, setTick] = useState(0);
  const [over, setOver] = useState<"" | "sources" | "assets">("");
  useEffect(() => { let alive = true; api.mediaBin().then(b => alive && setBin(b)).catch(() => {}); return () => { alive = false; }; },
    [api, project.path, project.source?.path, bp.assets.length, tick]);

  // which stored files the timeline is using right now
  const usedPaths = useMemo(() => {
    const ids = new Set<string>();
    for (const o of bp.operations) for (const [k, v] of Object.entries(o.parameters ?? {})) if (schema.operations[o.type]?.parameters[k]?.type === "asset" && typeof v === "string") ids.add(v);
    return new Set(bp.assets.filter(a => ids.has(a.id)).map(a => a.path));
  }, [bp, schema]);

  const match = (it: MediaItem) => (!q.trim() || it.name.toLowerCase().includes(q.trim().toLowerCase())) && (filter === "all" || it.kind === filter);
  const sources = (bin?.sources ?? []).filter(match), assets = (bin?.assets ?? []).filter(match);
  const refresh = () => setTick(t => t + 1);
  const fileOf = (e: React.DragEvent) => { const f = e.dataTransfer.files?.[0]; return f ? (window.evp?.pathFor ? window.evp.pathFor(f) : (f as any).path) as string | undefined : undefined; };

  const useSource = async (it: MediaItem) => {
    if (it.current) return;
    try { ctx.reload(await api.setSource(it.path)); ctx.toast(`'${it.name}' is now the source video`); } catch (e: any) { ctx.toast(e.message, "err"); }
  };
  const remove = async (it: MediaItem) => {
    const inUse = usedPaths.has(it.path);
    if (!window.confirm(inUse ? `'${it.name}' is used on the timeline. Remove it from the project anyway? (Your original file is not touched.)` : `Remove '${it.name}' from this project's media? (Your original file is not touched.)`)) return;
    try {
      await api.removeAsset(it.path);
      ctx.edit(b => ({ ...b, assets: b.assets.filter(a => a.path !== it.path) }));
      refresh();
    } catch (e: any) { ctx.toast(e.message, "err"); }
  };
  const use = (it: MediaItem) => it.kind === "lut" ? applyLut(ctx, it, ctx.t) : useBackgroundItem(ctx, it, ctx.t);
  const dragStart = (e: React.DragEvent, it: MediaItem, src: "asset" | "source") => {
    const d: MediaDrag = { src, kind: it.kind, path: it.path, name: it.name };
    e.dataTransfer.setData(MEDIA_MIME, JSON.stringify(d)); e.dataTransfer.effectAllowed = "copy";
  };

  const card = (it: MediaItem, src: "asset" | "source") => {
    const missing = !it.exists, inUse = src === "source" ? !!it.current : usedPaths.has(it.path);
    return (
      <div key={it.path} className={`mcard ${missing ? "missing" : ""} ${inUse ? "used" : ""}`} draggable={!missing} onDragStart={e => dragStart(e, it, src)}
        onDoubleClick={() => (src === "source" ? useSource(it) : use(it))} title={`${it.name}${it.width ? ` · ${it.width}×${it.height}` : ""}${it.duration !== undefined ? ` · ${fmtDur(it.duration)}` : ""}${missing ? " · FILE MISSING" : ""}`}>
        <div className="mthumb">
          {it.kind === "lut" ? <Icon n="color" size={28} /> : missing ? <Icon n="warn" size={26} /> :
            <img src={api.thumbUrl(src, it.path)} alt="" loading="lazy" draggable={false} onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />}
          {it.kind === "video" && it.duration !== undefined && <span className="mdur">{fmtDur(it.duration)}</span>}
          {inUse && <span className="mbadge">{src === "source" ? "On timeline" : "In use"}</span>}
        </div>
        <div className="mname">{it.name}</div>
        <div className="mmeta">{missing ? "file missing" : [it.width ? `${it.width}×${it.height}` : "", it.kind === "lut" ? "LUT" : "", fmtSize(it.size)].filter(Boolean).join(" · ")}</div>
        <div className="mact">
          {src === "source"
            ? <Btn label={it.current ? "Current" : "Use"} icon={it.current ? "check" : "film"} disabled={it.current || missing} onClick={() => useSource(it)} title="Make this the project's source video" />
            : <Btn label={it.kind === "lut" ? "Apply" : "Use"} icon={it.kind === "lut" ? "color" : "plus"} disabled={missing} onClick={() => use(it)}
                title={it.kind === "lut" ? "Add a colour grade with this LUT at the playhead" : "Add this as the background at the playhead (or drag it onto the Background track)"} />}
          {src === "asset" && <Btn icon="trash" title="Remove from this project's media" onClick={() => remove(it)} />}
        </div>
      </div>);
  };

  return (<>
    <h3>Media</h3>
    <p className="muted">Everything you import stays here for this project. Double-click, press <b>Use</b>, or drag a card onto the timeline.</p>
    <div className="btn-row">
      <Btn icon="film" label="Import video" kind="primary" onClick={async () => { await importVideoFile(ctx); refresh(); }} title="The footage you edit (only referenced, never modified)" />
      <Btn icon="image" label="Background" onClick={async () => { await importBackground(ctx); refresh(); }} title="Picture or video to put behind the person" />
      <Btn icon="color" label="LUT" onClick={async () => { await importAssetFile(ctx, "Import a colour LUT (.cube / .3dl)", "any"); refresh(); }} />
    </div>
    <div className="mfilter">
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search media…" aria-label="Search media" />
      {(["all", "video", "image", "lut"] as const).map(f => <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f === "all" ? "All" : f === "video" ? "Videos" : f === "image" ? "Pictures" : "LUTs"}</button>)}
    </div>
    {project.missing.source && <div className="issue error"><Icon n="warn" size={14} /> Source missing: {project.missing.source}</div>}

    <h4>Source videos</h4>
    <div className={"mzone" + (over === "sources" ? " over" : "")}
      onDragOver={e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver("sources"); } }} onDragLeave={() => setOver("")}
      onDrop={async e => { setOver(""); const p = fileOf(e); if (!p) return; e.preventDefault(); try { ctx.reload(await api.setSource(p)); ctx.toast("Video imported (original is never modified)"); } catch (er: any) { ctx.toast(er.message, "err"); } }}>
      {sources.length === 0 ? <div className="mempty">{bin ? "No source video yet. Import one, or drop a video file here." : "Loading…"}</div>
        : <div className="mgrid">{sources.map(it => card(it, "source"))}</div>}
      <p className="muted small">One source video is edited at a time; earlier ones stay here so you can switch back.</p>
    </div>

    <h4>Backgrounds and LUTs</h4>
    <div className={"mzone" + (over === "assets" ? " over" : "")}
      onDragOver={e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver("assets"); } }} onDragLeave={() => setOver("")}
      onDrop={async e => { setOver(""); const p = fileOf(e); if (!p) return; e.preventDefault(); await importAssetPath(ctx, p); refresh(); }}>
      {assets.length === 0 ? <div className="mempty">{bin ? (q || filter !== "all" ? "Nothing matches." : "No backgrounds yet. Import one, or drop a picture / video file here.") : "Loading…"}</div>
        : <div className="mgrid">{assets.map(it => card(it, "asset"))}</div>}
    </div>
  </>);
}
