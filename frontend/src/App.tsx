import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { importAssetFile, importBackground, importVideoFile, applyLut, useBackgroundItem } from "./imports";
import type { MediaDrag } from "./Media";
import { Api, ApiError, JobState, ProjectState, ReportConfig, Validation, AiDevice, connection } from "./api";
import * as m from "./model";
import type { Blueprint, Schema } from "./model";
import { Preview, Mode } from "./Preview";
import { Timeline } from "./Timeline";
import { GpuChip } from "./Gpu";
import { mergeBlueprint, applyStrength, ExecOptions } from "./merge";
import { Properties } from "./Properties";
import { Sidebar, TABS, Tab, Ctx } from "./Sidebar";
import { Btn, Empty, Icon, fmtEta, resolutionOptions } from "./ui";

type Toast = { id: number; msg: string; kind: "ok" | "err" };
const ACTIVE = ["queued", "running", "paused"];

export default function App() {
  const conn = useMemo(connection, []);
  const api = useMemo(() => (conn ? new Api(conn.base, conn.token) : null), [conn]);
  if (!api) return <Fatal title="Backend not connected">Start the app via Electron, or open this page with <code>?port=…&amp;token=…</code> (see docs/DEVELOPER.md).</Fatal>;
  return <Editor api={api} />;
}

const Fatal = ({ title, children }: { title: string; children: React.ReactNode }) => <div className="fatal"><Empty icon="warn" title={title}>{children}</Empty></div>;

function Editor({ api }: { api: Api }) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [project, setProject] = useState<ProjectState | null>(null);
  const [hist, setHist] = useState<m.History<Blueprint>>(() => m.newHistory(m.emptyBlueprint()));
  const bp = hist.present;
  const srcFps = project?.video?.fps ?? 30;
  const tl = useMemo(() => m.layoutClips(bp, project?.video?.duration ?? 0, m.gridFps(bp, srcFps)), [bp, project?.video?.duration, srcFps]);
  const [sel, setSelRaw] = useState<string | null>(null);
  const [selClip, setSelClipRaw] = useState<string | null>(null);
  const setSel = useCallback((id: string | null) => { setSelRaw(id); if (id) setSelClipRaw(null); }, []);
  const setSelClip = useCallback((id: string | null) => { setSelClipRaw(id); if (id) setSelRaw(null); }, []);
  const [t, setT] = useState(0);
  const [tab, setTab] = useState<Tab>("blueprint");
  const [mode, setMode] = useState<Mode>("original");
  const [zoom, setZoom] = useState(60);
  const [report, setReport] = useState<Validation | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [proxy, setProxy] = useState<{ key: string; path: string } | null>(null);      // key = the CONTENT of the edit it was rendered from (undo/redo makes new objects with the same content)
  const [job, setJob] = useState<JobState | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [importMenu, setImportMenu] = useState(false);
  useEffect(() => { if (window.evp?.isElectron) document.body.classList.add("electron"); }, []);
  const [pathDlg, setPathDlg] = useState<{ title: string; kind: string; resolve: (p: string | null) => void } | null>(null);
  const [saved, setSaved] = useState<Blueprint | null>(null);
  const restoreView = useRef(false);
  const [floorNotes, setFloorNotes] = useState<Record<string, { y: number; cut: boolean }>>({});
  const [hairNotes, setHairNotes] = useState<Record<string, { used: number; skipped: number }>>({});
  const [cameraNotes, setCameraNotes] = useState<Record<string, any>>({});
  const [plateNotes, setPlateNotes] = useState<Record<string, { used: number; rejected: number }>>({});
  const onServer = useRef(false);                  // an auto-save file with unsaved edits exists on disk
  const [autoAt, setAutoAt] = useState<number | null>(null);
  const [layout, setLayout] = useState({ left: 320, right: 330, bottom: 260 });
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const toastId = useRef(0);

  useEffect(() => { api.onPlate = n => setPlateNotes(old => ({ ...old, ...n })); api.onCamera = n => setCameraNotes(old => ({ ...old, ...n })); api.onHair = n => setHairNotes(old => ({ ...old, ...n })); api.onFloor = n => setFloorNotes(old => ({ ...old, ...n })); return () => { api.onPlate = undefined; api.onCamera = undefined; api.onHair = undefined; api.onFloor = undefined; }; }, [api]);
  useEffect(() => { api.schema().then(setSchema).catch(e => setFatal(e.message)); }, [api]);
  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = ++toastId.current; setToasts(t => [...t, { id, msg, kind }]); setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), kind === "err" ? 7000 : 3000);
  }, []);
  // any edit shows its result: the preview leaves "Original" (the untouched source) for "Edited", so a new effect is never invisible
  const edit = useCallback((fn: (b: Blueprint) => Blueprint, key?: string) => { setHist(h => m.commit(h, fn(h.present), key)); setMode(md => (md === "original" ? "edited" : md)); }, []);
  // undo / redo: one place for the buttons above the timeline, the toolbar and Ctrl+Z / Ctrl+Y; says what it undid
  const histRef = useRef(hist); histRef.current = hist;
  const schemaRef = useRef(schema); schemaRef.current = schema;
  const titleOf = useCallback((t: string) => schemaRef.current?.operations[t]?.title ?? t, []);
  const labelNext = (h: m.History<Blueprint>, dir: "undo" | "redo") => {
    const to = dir === "undo" ? h.past[h.past.length - 1] : h.future[0];
    return to ? m.describeChange(dir === "undo" ? to : h.present, dir === "undo" ? h.present : to, titleOf) : "";
  };
  const doUndo = useCallback(() => { const h = histRef.current; if (!h.past.length) return; const l = labelNext(h, "undo"); setHist(m.undo); toast(`Undid: ${l}`); }, [toast, titleOf]);
  const doRedo = useCallback(() => { const h = histRef.current; if (!h.future.length) return; const l = labelNext(h, "redo"); setHist(m.redo); toast(`Redid: ${l}`); }, [toast, titleOf]);
  const askPath = useCallback((title: string, kind: string) => {
    const b = window.evp;
    if (b?.pickFile && b?.pickFolder) {                      // desktop app: the operating system's own dialog, straight away
      const filters = kind === "video" ? [{ name: "Video", extensions: ["mp4", "mov", "mkv", "avi", "webm", "m4v"] }]
        : kind === "image" ? [{ name: "Image", extensions: ["png", "jpg", "jpeg", "webp"] }] : undefined;
      return (kind === "any" && /folder/i.test(title) ? b.pickFolder(title) : b.pickFile({ title, filters })) as Promise<string | null>;
    }
    return new Promise<string | null>(res => setPathDlg({ title, kind, resolve: res }));      // browser / development: type a path
  }, []);

  const load = useCallback((p: ProjectState) => {
    const ui = (p.ui ?? {}) as Record<string, any>;               // the exact view you left: playhead, tab, zoom, selection, panel sizes
    setProject(p); setHist(m.reset(p.blueprint));
    setSaved(p.restored && p.saved_blueprint ? p.saved_blueprint : p.blueprint);     // restored-but-unsaved work counts as unsaved (title dot, Save)
    setSelRaw(typeof ui.sel === "string" ? ui.sel : null); setSelClipRaw(typeof ui.selClip === "string" ? ui.selClip : null);
    setT(typeof ui.t === "number" && ui.t >= 0 ? ui.t : 0); setProxy(null); setReport(null);
    setZoom(typeof ui.zoom === "number" && ui.zoom > 0 ? ui.zoom : 60);
    if (ui.layout && typeof ui.layout.left === "number") setLayout(l => ({ ...l, ...ui.layout }));
    setToasts(ts => ts.filter(x => x.kind !== "err"));                               // an old error (e.g. a wrong path before) no longer applies
    setMode("original"); setTab((ui.tab as Tab) || (p.video ? "blueprint" : "media"));
    restoreView.current = ui.mode === "edited" && !!p.video;
    onServer.current = !!p.restored;
    if (p.restored) toast("Restored your work exactly as you left it", "ok");
  }, [toast]);

  useEffect(() => { if (t > tl.duration) setT(Math.max(0, tl.duration - 1 / tl.fps)); }, [tl.duration]);
  useEffect(() => { if (selClip && !tl.clips.some(c => c.id === selClip)) setSelClipRaw(null); }, [tl, selClip]);

  // ---- footage cutting (clips)
  const cutHere = useCallback(() => {
    const v = project?.video; if (!v) return;
    const r = m.cutAt(bp, t, v.duration, tl.fps);
    if (!r.ids) { toast("Move the playhead inside a clip to cut (not at its very start or end)", "err"); return; }
    edit(() => r.bp); setSelClip(r.ids[1]);
  }, [bp, t, project?.video, tl.fps, edit, toast, setSelClip]);
  const removeClip = useCallback((id?: string | null) => {
    const v = project?.video, cid = id ?? selClip; if (!v || !cid) return;
    if (tl.clips.length < 2) { toast("A project needs at least one clip", "err"); return; }
    const r = m.deleteClip(bp, cid, v.duration, tl.fps);
    edit(() => r.bp); setSelClipRaw(null);
    toast(r.removedOps.length ? `Clip deleted; ${plural(r.removedOps.length, "effect")} that only belonged to it ${r.removedOps.length === 1 ? "was" : "were"} removed` : "Clip deleted");
  }, [bp, selClip, project?.video, tl, edit, toast]);

  // validate (debounced) + autosave (debounced) after every document change
  const first = useRef(true);
  useEffect(() => {
    if (!project) return;
    if (first.current) { first.current = false; }
    const h1 = setTimeout(() => api.validate(bp).then(setReport).catch(() => {}), 400);
    const h2 = setTimeout(() => {
      if (bp !== saved) api.putBlueprint(bp, uiState).then(() => { onServer.current = true; setAutoAt(Date.now()); }).catch(() => {});
      else if (onServer.current) api.putBlueprint(bp, uiState, true).then(() => { onServer.current = false; }).catch(() => {});   // undone back to the saved state: forget the auto-save
    }, 350);      // auto-save: nothing waits for the Save button
    return () => { clearTimeout(h1); clearTimeout(h2); };
  }, [bp, project?.path]);

  // the view (playhead, tab, zoom, selection, panels) is saved too, so reopening shows the same screen
  const uiState = useMemo(() => ({ t, tab, mode, zoom, sel, selClip, layout }), [t, tab, mode, zoom, sel, selClip, layout]);
  const latest = useRef({ bp, saved, uiState, has: false });
  latest.current = { bp, saved, uiState, has: !!project };
  useEffect(() => {
    if (!project) return;
    const h = setTimeout(() => { api.putUi(uiState).catch(() => {}); }, 600);
    return () => clearTimeout(h);
  }, [uiState, project?.path]);
  useEffect(() => {
    const flush = async () => {
      const c = latest.current; if (!c.has) return;
      try { if (c.bp !== c.saved) await api.putBlueprint(c.bp, c.uiState); else await api.putUi(c.uiState); } catch { /* nothing more can be done */ }
    };
    (window as any).__evpFlush = flush;                                   // the desktop shell awaits this before it closes the window
    const onHide = () => { const c = latest.current; if (c.has) api.flushNow(c.bp !== c.saved ? c.bp : undefined, c.uiState); };
    const onVis = () => { if (document.visibilityState === "hidden") onHide(); };
    window.addEventListener("pagehide", onHide); document.addEventListener("visibilitychange", onVis);
    return () => { window.removeEventListener("pagehide", onHide); document.removeEventListener("visibilitychange", onVis); delete (window as any).__evpFlush; };
  }, [api]);

  const dirty = project && bp !== saved;
  const save = useCallback(async () => {
    if (!project) return;
    try { const r = await api.save(bp); onServer.current = false; setSaved(bp); setProject(p => p && { ...p, version: r.version, restored: false }); toast(`Saved (version ${r.version})`); } catch (e: any) { toast(e.message, "err"); }
  }, [api, bp, project, toast]);

  // ---- proxy render / execute
  const pollJob = useCallback(async (j: JobState, onDone: (j: JobState) => void) => {
    setJob(j);
    let cur = j;
    let misses = 0;
    while (ACTIVE.includes(cur.state)) {
      await new Promise(r => setTimeout(r, misses ? Math.min(3000, 300 * 2 ** misses) : 300));
      try { cur = await api.job(cur.id); setJob(cur); misses = 0; }
      catch { if (++misses > 12) { cur = { ...cur, state: "failed", error: "lost contact with the engine while rendering" } as JobState; break; } }   // a busy engine is not a failure: keep asking
    }
    onDone(cur);
  }, [api]);
  const updateProxy = useCallback(async (target: Blueprint = bp, quiet = false): Promise<boolean> => {
    const key = JSON.stringify(target);
    try {
      const r = await api.render(target, "proxy");
      if (r.cached) { setProxy({ key, path: r.output }); if (!quiet) setMode("edited"); return true; }
      let ok = false;
      await pollJob(r.job!, j => { if (j.state === "done") { ok = true; setProxy({ key, path: r.output }); if (!quiet) { setMode("edited"); toast("Preview ready"); } } else if (j.state === "failed") toast("Preview failed: " + j.error, "err"); });
      return ok;
    } catch (e: any) { toast(e.message, "err"); return false; }
  }, [api, bp, pollJob, toast]);

  // The picture in the preview follows every slider live: only the CURRENT frame is rendered (the same engine as the export). The preview VIDEO
  // (for playback) is rendered only when you press "Apply to full video" (or switch Auto on), so editing never re-processes the whole clip.
  const [autoPreview, setAutoPreviewRaw] = useState(() => { try { return localStorage.getItem("evp.autoApply") === "1"; } catch { return false; } });        // off by default: while you edit, only the current frame is rendered
  const setAutoPreview = (v: boolean) => { setAutoPreviewRaw(v); try { localStorage.setItem("evp.autoApply", v ? "1" : "0"); } catch { /* ignore */ } };
  const bpKey = useMemo(() => JSON.stringify(bp), [bp]);
  const failedAuto = useRef<Set<string>>(new Set());                     // edits whose automatic render FAILED: not retried in a loop (the button always retries)
  useEffect(() => {
    if (!project || !project.video || !autoPreview || mode !== "edited") return;
    if (proxy && proxy.key === bpKey) return;                              // already up to date (same content, whatever object)
    if (!report?.ok || tl.duration > 90) return;                           // invalid edits: nothing to render; very long videos: use the button
    if (job && job.kind === "proxy" && ACTIVE.includes(job.state)) return; // one at a time; re-checked when it finishes
    if (failedAuto.current.has(bpKey)) return;
    const h = setTimeout(async () => { const k = bpKey; if (!(await updateProxy(bp, true))) failedAuto.current.add(k); }, 1200);
    return () => clearTimeout(h);
  }, [bpKey, proxy, report, mode, autoPreview, job?.state, project?.path, tl.duration]);
  useEffect(() => {          // reopened on the edited preview: show it again (cached previews come back instantly)
    if (project && restoreView.current) { restoreView.current = false; updateProxy(project.blueprint); }
  }, [project?.path, project?.version]);
  const executeBlueprint = useCallback(async (nb: Blueprint, o: ExecOptions = { how: "add", applyOutput: false, clip: null }) => {
    nb = applyStrength(nb, schema, o.strength ?? 1);               // "Strength" slider of the Blueprint tab: the master Amount of every pasted effect
    let next = nb, msg = "Blueprint applied — showing the current frame. Press Apply to full video to render it all.";
    if (o.how === "add") {
      // ADD: the pasted blueprint is merged into the timeline; nothing that is already there is removed or replaced
      const r = mergeBlueprint(bp, nb, schema, { applyOutput: o.applyOutput, clip: o.clip }, tl.duration);
      next = r.bp;
      try {
        const v = await api.validate(next);
        const lost = v.warnings.find(w => w.code === "SKIPPED_OPERATION" && w.op_id && bp.operations.some(x => x.id === w.op_id));
        if (!v.ok || lost) { toast(`Nothing was changed: ${(v.errors[0] ?? lost)?.message ?? "the result is not valid"}`, "err"); return; }
        if (v.repaired) next = v.repaired as Blueprint;
      } catch { /* the editor validates again after the edit */ }
      msg = `Added ${plural(r.added.length, "effect")}; your ${plural(bp.operations.length, "existing effect")} ${bp.operations.length === 1 ? "was" : "were"} kept` + (r.notes.length ? ` — note: ${r.notes[0]}` : "") + ". Showing the current frame; press Apply to full video to render it all.";
    }
    edit(() => next); setSel(null); setTab("effects");
    toast(msg);                                                    // only the current frame is rendered (live); the whole timeline waits for "Apply to full video"
  }, [edit, toast, bp, schema, tl.duration, api]);

  // keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const all = document.querySelectorAll<HTMLElement>(".modal-bg"), modal = all.length ? all[all.length - 1] : null;
      if (e.key === "Escape") {                                                           // Esc closes the open dialog / popover, else clears the selection
        if (modal) { e.preventDefault(); modal.click(); return; }
        const pop = document.querySelector<HTMLElement>(".gpupop .popclose"); if (pop) { pop.click(); return; }
        if (!["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName)) { setSel(null); setSelClip(null); }
        return;
      }
      if (modal) return;                                                                  // nothing behind an open dialog reacts to keys
      const tag = (e.target as HTMLElement)?.tagName; if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); doRedo(); }
      else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
      else if (e.key === " ") { e.preventDefault(); (window as any).__evpToggle?.(); }
      else if (e.key === "ArrowLeft") (window as any).__evpStep?.(-1);
      else if (e.key === "ArrowRight") (window as any).__evpStep?.(1);
      else if (e.key === "Home") { e.preventDefault(); setT(0); }
      else if (e.key === "End") { e.preventDefault(); setT(Math.max(0, tl.duration - 1 / (tl.fps || 25))); }
      else if ((e.key === "c" || e.key === "C") && !mod) { cutHere(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel) { edit(b => m.deleteOp(b, sel)); setSel(null); }
      else if ((e.key === "Delete" || e.key === "Backspace") && selClip) { removeClip(); }
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [save, sel, selClip, edit, cutHere, removeClip, doUndo, doRedo, tl.duration, tl.fps]);

  if (fatal) return <Fatal title="Backend error">{fatal}</Fatal>;
  if (!schema) return <Fatal title="Starting…">Connecting to the processing engine.</Fatal>;
  if (!project) return <><Welcome api={api} onOpen={load} toast={toast} askPath={askPath} /><Toasts toasts={toasts} onClose={id => setToasts(ts => ts.filter(x => x.id !== id))} /><PathDialog dlg={pathDlg} close={() => setPathDlg(null)} /></>;

  const video = project.video;
  const stale = !proxy || proxy.key !== bpKey;
  const issues = [...(report?.errors ?? []), ...(report?.warnings ?? [])];
  const ctx: Ctx = { api, project, bp, schema, video, duration: tl.duration, t, mode, playing, videoRef, edit, setSel, askPath, toast, reload: load, issues, report, executeBlueprint, setReport, selClip, layout: tl };
  // a card from the Media bin dropped on the timeline: backgrounds on the Background track, LUTs on the Effects track, source videos on the Video track
  const onDropMedia = (d: MediaDrag, at: number, lane: string) => {
    if (d.kind === "lut") { if (lane !== "effects") { toast("Drop a LUT on the Effects track", "err"); return; } applyLut(ctx, d, at); return; }
    if (d.src === "source") {
      if (lane !== "video") { toast("Drop a source video on the Video track", "err"); return; }
      if (d.path === project.source?.path) return;
      api.setSource(d.path).then(p => { load(p); toast(`'${d.name}' is now the source video`); }).catch((e: any) => toast(e.message, "err")); return;
    }
    if (lane !== "background") { toast("Drop a background on the Background track", "err"); return; }
    useBackgroundItem(ctx, d, at);
  };
  const drag = (key: "left" | "right" | "bottom", sign: 1 | -1) => (e: React.PointerEvent) => {
    const start = key === "bottom" ? e.clientY : e.clientX, v0 = layout[key]; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const mv = (ev: PointerEvent) => setLayout(l => ({ ...l, [key]: Math.max(key === "bottom" ? 140 : 220, Math.min(key === "bottom" ? 520 : 560, v0 + sign * ((key === "bottom" ? ev.clientY : ev.clientX) - start))) }));
    const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  };

  return (
    <div className="app" style={{ gridTemplateColumns: `56px ${layout.left}px 5px 1fr 5px ${layout.right}px`, gridTemplateRows: `48px 1fr 5px ${layout.bottom}px 26px` }}>
      <header className="toolbar" style={{ gridColumn: "1 / -1" }}>
        <span className="logo"><Icon n="film" size={18} /> EVP</span>
        <b className="pname">{project.name}{dirty ? " •" : ""}</b>
        <span className="autosave" title="Every change is written to disk within a second, so closing or a crash loses nothing. Save creates a numbered version you can go back to.">{!dirty ? "Saved" : autoAt ? `Auto-saved ${new Date(autoAt).toLocaleTimeString()}` : "Auto-saving…"}</span>
        <span className="sep" />
        <span className="menu-wrap">
          <Btn icon="plus" label="Import" onClick={() => setImportMenu(v => !v)} title="Import a video, a background picture or a LUT" />
          {importMenu && <div className="menu" role="menu" onMouseLeave={() => setImportMenu(false)}>
            <button role="menuitem" onClick={() => { setImportMenu(false); importVideoFile(ctx); }}><Icon n="film" size={15} /> Video (the footage to edit)…</button>
            <button role="menuitem" onClick={() => { setImportMenu(false); importBackground(ctx); }}><Icon n="image" size={15} /> Background image or video…</button>
            <button role="menuitem" onClick={() => { setImportMenu(false); importAssetFile(ctx, "Import a colour LUT (.cube / .3dl)", "any"); }}><Icon n="color" size={15} /> Colour LUT…</button>
          </div>}
        </span>
        <Btn icon="save" label="Save" onClick={save} title="Save (Ctrl+S)" />
        <Btn icon="undo" onClick={doUndo} disabled={!hist.past.length} title="Undo (Ctrl+Z)" />
        <Btn icon="redo" onClick={doRedo} disabled={!hist.future.length} title="Redo (Ctrl+Y)" />
        <span className="spacer" />
        <Btn icon="bolt" label="Execute Blueprint" kind={bp.operations.length === 0 ? "primary" : undefined} onClick={() => setTab("blueprint")} title="Open the blueprint panel to paste, validate and execute" />
        <Btn icon="eye" label="Apply to full video" kind={bp.operations.length > 0 && stale ? "primary" : undefined} onClick={() => updateProxy()} disabled={!video || (report ? !report.ok : false)} title="Happy with this frame? Render the whole timeline (640px preview video for playback). While you edit, only the current frame is rendered. Export makes the final full-quality file." />
        <Btn icon="export" label="Export" onClick={() => setExportOpen(true)} disabled={!video} />
        <GpuChip api={api} working={!!job && ACTIVE.includes(job.state)} />
        <Btn icon="gear" onClick={() => setSettingsOpen(true)} title="Settings: problem reports" />
      </header>

      <nav className="rail" aria-label="Panels">
        {TABS.map(x => <button key={x.id} className={tab === x.id ? "on" : ""} onClick={() => setTab(x.id)} title={x.label} aria-label={x.label}><Icon n={x.icon} size={18} /><span>{x.label}</span></button>)}
      </nav>
      <aside className="side"><Sidebar tab={tab} ctx={ctx} /></aside>
      <div className="vhandle" onPointerDown={drag("left", 1)} />

      <main className="center">
        {project.restored && dirty && (
          <div className="recovery"><Icon n="check" /> Restored your work exactly as you left it{project.restored_at ? ` (${new Date(project.restored_at * 1000).toLocaleString()})` : ""}. It was auto-saved but not yet saved as a version.
            <Btn label="Save now" kind="primary" onClick={save} />
            <Btn label="Back to last saved version" onClick={async () => { try { load(await api.revert()); toast("Went back to the last saved version"); } catch (e: any) { toast(e.message, "err"); } }} /></div>)}
        <Preview zone={(() => { const o = bp.operations.find(x => x.id === sel); const q = o?.parameters ?? {};
            if (!o || o.type !== "studio_look" || !(typeof q.colour_light === "number" && q.colour_light > 0) || q.colour_light_place !== "zone") return null;
            const n = (k: string, d: number) => (typeof q[k] === "number" ? q[k] : d);
            return { x0: n("colour_light_zone_x0", 0.25), y0: n("colour_light_zone_y0", 0.1), x1: n("colour_light_zone_x1", 0.75), y1: n("colour_light_zone_y1", 0.9), colour: typeof q.colour_light_color_a === "string" ? q.colour_light_color_a : "#19b8ff",
              onChange: (patch: Record<string, any>) => edit(b => Object.entries(patch).reduce((acc, [k, v]) => m.setParam(acc, o.id, k, v), b), `zone:${o.id}`) }; })()}
          masks={{ ops: bp.operations.find(o => o.id === sel)?.type === "matte_mask" ? bp.operations.filter(o => o.type === "matte_mask" && o.enabled !== false) : [], selected: bp.operations.find(o => o.id === sel)?.type === "matte_mask" ? sel : null,
            onChange: (id, patch) => edit(b => Object.entries(patch).reduce((acc, [k, v]) => m.setParam(acc, id, k, v), b), `mask:${id}`), onSelect: setSel }} playing={playing} setPlaying={setPlaying} api={api} bp={bp} video={video} layout={tl} t={t} setT={setT} mode={mode} setMode={setMode} proxyUrl={proxy ? api.mediaUrl(proxy.path) : null}
          stale={stale} onUpdateProxy={() => { failedAuto.current.delete(bpKey); updateProxy(); }} auto={autoPreview} setAuto={setAutoPreview} proxyBusy={!!job && job.kind === "proxy" && ACTIVE.includes(job.state)} videoRef={videoRef} />
      </main>
      <div className="vhandle" onPointerDown={drag("right", -1)} />
      <aside className="side right"><Properties floorNotes={floorNotes} hairNotes={hairNotes} cameraNotes={cameraNotes} keyDoctor={(id: string) => api.keyDoctor(bp, id)} importAsset={() => importAssetFile(ctx, "Import the clean plate (a video or picture of the EMPTY green screen)", "any")} plateNotes={plateNotes} bp={bp} schema={schema} video={video} layout={tl} sel={sel} selClip={selClip} edit={edit} t={t} setT={setT} issues={issues} onSelect={setSel} onSelectClip={setSelClip}
          onCut={cutHere} onDeleteClip={() => removeClip()} onReapply={() => updateProxy()} /></aside>

      <div className="hhandle" style={{ gridColumn: "2 / -1" }} onPointerDown={drag("bottom", -1)} />
      <div style={{ gridColumn: "1 / -1" }}>
        <Timeline onDropMedia={onDropMedia} history={{ canUndo: hist.past.length > 0, canRedo: hist.future.length > 0, undoCount: hist.past.length, redoCount: hist.future.length, undoLabel: labelNext(hist, "undo"), redoLabel: labelNext(hist, "redo"), onUndo: doUndo, onRedo: doRedo }} bp={bp} schema={schema} video={video} layout={tl} t={t} setT={setT} sel={sel} setSel={setSel} selClip={selClip} setSelClip={setSelClip} edit={edit} issues={issues} zoom={zoom} setZoom={setZoom}
          onCut={cutHere} onDeleteClip={() => removeClip()} sourceName={project.source?.path?.split(/[\\/]/).pop() ?? "source"} />
      </div>
      <footer className="status" style={{ gridColumn: "1 / -1" }}>
        {(() => {
          const warns = (report?.warnings ?? []).filter(w => w.code !== "NO_OPERATIONS");             // an empty project is not a problem
          const txt = !report ? "Checking…" : !report.ok ? `✖ ${plural(report.errors.length, "problem")} — click to see` : report.plan.length === 0 ? "No effects yet: add one from Effects, or paste a blueprint"
            : `✔ ${plural(report.plan.length, "effect")} ready` + (warns.length ? ` · ${plural(warns.length, "note")} — click to see` : "");
          return <button className={"status-link" + (report && !report.ok ? " bad" : "")} onClick={() => setTab("blueprint")} title="Open the project check in the Blueprint panel">{txt}</button>;
        })()}
        {job && <JobBar job={job} api={api} onUpdate={setJob} compact />}
        <span className="spacer" /><span>{hist.past.length ? plural(hist.past.length, "undo step") : "Nothing to undo"}</span>
      </footer>
      {settingsOpen && <SettingsDialog api={api} onClose={() => setSettingsOpen(false)} toast={toast} />}
      {exportOpen && <ExportDialog askPath={askPath} api={api} bp={bp} edit={edit} onClose={() => setExportOpen(false)} job={job} setJob={setJob} pollJob={pollJob} toast={toast} projectName={project.name} video={video} />}
      <Toasts toasts={toasts} onClose={id => setToasts(ts => ts.filter(x => x.id !== id))} /><PathDialog dlg={pathDlg} close={() => setPathDlg(null)} />
    </div>
  );
}

function JobBar({ job, api, onUpdate, compact }: { job: JobState; api: Api; onUpdate: (j: JobState) => void; compact?: boolean }) {
  const act = async (a: "pause" | "resume" | "cancel" | "retry") => { try { onUpdate(await api.jobAction(job.id, a)); } catch { /* shown via state */ } };
  const live = ACTIVE.includes(job.state);
  return (
    <div className={`jobbar ${job.state}`}>
      <span>{job.kind === "proxy" ? "Preview" : "Export"}: {job.state}</span>
      <progress value={job.progress} max={1} aria-label="progress" /><span>{Math.round(job.progress * 100)}%</span>{live && <span>ETA {fmtEta(job.eta_seconds)}</span>}
      {live && job.current.startsWith("AI matting") && <span className="muted">{job.current}</span>}
      {live && job.state === "running" && <Btn label="Pause" onClick={() => act("pause")} />}{job.state === "paused" && <Btn label="Resume" onClick={() => act("resume")} />}
      {live && <Btn label="Cancel" kind="danger" onClick={() => act("cancel")} />}{(job.state === "failed" || job.state === "cancelled") && <Btn label="Retry" onClick={() => act("retry")} />}
      {job.error && <span className="err-text" title={job.error}>{job.error.length > 110 ? job.error.slice(0, 110) + "…" : job.error}</span>}
      {job.error && <Btn label="Copy error" onClick={() => { navigator.clipboard?.writeText([job.error, ...job.log.slice(-6)].join("\n")).catch(() => {}); }} title="Copy the full error and the last log lines to send to support" />}
    </div>);
}

function ExportDialog({ askPath, api, bp, edit, onClose, job, setJob, pollJob, toast, projectName, video }: { askPath: Ctx["askPath"]; api: Api; bp: Blueprint; edit: Ctx["edit"]; onClose: () => void; video: ProjectState["video"]; job: JobState | null; setJob: (j: JobState | null) => void; pollJob: (j: JobState, d: (j: JobState) => void) => Promise<void>; toast: Ctx["toast"]; projectName: string }) {
  const o = bp.output ?? {};
  const [name, setName] = useState(projectName);
  const [dir, setDirRaw] = useState(() => { try { return localStorage.getItem("evp.exportDir") ?? ""; } catch { return ""; } });   // "" = the project's own exports folder
  const setDir = (d: string) => { setDirRaw(d); try { localStorage.setItem("evp.exportDir", d); } catch { /* ignore */ } };
  const browse = async () => { const p = await askPath("Choose the export folder", "any"); if (p) setDir(p); };
  const [enc, setEnc] = useState("auto");
  const [sys, setSys] = useState<any>(null);
  const [log, setLog] = useState(false);
  const [exportJob, setExportJob] = useState<JobState | null>(job?.kind === "render" ? job : null);
  useEffect(() => { api.system().then(setSys).catch(() => {}); }, [api]);
  useEffect(() => { if (job?.kind === "render") setExportJob(job); }, [job]);
  const start = async () => {
    try {
      const r = await api.render(bp, "export", { filename: name, encoder: enc, ...(dir ? { out_dir: dir } : {}) });
      await pollJob(r.job!, j => { if (j.state === "done") toast("Export finished: " + j.output); else if (j.state === "failed") toast("Export failed: " + j.error, "err"); });
    } catch (e: any) { toast(e instanceof ApiError ? e.message : String(e), "err"); }
  };
  const running = !!exportJob && ACTIVE.includes(exportJob.state);
  const set = (p: Record<string, any>) => edit(b => m.setOutput(b, p), "out");
  return (
    <div className="modal-bg" onClick={() => !running && onClose()}>
      <div className="modal" onClick={e => e.stopPropagation()} role="dialog" aria-label="Export">
        <h3>Export final video</h3>
        <div className="grid2">
          <label className="field"><span className="fl">file name</span><input value={name} onChange={e => setName(e.target.value)} /></label>
          <div className="field exportdir" style={{ gridColumn: "1 / -1" }}><span className="fl">save to</span>
            <div className="slider-row">
              <input readOnly value={dir || "The project's exports folder"} title={dir || "Inside this project's folder"} aria-label="Export folder" onClick={browse} />
              <Btn icon="folder" label="Choose folder…" onClick={browse} disabled={running} title="Pick where the exported video is saved" />
              {dir && <Btn label="Project folder" onClick={() => setDir("")} disabled={running} title="Back to the project's own exports folder" />}
            </div>
            <span className="muted small">An existing file with the same name is never overwritten: the new one is saved as “name (2)”.</span></div>
          <label className="field"><span className="fl">container</span><select value={o.format ?? "mp4"} onChange={e => set({ format: e.target.value })}><option disabled={["prores", "prores4444", "vp9_alpha"].includes(o.codec ?? "")}>mp4</option><option disabled={o.codec === "vp9_alpha"}>mov</option><option disabled={o.codec !== "vp9_alpha"} value="webm">webm</option></select></label>
          {(o.codec === "prores") && <label className="field"><span className="fl">ProRes profile</span><select value={o.prores_profile ?? "hq"} onChange={e => set({ prores_profile: e.target.value })}><option value="proxy">Proxy</option><option value="lt">LT</option><option value="standard">422</option><option value="hq">422 HQ</option></select></label>}
          <label className="field"><span className="fl">resolution</span><select value={String(o.resolution ?? "source")} onChange={e => set({ resolution: e.target.value })}>{resolutionOptions(video, o.resolution).map(r => <option key={r}>{r}</option>)}</select></label>
          <label className="field"><span className="fl">frame rate</span><select value={String(o.fps ?? "source")} onChange={e => set({ fps: e.target.value === "source" ? "source" : parseFloat(e.target.value) })}>{["source", "24", "25", "30", "50", "60"].map(r => <option key={r}>{r}</option>)}</select></label>
          <label className="field"><span className="fl">codec</span><select value={o.codec ?? "h264"} onChange={e => set(e.target.value === "prores" || e.target.value === "prores4444" ? { codec: e.target.value, format: "mov" } : e.target.value === "vp9_alpha" ? { codec: "vp9_alpha", format: "webm" } : { codec: e.target.value, format: "mp4" })}><option value="h264">H.264</option><option value="h265">H.265 / HEVC</option><option value="prores">ProRes 422 10-bit (.mov, for editing)</option><option value="prores4444">Transparent: ProRes 4444 (.mov, for editors)</option><option value="vp9_alpha">Transparent: WebM VP9 (.webm, for browsers)</option></select></label>
          {(o.codec === "h264" || o.codec === "h265" || o.codec === undefined || o.codec === "vp9_alpha") && <label className="field"><span className="fl">quality CRF (lower = better)</span><input type="number" min={0} max={51} value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) || 20 })} /></label>}
          {(o.codec === "h264" || o.codec === "h265" || o.codec === undefined) && <label className="field"><span className="fl">bitrate (optional, e.g. 12M)</span><input value={o.video_bitrate ?? ""} onChange={e => set({ video_bitrate: e.target.value || undefined })} /></label>}
          {(o.codec === "h264" || o.codec === "h265" || o.codec === undefined) && <label className="field"><span className="fl">audio bitrate</span><select value={o.audio_bitrate ?? "192k"} onChange={e => set({ audio_bitrate: e.target.value })}>{["128k", "192k", "256k", "320k"].map(r => <option key={r}>{r}</option>)}</select></label>}
          <label className="field"><span className="fl">encoder</span><select value={enc} onChange={e => setEnc(e.target.value)}><option value="auto">Auto (GPU if it works, else CPU)</option><option value="cpu">CPU only</option></select></label>
        </div>
        {sys && <p className="muted">{sys.cpu_count} CPU threads · hardware encoders: {sys.hardware_encoders_working.length ? sys.hardware_encoders_working.join(", ") : "none detected (CPU encode)"} · AI device: {sys.ai_devices?.[0] ?? "unavailable"}{sys.ai_devices?.[0] === "CPU" ? " (install onnxruntime-gpu / -directml for GPU)" : ""}</p>}
        {exportJob && <JobBar job={exportJob} api={api} onUpdate={j => { setExportJob(j); setJob(j); }} />}
        {exportJob && <><div className="muted">{exportJob.current}{exportJob.hardware_fallback ? " · GPU encoder failed, used CPU" : ""}</div>
          {exportJob.state === "done" && <div className="issue ok"><Icon n="check" size={14} /> Saved to {exportJob.output}{window.evp?.reveal && <Btn icon="folder" label="Show in folder" onClick={() => window.evp!.reveal!(exportJob.output)} />}</div>}
          <Btn label={log ? "Hide log" : "Show render log"} onClick={() => setLog(l => !l)} />{log && <pre className="log">{exportJob.log.join("\n")}</pre>}</>}
        <div className="btn-row end"><Btn label="Close" onClick={onClose} disabled={running} /><Btn icon="export" kind="primary" label="Start export" onClick={start} disabled={running} /></div>
      </div>
    </div>);
}

function AiDeviceBox({ api }: { api: Api }) {
  const [d, setD] = useState<AiDevice | null>(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  useEffect(() => { api.aiDevice().then(setD).catch(() => {}); }, [api]);
  const choose = async (v: string) => { setBusy(true); setErr(""); try { setD(await api.setAiDevice(v)); } catch (e: any) { setErr(String(e.message ?? e)); } setBusy(false); };
  if (!d) return null;
  const gpu = d.provider !== "CPU";
  return (
    <div style={{ marginBottom: 14 }}>
      <h3>AI device</h3>
      <p className="muted">Runs the AI cut-out, upscaling and face tools. Currently: <b>{gpu ? d.running_on : "the processor (no graphics card in use)"}</b>.
        {!gpu && " On Windows the graphics card is used through DirectML; if you have one and this still says processor, update the app from GitHub and reinstall its requirements."}</p>
      <select value={d.choice} disabled={busy} onChange={e => choose(e.target.value)} aria-label="AI device" style={{ width: "100%" }}>
        {d.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {d.fell_back && <div className="issue warning">The graphics card ({d.wanted}) could not be used - DirectML refused it - so the processor was used instead. Pick another device in the list, or update the graphics driver.</div>}
      {err && <div className="issue error">{err}</div>}
    </div>);
}

function AboutBox() {
  const [info, setInfo] = useState<{ version: string; packaged: boolean; logDir: string } | null>(null);
  useEffect(() => { window.evp?.app?.info().then(setInfo).catch(() => {}); }, []);
  return (
    <div style={{ marginBottom: 14 }}>
      <h3>About</h3>
      <p className="muted">EVP Editor {info?.version ?? ""}{info && !info.packaged ? " (development)" : ""}. Updates come from the project's GitHub releases; nothing is downloaded or installed without asking you.</p>
      <div className="btn-row"><Btn label="Check for updates" onClick={() => window.evp?.app?.checkUpdates()} /><Btn icon="folder" label="Open logs folder" onClick={() => window.evp?.app?.openLogs()} /></div>
    </div>);
}

function SettingsDialog({ api, onClose, toast }: { api: Api; onClose: () => void; toast: Ctx["toast"] }) {
  const [cfg, setCfg] = useState<ReportConfig | null>(null);
  const [endpoint, setEndpoint] = useState(""); const [bucket, setBucket] = useState("evp-reports");
  const [keyId, setKeyId] = useState(""); const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(() => api.reportConfig().then(c => { setCfg(c); setEndpoint(e => e || c.endpoint); setBucket(b => c.bucket || b); }).catch(() => {}), [api]);
  useEffect(() => { load(); }, [load]);
  const save = async (extra: Record<string, any> = {}) => {
    setBusy(true);
    try {
      const c = await api.saveReportConfig({ endpoint: endpoint || undefined, bucket: bucket || undefined, access_key_id: keyId || undefined, secret_access_key: secret || undefined, ...extra });
      setCfg(c); setKeyId(""); setSecret(""); setMsg("Saved.");
    } catch (e: any) { setMsg(e instanceof ApiError ? e.message : String(e)); }
    setBusy(false);
  };
  const test = async () => {
    setBusy(true); setMsg("Sending a test report…");
    try { const r = await api.reportTest(); setMsg(r.ok ? "Test report delivered." : `Could not deliver: ${r.error ?? "unknown error"}${r.queued ? " (kept to retry later)" : ""}`); await load(); }
    catch (e: any) { setMsg(String(e)); }
    setBusy(false);
  };
  const send = async () => {
    setBusy(true);
    try { const r = await api.reportSend(note); setMsg(r.ok ? "Report sent. Thank you." : `Could not send: ${r.error}`); if (r.ok) setNote(""); await load(); }
    catch (e: any) { setMsg(String(e)); }
    setBusy(false);
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} role="dialog" aria-label="Settings">
        <AiDeviceBox api={api} />
        {window.evp?.app && <AboutBox />}
        <h3>Problem reports</h3>
        <p className="muted">When a render fails, the app can upload a small diagnostic file (error, log tail, versions, your blueprint with file names only) to <b>your own</b> Cloudflare R2 bucket. It never contains video, audio or frames, and home-folder names are masked. Your keys are stored only on this computer (owner-only file) and are never sent anywhere except to your R2 endpoint.</p>
        <div className="grid2">
          <label className="field"><span className="fl">R2 endpoint <em>(https://&lt;account&gt;.r2.cloudflarestorage.com)</em></span><input value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder="https://…r2.cloudflarestorage.com" /></label>
          <label className="field"><span className="fl">bucket name</span><input value={bucket} onChange={e => setBucket(e.target.value)} /></label>
          <label className="field"><span className="fl">Access Key ID <em>{cfg?.access_key_id_masked ? `(saved ${cfg.access_key_id_masked})` : ""}</em></span><input value={keyId} onChange={e => setKeyId(e.target.value)} autoComplete="off" placeholder={cfg?.access_key_id_masked ? "leave empty to keep" : ""} /></label>
          <label className="field"><span className="fl">Secret Access Key {cfg?.has_secret ? "(saved)" : ""}</span><input type="password" value={secret} onChange={e => setSecret(e.target.value)} autoComplete="new-password" placeholder={cfg?.has_secret ? "leave empty to keep" : ""} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={cfg?.auto_send ?? true} onChange={e => save({ auto_send: e.target.checked })} /> Send a report automatically when a render fails (at most 6 per hour, never the same error twice)</label>
        <div className="btn-row"><Btn label="Save" kind="primary" onClick={() => save()} disabled={busy} /><Btn label="Send test report" onClick={test} disabled={busy || !cfg?.configured} /></div>
        <h3 style={{ marginTop: 16 }}>Report a problem now</h3>
        <textarea rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder="What went wrong? (optional)" style={{ width: "100%" }} />
        <div className="btn-row"><Btn label="Send report" onClick={send} disabled={busy || !cfg?.configured} /></div>
        {msg && <div className="issue">{msg}</div>}
        {cfg && !cfg.configured && <p className="muted">Not configured: reports stay on this computer until you add the endpoint, bucket and keys above.</p>}
        {!!cfg?.pending && <p className="muted">{cfg.pending} report(s) waiting to be delivered (no connection earlier); they are retried automatically.</p>}
        <div className="btn-row end"><Btn label="Close" onClick={onClose} /></div>
      </div>
    </div>);
}

/** 1 effect / 2 effects */
export function plural(n: number, word: string): string { return `${n} ${word}${n === 1 ? "" : "s"}`; }

function Welcome({ api, onOpen, toast, askPath }: { api: Api; onOpen: (p: ProjectState) => void; toast: Ctx["toast"]; askPath: Ctx["askPath"] }) {
  const [recent, setRecent] = useState<{ path: string; name: string; modified?: number; unsaved?: boolean }[]>([]);
  const [defs, setDefs] = useState<{ projects_dir: string } | null>(null);
  const [over, setOver] = useState(false); const [busy, setBusy] = useState(false);
  const [adv, setAdv] = useState(false);
  const [name, setName] = useState("My Project"); const [parent, setParent] = useState(""); const [source, setSource] = useState("");
  useEffect(() => { api.recent().then(setRecent).catch(() => {}); api.defaults().then(setDefs).catch(() => {}); }, [api]);
  const go = async (f: () => Promise<ProjectState>) => { setBusy(true); try { onOpen(await f()); } catch (e: any) { toast(e.message, "err"); } setBusy(false); };
  const withVideo = async () => { const p = await askPath("Choose your video (MP4, MOV, MKV, AVI, WebM)", "video"); if (p) go(() => api.quickProject(p)); };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setOver(false);
    const f = e.dataTransfer.files?.[0]; if (!f) return;
    const path = window.evp?.pathFor ? window.evp.pathFor(f) : (f as any).path;
    if (!path) { toast("Drag and drop is not available here: click the box to choose the file", "err"); return; }
    go(() => api.quickProject(path));
  };
  const short = (p: string) => p.length > 58 ? p.slice(0, 22) + " … " + p.slice(-30) : p;
  return (
    <div className="welcome2">
      <div className="dragbar" />
      <div className="hero">
        <span className="mark"><Icon n="film" size={26} /></span>
        <div><h1>EVP Editor</h1><p>AI blueprint video editor: key, replace backgrounds, grade, enhance. Everything stays editable.</p></div>
      </div>
      <div className="cols">
        <section className="wcard start">
          <h3>Start</h3>
          <div className={"drop" + (over ? " over" : "")} role="button" tabIndex={0} aria-label="Choose a video"
            onClick={() => !busy && withVideo()} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") withVideo(); }}
            onDragOver={e => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
            <Icon n="export" size={34} />
            <b>{busy ? "Creating your project…" : "Drop a video here"}</b>
            <span>{busy ? "one moment" : "or click to choose a file"}</span>
            <em>MP4 · MOV · MKV · AVI · WebM</em>
          </div>
          <div className="btn-row">
            <Btn icon="plus" label="Empty project" disabled={busy} onClick={() => go(() => api.quickProject())} title="Start without a video; add one later" />
            <Btn icon="folder" label="Open project…" disabled={busy} onClick={async () => { const p = await askPath("Open a .evproj folder", "any"); if (p) go(() => api.openProject(p)); }} />
            <span className="spacer" />
            <button className="link" onClick={() => setAdv(a => !a)}>{adv ? "Hide" : "Name and folder…"}</button>
          </div>
          {adv && <div className="adv">
            <label className="field"><span className="fl">project name</span><input value={name} onChange={e => setName(e.target.value)} /></label>
            <label className="field"><span className="fl">save in folder <em>(default: {defs?.projects_dir ?? "…"})</em></span><div className="slider-row"><input value={parent} onChange={e => setParent(e.target.value)} placeholder="Default folder" /><Btn label="Browse" onClick={async () => { const p = await askPath("Choose the projects folder", "any"); if (p) setParent(p); }} /></div></label>
            <label className="field"><span className="fl">video (optional)</span><div className="slider-row"><input value={source} onChange={e => setSource(e.target.value)} placeholder="Choose a video file…" /><Btn label="Browse" onClick={async () => { const p = await askPath("Choose your video (MP4, MOV, MKV, AVI, WebM)", "video"); if (p) setSource(p); }} /></div></label>
            <Btn kind="primary" icon="plus" label="Create project" disabled={!name.trim() || busy}
              onClick={() => go(() => parent.trim() ? api.newProject(parent.trim(), name.trim(), source.trim() || undefined) : api.quickProject(source.trim() || undefined, name.trim()))} />
          </div>}
        </section>
        <section className="wcard recent">
          <h3>Recent projects</h3>
          {recent.length === 0 && <p className="muted">Nothing yet. Your projects will appear here.</p>}
          {recent[0] && <button className="continue" disabled={busy} onClick={() => go(() => api.openProject(recent[0].path))}><Icon n="bolt" size={15} /> Continue where you left off: <b>{recent[0].name}</b></button>}
          <div className="recent-list">{recent.map(r => <button className="item" key={r.path} onClick={() => go(() => api.openProject(r.path))}><Icon n="folder" size={15} /><span><b>{r.name}{r.unsaved && <i className="badge" title="Auto-saved work that was never saved as a version. Opening it restores exactly that state.">auto-saved</i>}</b><em>{r.modified ? new Date(r.modified * 1000).toLocaleString() + " · " : ""}{short(r.path)}</em></span></button>)}</div>
        </section>
      </div>
      <footer className="foot">{defs ? `New projects are saved in ${defs.projects_dir}` : ""}</footer>
    </div>);
}

function PathDialog({ dlg, close }: { dlg: { title: string; kind: string; resolve: (p: string | null) => void } | null; close: () => void }) {
  const [v, setV] = useState("");
  useEffect(() => { setV(""); }, [dlg]);
  if (!dlg) return null;
  const done = (p: string | null) => { dlg.resolve(p); close(); };
  const browse = async () => {
    const b = window.evp;
    const p = dlg.kind === "any" && dlg.title.toLowerCase().includes("folder") ? await b?.pickFolder?.(dlg.title)
      : await b?.pickFile?.({ title: dlg.title, filters: dlg.kind === "video" ? [{ name: "Video", extensions: ["mp4", "mov", "mkv", "avi", "webm"] }] : dlg.kind === "image" ? [{ name: "Image", extensions: ["png", "jpg", "jpeg", "webp"] }] : undefined });
    if (p) done(p);
  };
  return (
    <div className="modal-bg" onClick={() => done(null)}><div className="modal small" onClick={e => e.stopPropagation()} role="dialog" aria-label={dlg.title}>
      <h3>{dlg.title}</h3>
      <input autoFocus value={v} onChange={e => setV(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && v.trim()) done(v.trim()); }} placeholder="Full path" />
      <div className="btn-row end">{window.evp?.pickFile && <Btn icon="folder" label="Browse…" onClick={browse} />}<Btn label="Cancel" onClick={() => done(null)} /><Btn kind="primary" label="OK" disabled={!v.trim()} onClick={() => done(v.trim())} /></div>
    </div></div>);
}

const Toasts = ({ toasts, onClose }: { toasts: Toast[]; onClose: (id: number) => void }) => <div className="toasts" role="status" aria-live="polite">{toasts.map(t => <div key={t.id} className={`toast ${t.kind}`} onClick={() => onClose(t.id)} title="Click to close">{t.msg}</div>)}</div>;
