import React, { useState } from "react";
import type { Blueprint, Op, OpSpec, ParamSpec, Schema } from "./model";
import * as m from "./model";
import type { Issue, VideoInfo } from "./api";
import { Btn, Empty, Icon, resolutionOptions } from "./ui";

type Edit = (fn: (b: Blueprint) => Blueprint, key?: string) => void;

function ParamField({ name, spec, value, onChange, bp }: { name: string; spec: ParamSpec; value: any; onChange: (v: any) => void; bp: Blueprint }) {
  const label = name.replace(/_/g, " ");
  const v = value ?? spec.default;
  const [txt, setTxt] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  let input: React.ReactNode;
  if (spec.type === "number" || spec.type === "integer") {
    const min = spec.minimum ?? 0, max = spec.maximum ?? 1, step = spec.type === "integer" ? 1 : (max - min) / 200;
    input = (<div className="slider-row">
      <input type="range" min={min} max={max} step={step} value={Number(v ?? 0)} onChange={e => onChange(spec.type === "integer" ? parseInt(e.target.value) : parseFloat(e.target.value))} aria-label={label} />
      <input type="number" min={min} max={max} step={step} value={Number(v ?? 0)} onChange={e => { const n = parseFloat(e.target.value); if (!isNaN(n)) onChange(spec.type === "integer" ? Math.round(n) : n); }} />
    </div>);
  } else if (spec.type === "boolean") input = <input type="checkbox" checked={!!v} onChange={e => onChange(e.target.checked)} aria-label={label} />;
  else if (spec.type === "enum") input = <select value={v} onChange={e => onChange(e.target.value)}>{spec.enum!.map(o => <option key={o}>{o}</option>)}</select>;
  else if (spec.type === "color") {
    const hex = typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v) ? v : "#00ff00";
    input = <div className="slider-row"><input type="color" value={hex} onChange={e => onChange(e.target.value)} aria-label={label} /><input type="text" value={v ?? ""} onChange={e => onChange(e.target.value)} /></div>;
  } else if (spec.type === "asset") {
    const opts = bp.assets.filter(a => spec.asset_types?.includes(a.type));
    input = <select value={value ?? ""} onChange={e => onChange(e.target.value || undefined)}><option value="">— none —</option>{opts.map(a => <option key={a.id} value={a.id}>{a.id} ({a.type})</option>)}</select>;
  } else if (spec.type === "points") {
    const shown = txt ?? (value ? JSON.stringify(value) : "");
    input = <input className={bad ? "bad" : ""} type="text" value={shown} placeholder={name.startsWith("curve") ? "[[0,0],[0.5,0.6],[1,1]]" : "[0,0,0]"}
      onChange={e => { setTxt(e.target.value); if (e.target.value.trim() === "") { setBad(false); onChange(undefined); return; } try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); } }}
      onBlur={() => { if (!bad) setTxt(null); }} />;
  } else input = <input type="text" value={v ?? ""} onChange={e => onChange(e.target.value)} />;
  return (<label className="field" title={spec.description}>
    <span className="fl">{label}{spec.unit ? <em> {spec.unit}</em> : null}{value !== undefined && spec.default !== undefined && value !== spec.default ? <i className="dot" title="changed from default" /> : null}</span>
    {input}
  </label>);
}

function ClipPanel({ bp, video, layout, id, edit, t, onCut, onDeleteClip, onSelectClip }: {
  bp: Blueprint; video: VideoInfo; layout: m.Layout; id: string; edit: Edit; t: number; onCut: () => void; onDeleteClip: () => void; onSelectClip: (id: string | null) => void;
}) {
  const c = layout.clips.find(x => x.id === id), fps = layout.fps;
  if (!c) return null;
  const i = layout.clips.indexOf(c), n = layout.clips.length, step = 1 / fps;
  const inside = Math.round(t * fps) > Math.round(c.tStart * fps) && Math.round(t * fps) < Math.round(c.tEnd * fps);
  return (
    <div className="props">
      <header className="props-head"><div><h3>Clip {i + 1} of {n}</h3><code>{c.id}</code></div></header>
      <p className="muted">A piece of your original footage. Trim it, cut it, reorder it or delete it — the original file is never changed. Effects are timed on this cut timeline.</p>
      <label className="field"><span className="fl">name</span><input type="text" value={c.implicit ? "" : (c.label ?? "")} placeholder="optional label"
        onChange={e => edit(b => m.labelClip(b, id, e.target.value, video.duration, fps), "label:" + id)} /></label>
      <div className="btn-row">
        <Btn icon="scissors" label="Cut at playhead" disabled={!inside} onClick={onCut} title="Split this clip at the playhead (C)" />
        <Btn icon="copy" label="Duplicate" onClick={() => { const r = m.duplicateClip(bp, id, video.duration, fps); if (r.id) { edit(() => r.bp); onSelectClip(r.id); } }} title="Repeat this clip right after itself" />
        <Btn label="◀ Earlier" disabled={i === 0} onClick={() => edit(b => m.moveClip(b, id, -1, video.duration, fps))} title="Move this clip before the previous one" />
        <Btn label="Later ▶" disabled={i === n - 1} onClick={() => edit(b => m.moveClip(b, id, 1, video.duration, fps))} title="Move this clip after the next one" />
        <Btn icon="trash" label="Delete" kind="danger" disabled={n < 2} onClick={onDeleteClip} title="Remove this clip; later footage and effects move earlier" />
      </div>
      <section>
        <h4>Source footage</h4>
        <div className="grid2">
          <label className="field"><span className="fl">in <em>s</em></span><input type="number" step={step} min={0} max={video.duration} value={+c.srcStart.toFixed(4)}
            onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) edit(b => m.trimClip(b, id, "start", v, video.duration, fps), `clipin:${id}`); }} /></label>
          <label className="field"><span className="fl">out <em>s</em></span><input type="number" step={step} min={0} max={video.duration} value={+c.srcEnd.toFixed(4)}
            onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) edit(b => m.trimClip(b, id, "end", v, video.duration, fps), `clipout:${id}`); }} /></label>
        </div>
        <p className="muted">Source {m.formatTime(c.srcStart, fps)} → {m.formatTime(c.srcEnd, fps)} of {video.duration.toFixed(2)} s</p>
      </section>
      <section>
        <h4>On the timeline</h4>
        <p className="muted">{m.formatTime(c.tStart, fps)} → {m.formatTime(c.tEnd, fps)} · {(c.tEnd - c.tStart).toFixed(2)} s</p>
        <p className="muted">Trimming or deleting moves later footage <b>and</b> effects automatically. Reordering clips keeps effects at their timeline times.</p>
      </section>
    </div>);
}

export function Properties({ bp, schema, video, layout, sel, selClip, edit, t, issues, onReapply, onSelect, onSelectClip, onCut, onDeleteClip }: {
  bp: Blueprint; schema: Schema; video: VideoInfo | null; layout: m.Layout; sel: string | null; selClip: string | null; edit: Edit; t: number; issues: Issue[];
  onReapply: () => void; onSelect: (id: string | null) => void; onSelectClip: (id: string | null) => void; onCut: () => void; onDeleteClip: () => void;
}) {
  const op: Op | undefined = bp.operations.find(o => o.id === sel);
  const fps = layout.fps, dur = layout.duration;
  if (!op && selClip && video) return <ClipPanel bp={bp} video={video} layout={layout} id={selClip} edit={edit} t={t} onCut={onCut} onDeleteClip={onDeleteClip} onSelectClip={onSelectClip} />;
  if (!op) return <OutputPanel bp={bp} edit={edit} video={video} layout={layout} />;
  const spec: OpSpec | undefined = schema.operations[op.type];
  const mine = issues.filter(i => i.op_id === op.id);
  const end = op.end ?? dur;
  return (
    <div className="props">
      <header className="props-head">
        <div><h3>{spec?.title ?? op.type}</h3><code>{op.id}</code></div>
        <label className="switch" title="Enable / disable this operation"><input type="checkbox" checked={op.enabled !== false} onChange={() => edit(b => m.toggleOp(b, op.id))} /><span>{op.enabled === false ? "Disabled" : "Enabled"}</span></label>
      </header>
      <p className="muted">{spec?.description}</p>
      {mine.map((i, k) => <div key={k} className={`issue ${i.level}`}><Icon n="warn" size={14} /> {i.message}</div>)}
      <div className="btn-row">
        <Btn icon="copy" label="Duplicate" onClick={() => edit(b => m.duplicateOp(b, op.id).bp)} />
        <Btn icon="split" label="Split at playhead" onClick={() => { const r = m.splitOp(bp, op.id, t, fps); if (r.ids) { edit(() => r.bp); onSelect(r.ids[1]); } }} title="Split this operation at the playhead into two operations" />
        <Btn icon="reset" label="Reset" onClick={() => edit(b => m.resetParams(b, op.id))} title="Reset all parameters to defaults" />
        <Btn icon="bolt" label="Reapply" onClick={onReapply} title="Re-render the preview with this operation" />
        <Btn icon="trash" label="Delete" kind="danger" onClick={() => { edit(b => m.deleteOp(b, op.id)); onSelect(null); }} />
      </div>
      <section>
        <h4>Timing</h4>
        <div className="grid2">
          <label className="field"><span className="fl">start <em>s</em></span><input type="number" step={1 / fps} min={0} max={dur} value={+op.start.toFixed(4)} onChange={e => { const s = parseFloat(e.target.value); if (!isNaN(s)) edit(b => m.setOpTime(b, op.id, Math.min(s, end - 1 / fps), end), "time:" + op.id); }} /></label>
          <label className="field"><span className="fl">end <em>s</em></span><input type="number" step={1 / fps} min={0} max={dur} value={+end.toFixed(4)} onChange={e => { const s = parseFloat(e.target.value); if (!isNaN(s)) edit(b => m.setOpTime(b, op.id, op.start, Math.max(s, op.start + 1 / fps)), "time:" + op.id); }} /></label>
        </div>
      </section>
      <section>
        <h4>Parameters</h4>
        {spec ? Object.entries(spec.parameters).map(([k, ps]) => (
          <ParamField key={k} name={k} spec={ps} bp={bp} value={op.parameters?.[k]} onChange={v => edit(b => m.setParam(b, op.id, k, v), `p:${op.id}:${k}`)} />))
          : <p className="muted">Unknown operation type — see Blueprint errors.</p>}
      </section>
      <section>
        <h4>Depends on</h4>
        {bp.operations.filter(o => o.id !== op.id).length === 0 && <p className="muted">No other operations.</p>}
        {bp.operations.filter(o => o.id !== op.id).map(o => (
          <label key={o.id} className="check"><input type="checkbox" checked={!!op.depends_on?.includes(o.id)}
            onChange={e => edit(b => ({ ...b, operations: b.operations.map(x => x.id === op.id ? { ...x, depends_on: e.target.checked ? [...(x.depends_on ?? []), o.id] : (x.depends_on ?? []).filter(d => d !== o.id) } : x) }))} /> {o.id} <em>{o.type}</em></label>))}
      </section>
    </div>
  );
}

function OutputPanel({ bp, edit, video, layout }: { bp: Blueprint; edit: Edit; video: VideoInfo | null; layout: m.Layout }) {
  const o = bp.output ?? {};
  const set = (patch: Record<string, any>) => edit(b => m.setOutput(b, patch), "out");
  return (
    <div className="props">
      <header className="props-head"><div><h3>Output settings</h3><code>{bp.project?.name ?? "Untitled"}</code></div></header>
      <p className="muted">Select an operation in the timeline to edit it. These settings apply to the final render.</p>
      <label className="field"><span className="fl">resolution</span>
        <select value={String(o.resolution ?? "source")} onChange={e => set({ resolution: e.target.value })}>
          {resolutionOptions(video, o.resolution).map(r => <option key={r} value={r}>{r === "source" ? "source" : video && r === `${video.width * 2}x${video.height * 2}` ? `${r} (2× source)` : r}</option>)}
        </select></label>
      <label className="field"><span className="fl">frame rate</span>
        <select value={String(o.fps ?? "source")} onChange={e => set({ fps: e.target.value === "source" ? "source" : parseFloat(e.target.value) })}>
          {["source", "24", "25", "30", "50", "60"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label className="field"><span className="fl">container</span><select value={o.format ?? "mp4"} onChange={e => set({ format: e.target.value })}><option disabled={o.codec === "prores"}>mp4</option><option>mov</option></select></label>
          {o.codec === "prores" && <label className="field"><span className="fl">ProRes profile</span><select value={o.prores_profile ?? "hq"} onChange={e => set({ prores_profile: e.target.value })}><option value="proxy">Proxy</option><option value="lt">LT</option><option value="standard">422</option><option value="hq">422 HQ</option></select></label>}
      <label className="field"><span className="fl">codec</span><select value={o.codec ?? "h264"} onChange={e => set(e.target.value === "prores" ? { codec: "prores", format: "mov" } : { codec: e.target.value })}><option value="h264">H.264</option><option value="h265">H.265 / HEVC</option><option value="prores">ProRes 422 10-bit (.mov, for editing)</option></select></label>
      <label className="field"><span className="fl">quality (CRF) <em>lower = better</em></span><div className="slider-row"><input type="range" min={14} max={32} value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) })} /><input type="number" value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) || 20 })} /></div></label>
      <label className="field"><span className="fl">audio bitrate</span><select value={o.audio_bitrate ?? "192k"} onChange={e => set({ audio_bitrate: e.target.value })}>{["128k", "192k", "256k", "320k"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label className="field"><span className="fl">encode speed</span><select value={o.preset ?? "medium"} onChange={e => set({ preset: e.target.value })}>{["ultrafast", "veryfast", "fast", "medium", "slow"].map(r => <option key={r}>{r}</option>)}</select></label>
      {video && <p className="muted">Source: {video.width}×{video.height} · {video.fps} fps · {video.duration.toFixed(2)}s · {video.codec}{layout.cut ? ` · timeline after cuts: ${layout.duration.toFixed(2)}s (${layout.clips.length} clips)` : ""}</p>}
      {!video && <Empty icon="film" title="No source video">Import a video from the Media tab.</Empty>}
    </div>
  );
}
