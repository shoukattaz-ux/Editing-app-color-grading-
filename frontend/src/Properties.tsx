import React, { useState } from "react";
import type { Blueprint, Op, OpSpec, ParamSpec, Schema } from "./model";
import * as m from "./model";
import type { Issue, VideoInfo } from "./api";
import { Btn, Empty, Icon } from "./ui";

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

export function Properties({ bp, schema, video, sel, edit, t, issues, onReapply, onSelect }: {
  bp: Blueprint; schema: Schema; video: VideoInfo | null; sel: string | null; edit: Edit; t: number; issues: Issue[]; onReapply: () => void; onSelect: (id: string | null) => void;
}) {
  const op: Op | undefined = bp.operations.find(o => o.id === sel);
  const fps = video?.fps ?? 30, dur = video?.duration ?? 0;
  if (!op) return <OutputPanel bp={bp} edit={edit} video={video} />;
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

function OutputPanel({ bp, edit, video }: { bp: Blueprint; edit: Edit; video: VideoInfo | null }) {
  const o = bp.output ?? {};
  const set = (patch: Record<string, any>) => edit(b => m.setOutput(b, patch), "out");
  return (
    <div className="props">
      <header className="props-head"><div><h3>Output settings</h3><code>{bp.project?.name ?? "Untitled"}</code></div></header>
      <p className="muted">Select an operation in the timeline to edit it. These settings apply to the final render.</p>
      <label className="field"><span className="fl">resolution</span>
        <select value={String(o.resolution ?? "source")} onChange={e => set({ resolution: e.target.value })}>
          {["source", "1280x720", "1920x1080", "2560x1440", "3840x2160"].map(r => <option key={r}>{r}</option>)}
          {o.resolution && !["source", "1280x720", "1920x1080", "2560x1440", "3840x2160"].includes(o.resolution) && <option>{o.resolution}</option>}
        </select></label>
      <label className="field"><span className="fl">frame rate</span>
        <select value={String(o.fps ?? "source")} onChange={e => set({ fps: e.target.value === "source" ? "source" : parseFloat(e.target.value) })}>
          {["source", "24", "25", "30", "50", "60"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label className="field"><span className="fl">container</span><select value={o.format ?? "mp4"} onChange={e => set({ format: e.target.value })}><option>mp4</option><option>mov</option></select></label>
      <label className="field"><span className="fl">codec</span><select value={o.codec ?? "h264"} onChange={e => set({ codec: e.target.value })}><option value="h264">H.264</option><option value="h265">H.265 / HEVC</option></select></label>
      <label className="field"><span className="fl">quality (CRF) <em>lower = better</em></span><div className="slider-row"><input type="range" min={14} max={32} value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) })} /><input type="number" value={o.crf ?? 20} onChange={e => set({ crf: parseInt(e.target.value) || 20 })} /></div></label>
      <label className="field"><span className="fl">audio bitrate</span><select value={o.audio_bitrate ?? "192k"} onChange={e => set({ audio_bitrate: e.target.value })}>{["128k", "192k", "256k", "320k"].map(r => <option key={r}>{r}</option>)}</select></label>
      <label className="field"><span className="fl">encode speed</span><select value={o.preset ?? "medium"} onChange={e => set({ preset: e.target.value })}>{["ultrafast", "veryfast", "fast", "medium", "slow"].map(r => <option key={r}>{r}</option>)}</select></label>
      {video && <p className="muted">Source: {video.width}×{video.height} · {video.fps} fps · {video.duration.toFixed(2)}s · {video.codec}</p>}
      {!video && <Empty icon="film" title="No source video">Import a video from the Media tab.</Empty>}
    </div>
  );
}
