import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Api, ApiError, JobState, ProjectState, Validation, connection } from "./api";
import * as m from "./model";
import type { Blueprint, Schema } from "./model";
import { Preview, Mode } from "./Preview";
import { Timeline } from "./Timeline";
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
  const [proxy, setProxy] = useState<{ bp: Blueprint; path: string } | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [pathDlg, setPathDlg] = useState<{ title: string; kind: string; resolve: (p: string | null) => void } | null>(null);
  const [saved, setSaved] = useState<Blueprint | null>(null);
  const [layout, setLayout] = useState({ left: 320, right: 330, bottom: 260 });
  const videoRef = useRef<HTMLVideoElement>(null);
  const toastId = useRef(0);

  useEffect(() => { api.schema().then(setSchema).catch(e => setFatal(e.message)); }, [api]);
  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = ++toastId.current; setToasts(t => [...t, { id, msg, kind }]); setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), kind === "err" ? 7000 : 3000);
  }, []);
  const edit = useCallback((fn: (b: Blueprint) => Blueprint, key?: string) => setHist(h => m.commit(h, fn(h.present), key)), []);
  const askPath = useCallback((title: string, kind: string) => new Promise<string | null>(res => setPathDlg({ title, kind, resolve: res })), []);

  const load = useCallback((p: ProjectState) => {
    setProject(p); setHist(m.reset(p.blueprint)); setSaved(p.blueprint); setSelRaw(null); setSelClipRaw(null); setT(0); setProxy(null); setReport(null);
    setMode("original"); setTab(p.video ? "blueprint" : "media");
    if (p.recovery_available) toast("Unsaved changes from a previous session were found — see the banner above the timeline.", "err");
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
    toast(r.removedOps.length ? `Clip deleted; ${r.removedOps.length} effect(s) that were only on it were removed` : "Clip deleted");
  }, [bp, selClip, project?.video, tl, edit, toast]);

  // validate (debounced) + autosave (debounced) after every document change
  const first = useRef(true);
  useEffect(() => {
    if (!project) return;
    if (first.current) { first.current = false; }
    const h1 = setTimeout(() => api.validate(bp).then(setReport).catch(() => {}), 400);
    const h2 = setTimeout(() => { if (bp !== saved) api.putBlueprint(bp).catch(() => {}); }, 1200);
    return () => { clearTimeout(h1); clearTimeout(h2); };
  }, [bp, project?.path]);

  const dirty = project && bp !== saved;
  const save = useCallback(async () => {
    if (!project) return;
    try { const r = await api.save(bp); setSaved(bp); setProject(p => p && { ...p, version: r.version }); toast(`Saved (version ${r.version})`); } catch (e: any) { toast(e.message, "err"); }
  }, [api, bp, project, toast]);

  // ---- proxy render / execute
  const pollJob = useCallback(async (j: JobState, onDone: (j: JobState) => void) => {
    setJob(j);
    let cur = j;
    while (ACTIVE.includes(cur.state)) { await new Promise(r => setTimeout(r, 300)); try { cur = await api.job(cur.id); setJob(cur); } catch { break; } }
    onDone(cur);
  }, [api]);
  const updateProxy = useCallback(async (target: Blueprint = bp) => {
    try {
      const r = await api.render(target, "proxy");
      if (r.cached) { setProxy({ bp: target, path: r.output }); setMode("edited"); return; }
      await pollJob(r.job!, j => { if (j.state === "done") { setProxy({ bp: target, path: r.output }); setMode("edited"); toast("Preview ready"); } else if (j.state === "failed") toast("Preview failed: " + j.error, "err"); });
    } catch (e: any) { toast(e.message, "err"); }
  }, [api, bp, pollJob, toast]);
  const executeBlueprint = useCallback(async (nb: Blueprint) => {
    // keep imported blueprint's own asset list; existing project assets with same id are replaced by the blueprint's definition
    edit(() => nb); setSel(null); setTab("effects");
    toast("Blueprint applied — rendering preview…");
    await updateProxy(nb);
  }, [edit, updateProxy, toast]);

  // keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName; if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); setHist(e.shiftKey ? m.redo : m.undo); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); setHist(m.redo); }
      else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
      else if (e.key === " ") { e.preventDefault(); (window as any).__evpToggle?.(); }
      else if (e.key === "ArrowLeft") (window as any).__evpStep?.(-1);
      else if (e.key === "ArrowRight") (window as any).__evpStep?.(1);
      else if ((e.key === "c" || e.key === "C") && !mod) { cutHere(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel) { edit(b => m.deleteOp(b, sel)); setSel(null); }
      else if ((e.key === "Delete" || e.key === "Backspace") && selClip) { removeClip(); }
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [save, sel, selClip, edit, cutHere, removeClip]);
  useEffect(() => { const f = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } }; window.addEventListener("beforeunload", f); return () => window.removeEventListener("beforeunload", f); }, [dirty]);

  if (fatal) return <Fatal title="Backend error">{fatal}</Fatal>;
  if (!schema) return <Fatal title="Starting…">Connecting to the processing engine.</Fatal>;
  if (!project) return <><Welcome api={api} onOpen={load} toast={toast} askPath={askPath} /><Toasts toasts={toasts} /><PathDialog dlg={pathDlg} close={() => setPathDlg(null)} /></>;

  const video = project.video;
  const stale = !proxy || proxy.bp !== bp;
  const issues = [...(report?.errors ?? []), ...(report?.warnings ?? [])];
  const ctx: Ctx = { api, project, bp, schema, video, duration: tl.duration, t, edit, setSel, askPath, toast, reload: load, issues, report, executeBlueprint, setReport };
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
        <span className="sep" />
        <Btn icon="plus" label="Import" onClick={() => setTab("media")} title="Import media" />
        <Btn icon="save" label="Save" onClick={save} title="Save (Ctrl+S)" />
        <Btn icon="undo" onClick={() => setHist(m.undo)} disabled={!hist.past.length} title="Undo (Ctrl+Z)" />
        <Btn icon="redo" onClick={() => setHist(m.redo)} disabled={!hist.future.length} title="Redo (Ctrl+Shift+Z)" />
        <span className="spacer" />
        <Btn icon="bolt" label="Execute Blueprint" kind="primary" onClick={() => setTab("blueprint")} title="Open the blueprint panel to paste, validate and execute" />
        <Btn icon="eye" label="Render preview" onClick={() => updateProxy()} disabled={!video || (report ? !report.ok : false)} title="Render the 640px proxy preview" />
        <Btn icon="export" label="Export" onClick={() => setExportOpen(true)} disabled={!video} />
      </header>

      <nav className="rail" aria-label="Panels">
        {TABS.map(x => <button key={x.id} className={tab === x.id ? "on" : ""} onClick={() => setTab(x.id)} title={x.label} aria-label={x.label}><Icon n={x.icon} size={18} /><span>{x.label}</span></button>)}
      </nav>
      <aside className="side"><Sidebar tab={tab} ctx={ctx} /></aside>
      <div className="vhandle" onPointerDown={drag("left", 1)} />

      <main className="center">
        {project.recovery_available && (
          <div className="recovery"><Icon n="warn" /> Unsaved edits from an earlier session were recovered.
            <Btn label="Restore them" kind="primary" onClick={async () => { try { load(await api.recover()); toast("Recovered"); } catch (e: any) { toast(e.message, "err"); } }} />
            <Btn label="Discard" onClick={async () => { await api.discardAutosave(); setProject(p => p && { ...p, recovery_available: false }); }} /></div>)}
        <Preview api={api} bp={bp} video={video} layout={tl} t={t} setT={setT} mode={mode} setMode={setMode} proxyUrl={proxy ? api.mediaUrl(proxy.path) : null}
          stale={stale} onUpdateProxy={() => updateProxy()} proxyBusy={!!job && job.kind === "proxy" && ACTIVE.includes(job.state)} videoRef={videoRef} />
      </main>
      <div className="vhandle" onPointerDown={drag("right", -1)} />
      <aside className="side right"><Properties bp={bp} schema={schema} video={video} layout={tl} sel={sel} selClip={selClip} edit={edit} t={t} issues={issues} onSelect={setSel} onSelectClip={setSelClip}
          onCut={cutHere} onDeleteClip={() => removeClip()} onReapply={() => updateProxy()} /></aside>

      <div className="hhandle" style={{ gridColumn: "2 / -1" }} onPointerDown={drag("bottom", -1)} />
      <div style={{ gridColumn: "1 / -1" }}>
        <Timeline bp={bp} schema={schema} video={video} layout={tl} t={t} setT={setT} sel={sel} setSel={setSel} selClip={selClip} setSelClip={setSelClip} edit={edit} issues={issues} zoom={zoom} setZoom={setZoom}
          onCut={cutHere} onDeleteClip={() => removeClip()} sourceName={project.source?.path?.split(/[\\/]/).pop() ?? "source"} />
      </div>
      <footer className="status" style={{ gridColumn: "1 / -1" }}>
        <span>{report ? (report.ok ? `✔ ${report.plan.length} ops valid` : `✖ ${report.errors.length} error(s)`) : "…"}{report?.warnings.length ? ` · ${report.warnings.length} warning(s)` : ""}</span>
        {job && <JobBar job={job} api={api} onUpdate={setJob} compact />}
        <span className="spacer" /><span>{hist.past.length} undo step(s)</span>
      </footer>
      {exportOpen && <ExportDialog api={api} bp={bp} edit={edit} onClose={() => setExportOpen(false)} job={job} setJob={setJob} pollJob={pollJob} toast={toast} projectName={project.name} video={video} />}
      <Toasts toasts={toasts} /><PathDialog dlg={pathDlg} close={() => setPathDlg(null)} />
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
      {job.error && <span className="err-text" title={job.error}>{job.error.slice(0, 80)}</span>}
    </div>);
}

function ExportDialog({ api, bp, edit, onClose, job, setJob, pollJob, toast, projectName, video }: { api: Api; bp: Blueprint; edit: Ctx["edit"]; onClose: () => void; video: ProjectState["video"]; job: JobState | null; setJob: (j: JobState | null) => void; pollJob: (j: JobState, d: (j: JobState) => void) => Promise<void>; toast: Ctx["toast"]; projectName: string }) {
  const o = bp.output ?? {};
  const [name, setName] = useState(projectName);
  const [enc, setEnc] = useState("auto");
  const [sys, setSys] = useState<any>(null);
  const [log, setLog] = useState(false);
  const [exportJob, setExportJob] = useState<JobState | null>(job?.kind === "render" ? job : null);
  useEffect(() => { api.system().then(setSys).catch(() => {}); }, [api]);
  useEffect(() => { if (job?.kind === "render") setExportJob(job); }, [job]);
  const start = async () => {
    try {
      const r = await api.render(bp, "export", { filename: name, encoder: enc });
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
          <label className="field"><span className="fl">container</span><select value={o.format ?? "mp4"} onChange={e => set({ format: e.target.value })}><option disabled={o.codec === "prores"}>mp4</option><option>mov</option></select></label>
          {o.codec === "prores" && <label className="field"><span className="fl">ProRes profile</span><select value={o.prores_profile ?? "hq"} onChange={e => set({ prores_profile: e.target.value })}><option value="proxy">Proxy</option><option value="lt">LT</option><option value="standard">422</option><option value="hq">422 HQ</option></select></label>}
          <label className="field"><span className="fl">resolution</span><select value={String(o.resolution ?? "source")} onChange={e => set({ resolution: e.target.value })}>{resolutionOptions(video, o.resolution).map(r => <option key={r}>{r}</option>)}</select></label>
          <label className="field"><span className="fl">frame rate</span><select value={String(o.fps ?? "source")} onChange={e => set({ fps: e.target.value === "source" ? "source" : parseFloat(e.target.value) })}>{["source", "24", "25", "30", "50", "60"].map(r => <option key={r}>{r}</option>)}</select></label>
          <label className="field"><span className="fl">codec</span><select value={o.codec ?? "h264"} onChange={e => set(e.target.value === "prores" ? { codec: "prores", format: "mov" } : { codec: e.target.value })}><option value="h264">H.264</option><option value="h265">H.265 / HEVC</option><option value="prores">ProRes 422 10-bit (.mov, for editing)</option></select></label>
          {o.codec !== "prores" && <label className="field"><span className="fl">quality CRF (lower = better)</span><input type="number" min={0} max={51} value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) || 20 })} /></label>}
          {o.codec !== "prores" && <label className="field"><span className="fl">bitrate (optional, e.g. 12M)</span><input value={o.video_bitrate ?? ""} onChange={e => set({ video_bitrate: e.target.value || undefined })} /></label>}
          {o.codec !== "prores" && <label className="field"><span className="fl">audio bitrate</span><select value={o.audio_bitrate ?? "192k"} onChange={e => set({ audio_bitrate: e.target.value })}>{["128k", "192k", "256k", "320k"].map(r => <option key={r}>{r}</option>)}</select></label>}
          <label className="field"><span className="fl">encoder</span><select value={enc} onChange={e => setEnc(e.target.value)}><option value="auto">Auto (GPU if it works, else CPU)</option><option value="cpu">CPU only</option></select></label>
        </div>
        {sys && <p className="muted">{sys.cpu_count} CPU threads · hardware encoders: {sys.hardware_encoders_working.length ? sys.hardware_encoders_working.join(", ") : "none detected (CPU encode)"} · AI device: {sys.ai_devices?.[0] ?? "unavailable"}{sys.ai_devices?.[0] === "CPU" ? " (install onnxruntime-gpu / -directml for GPU)" : ""}</p>}
        {exportJob && <JobBar job={exportJob} api={api} onUpdate={j => { setExportJob(j); setJob(j); }} />}
        {exportJob && <><div className="muted">{exportJob.current}{exportJob.hardware_fallback ? " · GPU encoder failed, used CPU" : ""}</div>
          {exportJob.state === "done" && <div className="issue ok"><Icon n="check" size={14} /> Saved to {exportJob.output}</div>}
          <Btn label={log ? "Hide log" : "Show render log"} onClick={() => setLog(l => !l)} />{log && <pre className="log">{exportJob.log.join("\n")}</pre>}</>}
        <div className="btn-row end"><Btn label="Close" onClick={onClose} disabled={running} /><Btn icon="export" kind="primary" label="Start export" onClick={start} disabled={running} /></div>
      </div>
    </div>);
}

function Welcome({ api, onOpen, toast, askPath }: { api: Api; onOpen: (p: ProjectState) => void; toast: Ctx["toast"]; askPath: Ctx["askPath"] }) {
  const [recent, setRecent] = useState<{ path: string; name: string }[]>([]);
  const [name, setName] = useState("My Project"); const [parent, setParent] = useState(""); const [source, setSource] = useState("");
  useEffect(() => { api.recent().then(setRecent).catch(() => {}); }, [api]);
  const go = async (f: () => Promise<ProjectState>) => { try { onOpen(await f()); } catch (e: any) { toast(e.message, "err"); } };
  return (
    <div className="welcome">
      <div className="panel">
        <h1><Icon n="film" size={28} /> EVP <small>AI Blueprint Video Editor</small></h1>
        <p className="muted">Import a video, paste the editing blueprint produced by your AI, and the engine executes it. Everything stays editable.</p>
        <h3>New project</h3>
        <label className="field"><span className="fl">name</span><input value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="field"><span className="fl">save in folder</span><div className="slider-row"><input value={parent} onChange={e => setParent(e.target.value)} placeholder="/path/to/Projects" /><Btn label="Browse" onClick={async () => { const p = await askPath("Folder for the project", "any"); if (p) setParent(p); }} /></div></label>
        <label className="field"><span className="fl">original video (optional now)</span><div className="slider-row"><input value={source} onChange={e => setSource(e.target.value)} placeholder="/path/to/video.mp4" /><Btn label="Browse" onClick={async () => { const p = await askPath("Original video", "video"); if (p) setSource(p); }} /></div></label>
        <Btn kind="primary" icon="plus" label="Create project" disabled={!name.trim() || !parent.trim()} onClick={() => go(() => api.newProject(parent.trim(), name.trim(), source.trim() || undefined))} />
      </div>
      <div className="panel">
        <h3>Recent projects</h3>
        {recent.length === 0 && <p className="muted">None yet.</p>}
        {recent.map(r => <button className="item" key={r.path} onClick={() => go(() => api.openProject(r.path))}><Icon n="folder" size={14} /> {r.name} <em>{r.path}</em></button>)}
        <Btn icon="folder" label="Open project folder…" onClick={async () => { const p = await askPath("Open a .evproj folder", "any"); if (p) go(() => api.openProject(p)); }} />
      </div>
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

const Toasts = ({ toasts }: { toasts: Toast[] }) => <div className="toasts" role="status" aria-live="polite">{toasts.map(t => <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>)}</div>;
