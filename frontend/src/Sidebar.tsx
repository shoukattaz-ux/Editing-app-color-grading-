import React, { useEffect, useRef, useState } from "react";
import type { Api, Issue, ProjectState, Validation, VideoInfo } from "./api";
import type { Blueprint, Schema } from "./model";
import type { ExecOptions } from "./merge";
import * as m from "./model";
import { importAssetFile, importBackground, importVideoFile, addBackground } from "./imports";
import { Btn, Empty, Icon } from "./ui";
import { ScopesPanel } from "./ScopesPanel";

export type Tab = "media" | "projects" | "blueprint" | "effects" | "backgrounds" | "color" | "scopes" | "assets";
export const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "media", label: "Media", icon: "film" }, { id: "projects", label: "Projects", icon: "folder" }, { id: "blueprint", label: "Blueprint", icon: "bolt" },
  { id: "effects", label: "Effects", icon: "wand" }, { id: "backgrounds", label: "Backgrounds", icon: "image" }, { id: "color", label: "Color", icon: "color" }, { id: "scopes", label: "Scopes", icon: "scope" }, { id: "assets", label: "Assets", icon: "layers" },
];
export interface Ctx {
  api: Api; project: ProjectState; bp: Blueprint; schema: Schema; video: VideoInfo | null; duration: number; t: number; mode: string; playing: boolean; videoRef: React.RefObject<HTMLVideoElement>; edit: (fn: (b: Blueprint) => Blueprint, key?: string) => void;
  setSel: (id: string | null) => void; askPath: (title: string, kind: "video" | "image" | "lut" | "any") => Promise<string | null>; toast: (msg: string, kind?: "ok" | "err") => void;
  reload: (p: ProjectState) => void; issues: Issue[]; report: Validation | null; executeBlueprint: (bp: Blueprint, how?: ExecOptions) => Promise<void>; selClip: string | null; layout: m.Layout; setReport: (v: Validation | null) => void;
}

export function Sidebar({ tab, ctx }: { tab: Tab; ctx: Ctx }) {
  return <div className="side-content">{{
    media: <MediaTab ctx={ctx} />, projects: <ProjectsTab ctx={ctx} />, blueprint: <BlueprintTab ctx={ctx} />, effects: <EffectsTab ctx={ctx} />,
    backgrounds: <BackgroundsTab ctx={ctx} />, color: <ColorTab ctx={ctx} />, scopes: <ScopesPanel ctx={ctx} />, assets: <AssetsTab ctx={ctx} />,
  }[tab]}</div>;
}

const span = (ctx: Ctx) => { const d = ctx.duration || ctx.video?.duration || 5; const s = m.snap(Math.min(ctx.t, Math.max(0, d - 1)), ctx.video?.fps ?? 30); return [s, Math.min(d, m.snap(s + Math.min(5, d), ctx.video?.fps ?? 30))] as const; };

function MediaTab({ ctx }: { ctx: Ctx }) {
  const { video, project } = ctx;
  const importVideo = () => importVideoFile(ctx);
  return (<>
    <h3>Source video</h3>
    {video ? <div className="card"><Icon n="film" size={20} /><div><b>{project.source?.path?.split(/[\\/]/).pop()}</b>
      <div className="muted">{video.width}×{video.height} · {video.fps} fps · {video.duration.toFixed(2)} s · {video.codec} · {video.has_audio ? "audio" : "no audio"}</div></div></div>
      : <Empty icon="film" title="No video yet">Import the original video. The file is only referenced — never overwritten.</Empty>}
    {project.missing.source && <div className="issue error"><Icon n="warn" size={14} /> Source missing: {project.missing.source}</div>}
    <Btn icon="plus" kind="primary" label={video ? "Replace / relink video…" : "Import video…"} onClick={importVideo} />
    <h3 style={{ marginTop: 18 }}>Backgrounds and looks</h3>
    <p className="muted">Pictures or videos to put behind the person, and colour LUTs. They are copied into the project folder.</p>
    <div className="btn-row">
      <Btn icon="image" label="Import background…" onClick={() => importBackground(ctx)} title="Pick an image or video; it is added to the project and used as the background right away" />
      <Btn icon="color" label="Import LUT…" onClick={() => importAssetFile(ctx, "Import a colour LUT (.cube / .3dl)", "any")} />
    </div>
  </>);
}

function ProjectsTab({ ctx }: { ctx: Ctx }) {
  const [vers, setVers] = useState<any[]>([]);
  const refresh = () => ctx.api.versions().then(setVers).catch(() => {});
  useEffect(() => { refresh(); }, [ctx.project.version]);
  return (<>
    <h3>{ctx.project.name}</h3>
    <div className="muted">{ctx.project.path}</div>
    <div className="btn-row"><Btn icon="save" label="Save as…" onClick={async () => {
      const parent = await ctx.askPath("Folder for the copy", "any"); if (!parent) return;
      const name = prompt("New project name", ctx.project.name + " copy"); if (!name) return;
      try { ctx.reload(await ctx.api.saveAs(parent, name)); ctx.toast("Saved as " + name); } catch (e: any) { ctx.toast(e.message, "err"); }
    }} /></div>
    <h4>Versions</h4>
    {vers.length === 0 && <p className="muted">Each explicit save (Ctrl+S) keeps a restorable version.</p>}
    {vers.map(v => <div className="row" key={v.file}><span>v{v.version} · {v.operations} ops · {new Date(v.modified * 1000).toLocaleString()}</span>
      <Btn label="Restore" onClick={async () => { try { ctx.reload(await ctx.api.restore(v.file)); ctx.toast("Restored " + v.file); } catch (e: any) { ctx.toast(e.message, "err"); } }} /></div>)}
  </>);
}

function BlueprintTab({ ctx }: { ctx: Ctx }) {
  const [text, setText] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  const [rep, setRep] = useState<Validation | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const check = async () => {
    const { bp, error } = m.parseBlueprintText(text);
    if (error) { const v: Validation = { ok: false, errors: [{ level: "error", code: "INVALID_JSON", message: "Not valid JSON: " + error }], warnings: [], plan: [], video: null }; setRep(v); return null; }
    try { const v = await ctx.api.validate(bp); setRep(v); return { bp: (v.repaired as Blueprint | null | undefined) ?? bp!, v }; } catch (e: any) { ctx.toast(e.message, "err"); return null; }
  };
  const [how, setHow] = useState<"add" | "replace">("add");
  const [applyOutput, setApplyOutput] = useState(false);
  const [scope, setScope] = useState<"all" | "clip">("all");
  const laid = ctx.layout.clips.find(c => c.id === ctx.selClip);
  const canClip = !!laid && ctx.layout.clips.length > 1;
  const run = async () => {
    const r = await check();
    if (r?.v.ok) await ctx.executeBlueprint(r.bp, { how, applyOutput, clip: how === "add" && scope === "clip" && laid && canClip ? { tStart: laid.tStart, tEnd: laid.tEnd } : null });
    else if (r) ctx.toast("Blueprint has errors — nothing was executed", "err");
  };
  return (<>
    <h3>Editing blueprint</h3>
    <p className="muted">Paste or import the JSON blueprint generated by your external AI.</p>
    <textarea className="bp-text" spellCheck={false} value={text} onChange={e => { setText(e.target.value); setRep(null); }} placeholder='{ "blueprint_version": "1.0", "operations": [ … ] }' aria-label="Blueprint JSON" />
    <div className="btn-row">
      <Btn icon="copy" label="Paste" onClick={async () => { try { setText(await navigator.clipboard.readText()); setRep(null); } catch { ctx.toast("Clipboard unavailable — paste with Ctrl+V into the box", "err"); } }} />
      <Btn icon="folder" label="Import JSON" onClick={() => file.current?.click()} />
      <input ref={file} type="file" accept=".json,application/json" hidden onChange={async e => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setRep(null); } e.target.value = ""; }} />
      <Btn icon="reset" label="Load current" title="Show the current project's blueprint" onClick={() => { setText(JSON.stringify(ctx.bp, null, 2)); setRep(null); }} />
    </div>
    <fieldset className="exec-opts">
      <legend>When I execute</legend>
      <label><input type="radio" name="how" checked={how === "add"} onChange={() => setHow("add")} /> <b>Add to my timeline</b> <span className="muted">— keeps everything already there (background removal, backgrounds, effects, cuts)</span></label>
      <label><input type="radio" name="how" checked={how === "replace"} onChange={() => setHow("replace")} /> Replace the whole timeline <span className="muted">— like opening the blueprint as a new project (Undo brings it back)</span></label>
      {how === "add" && <>
        <label className="sub">Apply to
          <select value={canClip ? scope : "all"} onChange={e => setScope(e.target.value as "all" | "clip")} disabled={!canClip} aria-label="Where to apply">
            <option value="all">the whole timeline (times as written)</option>
            <option value="clip">only the selected clip{laid ? ` (${laid.tStart.toFixed(1)}–${laid.tEnd.toFixed(1)} s; its times start at the clip's start)` : ""}</option>
          </select></label>
        {!canClip && <span className="muted sub">Cut the footage and select a clip on the timeline to apply a blueprint to just that clip.</span>}
        <label className="sub"><input type="checkbox" checked={applyOutput} onChange={e => setApplyOutput(e.target.checked)} /> also use the blueprint's output settings (codec, quality, size)</label>
      </>}
    </fieldset>
    <div className="btn-row">
      <Btn icon="check" label="Validate" onClick={check} disabled={!text.trim()} />
      <Btn icon="bolt" kind="primary" label="Execute Blueprint" onClick={run} disabled={!text.trim() || !ctx.video} title={ctx.video ? "Validate, apply to the project and render the preview" : "Import a source video first"} />
    </div>
    {!ctx.video && <div className="issue warning"><Icon n="warn" size={14} /> Import a source video before executing a blueprint.</div>}
    {rep && <Report rep={rep} onSelect={id => ctx.setSel(id)} />}
    {!rep && ctx.report && <><h4>Current project</h4><Report rep={ctx.report} onSelect={id => ctx.setSel(id)} /></>}
  </>);
}

export function Report({ rep, onSelect }: { rep: Validation; onSelect: (id: string) => void }) {
  return (<div className="report">
    <div className={`status ${rep.ok ? "ok" : "bad"}`}>{rep.ok ? <><Icon n="check" size={14} /> {rep.repaired ? "Accepted after automatic repair" : "Valid"} — {rep.plan.length} operation(s) ready</> : <><Icon n="x" size={14} /> {rep.errors.length} error(s)</>}{rep.warnings.length ? ` · ${rep.warnings.length} warning(s)` : ""}</div>
    {[...rep.errors, ...rep.warnings].map((i, k) => <div key={k} className={`issue ${i.level}`} onClick={() => i.op_id && onSelect(i.op_id)} role={i.op_id ? "button" : undefined}>
      <b>{i.code}</b> {i.op_id && <code>{i.op_id}</code>} {i.message}</div>)}
    {rep.ok && rep.plan.length > 0 && <details><summary>Execution order</summary>{rep.plan.map(p => <div className="plan" key={p.id}>{p.order}. <code>{p.id}</code> {p.type} {p.start.toFixed(2)}–{p.end.toFixed(2)}s{p.alpha_source ? ` ← alpha from ${p.alpha_source}` : ""}</div>)}</details>}
  </div>);
}

function EffectsTab({ ctx }: { ctx: Ctx }) {
  const groups: Record<string, [string, any][]> = {};
  Object.entries(ctx.schema.operations).forEach(([k, s]) => (groups[s.category] ??= []).push([k, s]));
  return (<>
    <h3>Effects</h3><p className="muted">Adds an operation at the playhead (5 s). Edit it in the properties panel.</p>
    {!ctx.video && <div className="issue warning">Import a video first.</div>}
    {Object.entries(groups).map(([cat, list]) => (<div key={cat}><h4>{cat}</h4>
      {list.map(([k, s]) => (<button key={k} className="item" disabled={!s.supported || !ctx.video} title={s.supported ? s.description : s.unsupported_reason}
        onClick={() => { const [a, b] = span(ctx); let id = ""; ctx.edit(bp => { const r = m.addOp(bp, s, a, b); id = r.id; return r.bp; }); setTimeout(() => ctx.setSel(id), 0); }}>
        <Icon n={s.supported ? "plus" : "x"} size={14} /> {s.title}{!s.supported && <em> · needs model</em>}</button>))}</div>))}
  </>);
}

function BackgroundsTab({ ctx }: { ctx: Ctx }) {
  const bgs = ctx.bp.assets.filter(a => a.type === "image" || a.type === "video");
  const spec = ctx.schema.operations["background_replacement"];
  const hasKey = ctx.bp.operations.some(o => ctx.schema.operations[o.type]?.provides_alpha);
  const add = (assetId?: string) => addBackground(ctx, assetId);
  return (<>
    <h3>Backgrounds</h3><p className="muted">Replace the removed background with an image, video or colour.</p>
    {!hasKey && <div className="issue warning"><Icon n="warn" size={14} /> No chroma key / background removal operation yet — add one in Effects, or a replacement will be reported as having no alpha source.</div>}
    <div className="btn-row">
      <Btn icon="wand" kind={hasKey ? undefined : "primary"} label="AI remove background" disabled={!ctx.video} title="Person matting without a green screen (Robust Video Matting; model downloads once, ~15 MB)"
        onClick={() => { const d = ctx.duration; let id = ""; ctx.edit(bp => { const r = m.addOp(bp, ctx.schema.operations["background_removal"], 0, d); id = r.id; return m.setParam(r.bp, r.id, "mode", "ai"); }); setTimeout(() => ctx.setSel(id), 0); }} />
      <Btn icon="plus" label="Solid colour background" onClick={() => add()} disabled={!ctx.video} />
    </div>
    <Btn icon="image" kind="primary" label="Import background image / video…" onClick={() => importBackground(ctx)} />
    {bgs.length === 0 ? <Empty icon="image" title="No background pictures yet">Use the button above: the picture is added to the project and applied as the background.</Empty> :
      bgs.map(a => <button key={a.id} className="item" disabled={!ctx.video} onClick={() => add(a.id)}><Icon n={a.type === "image" ? "image" : "film"} size={14} /> {a.id} <em>{a.path}</em></button>)}
  </>);
}

function ColorTab({ ctx }: { ctx: Ctx }) {
  const spec = ctx.schema.operations["color_grading"];
  const presets = spec.parameters.preset.enum!.filter(p => p !== "none");
  const add = (preset?: string) => {
    const [a, b] = span(ctx); let id = "";
    ctx.edit(bp => { const r = m.addOp(bp, spec, a, b); id = r.id; return preset ? m.setParam(r.bp, r.id, "preset", preset) : r.bp; });
    setTimeout(() => ctx.setSel(id), 0);
  };
  return (<>
    <h3>Color grading</h3><p className="muted">Add a grade at the playhead, then fine-tune exposure, curves, LGG, LUT… in the properties panel.</p>
    <Btn icon="plus" label="Blank grade" onClick={() => add()} disabled={!ctx.video} />
    <h4>Cinematic presets</h4>
    {presets.map(p => <button key={p} className="item" disabled={!ctx.video} onClick={() => add(p)}><Icon n="color" size={14} /> {p.replace(/_/g, " ")}</button>)}
  </>);
}

function AssetsTab({ ctx }: { ctx: Ctx }) {
  const isMissing = (id: string) => ctx.issues.some(i => (i.code === "MISSING_ASSET" || i.code === "UNSAFE_PATH") && i.message.includes(`'${id}'`));
  const ext = (p: string) => p.split(".").pop()!.toLowerCase();
  const add = () => importAssetFile(ctx, "Import asset (image, video or .cube LUT)", "any");
  const locate = async (id: string, type: string) => {
    const p = await ctx.askPath(`Locate file for '${id}'`, "any"); if (!p) return;
    try { const r = await ctx.api.importAsset(p); ctx.edit(bp => m.addAsset(bp, { id, type, path: r.path })); } catch (e: any) { ctx.toast(e.message, "err"); }
  };
  return (<>
    <h3>Assets</h3>
    <Btn icon="plus" kind="primary" label="Import asset…" onClick={add} />
    {ctx.bp.assets.length === 0 && <Empty icon="layers" title="No assets">Backgrounds and LUTs referenced by the blueprint appear here.</Empty>}
    {ctx.bp.assets.map(a => <div className="row" key={a.id}><span><Icon n={a.type === "lut" ? "color" : a.type === "video" ? "film" : "image"} size={14} /> <b>{a.id}</b> <em>{a.type} · {a.path}</em></span>
      {isMissing(a.id) ? <Btn icon="warn" kind="danger" label="Relink" onClick={() => locate(a.id, a.type)} /> : <Btn icon="trash" title="Remove asset" onClick={() => ctx.edit(bp => ({ ...bp, assets: bp.assets.filter(x => x.id !== a.id) }))} />}</div>)}
  </>);
}
