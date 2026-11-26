import React, { useState, useEffect } from "react";
import type { Blueprint, Op, OpSpec, ParamSpec, Schema } from "./model";
import * as m from "./model";
import type { Api, CameraNote, EnhanceNote, Issue, ModelStatus, VideoInfo } from "./api";
import { Btn, Empty, Icon, resolutionOptions } from "./ui";
import { KeyframeGraph } from "./Keyframes";
import { ColorWheels } from "./ColorWheels";

type Edit = (fn: (b: Blueprint) => Blueprint, key?: string) => void;

/** "gain_db" + unit "dB" -> "Gain" (the unit is shown once, beside it); "fade_in_s" -> "Fade in"; "light_pattern_asset_id" -> "Light pattern picture / video" */
export function humanLabel(name: string, unit?: string | null): string {
  let n = name.replace(/_asset_id$/, " picture / video").replace(/_id$/, "");
  const u = (unit ?? "").toLowerCase();
  const suffix = { db: "db", s: "s", px: "px", deg: "deg", pct: "pct", ms: "ms" } as Record<string, string>;
  for (const k of Object.keys(suffix)) if (n.endsWith("_" + k) && (!u || u.startsWith(k) || k === "s" && u.startsWith("s"))) { n = n.slice(0, -k.length - 1); break; }
  n = n.replace(/_/g, " ").trim();
  return n.charAt(0).toUpperCase() + n.slice(1);
}

/** the unit a parameter name ends with (fade_in_s -> "s", gain_db -> "dB"), shown when the schema gives none */
export function suffixUnit(name: string): string {
  const m = /_(db|s|px|deg|ms)$/.exec(name); return m ? ({ db: "dB", s: "s", px: "px", deg: "°", ms: "ms" } as Record<string, string>)[m[1]] : "";
}

function ParamControl({ name, spec, value, onChange, bp, extra }: { name: string; spec: ParamSpec; value: any; onChange: (v: any) => void; bp: Blueprint; extra?: React.ReactNode }) {
  const label = humanLabel(name, spec.unit);
  const aria = name.replace(/_/g, " ");          // the parameter's own name (distinct from the friendly cards above, which use plain words)
  const v = value ?? spec.default;
  const [txt, setTxt] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  let input: React.ReactNode;
  if (spec.type === "number" || spec.type === "integer") {
    const min = spec.minimum ?? 0, max = spec.maximum ?? 1, step = spec.type === "integer" ? 1 : (max - min) / 200;
    input = (<div className="slider-row">
      <input type="range" min={min} max={max} step={step} value={Number(v ?? 0)} onChange={e => onChange(spec.type === "integer" ? parseInt(e.target.value) : parseFloat(e.target.value))} aria-label={aria} />
      <input type="number" min={min} max={max} step={step} value={+Number(v ?? 0).toFixed(spec.type === "integer" ? 0 : 3)} onChange={e => { const n = parseFloat(e.target.value); if (!isNaN(n)) onChange(spec.type === "integer" ? Math.round(n) : n); }} />
    </div>);
  } else if (spec.type === "boolean") input = <input type="checkbox" checked={!!v} onChange={e => onChange(e.target.checked)} aria-label={aria} />;
  else if (spec.type === "enum") input = <select value={v} onChange={e => onChange(e.target.value)}>{spec.enum!.map(o => <option key={o}>{o}</option>)}</select>;
  else if (spec.type === "color") {
    const hex = typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v) ? v : "#00ff00";
    input = <div className="slider-row"><input type="color" value={hex} onChange={e => onChange(e.target.value)} aria-label={aria} /><input type="text" value={v ?? ""} onChange={e => onChange(e.target.value)} /></div>;
  } else if (spec.type === "asset") {
    const opts = bp.assets.filter(a => spec.asset_types?.includes(a.type));
    input = <select value={value ?? ""} onChange={e => onChange(e.target.value || undefined)}><option value="">— none —</option>{opts.map(a => <option key={a.id} value={a.id}>{a.id} ({a.type})</option>)}</select>;
  } else if (spec.type === "points") {
    const shown = txt ?? (value ? JSON.stringify(value) : "");
    input = <input className={bad ? "bad" : ""} type="text" value={shown} placeholder={name.startsWith("curve") ? "[[0,0],[0.5,0.6],[1,1]]" : "[0,0,0]"}
      onChange={e => { setTxt(e.target.value); if (e.target.value.trim() === "") { setBad(false); onChange(undefined); return; } try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); } }}
      onBlur={() => { if (!bad) setTxt(null); }} />;
  } else input = <input type="text" value={v ?? ""} onChange={e => onChange(e.target.value)} />;
  return (<div className="pc" title={spec.description}>
    <div className="pc-head"><label className="fl">{label}{(spec.unit || suffixUnit(name)) ? <em className="unit"> ({spec.unit || suffixUnit(name)})</em> : null}{value !== undefined && spec.default !== undefined && value !== spec.default ? <i className="dot" title="changed from default" /> : null}</label>{extra}</div>
    {input}
  </div>);
}

/** One parameter row: the value control (shows the interpolated value at the playhead when animated) plus stopwatch / keyframe controls. */
function ParamField({ name, spec, op, bp, t, fps, opLen, edit, setT }: {
  name: string; spec: ParamSpec; op: Op; bp: Blueprint; t: number; fps: number; opLen: number; edit: Edit; setT: (t: number) => void;
}) {
  const kfs = op.keyframes?.[name];
  const animated = !!kfs?.length;
  const canAnimate = !!spec.animatable;
  const tRel = Math.min(Math.max(t - op.start, 0), opLen);
  const shown = animated ? m.valueAt(kfs!, t - op.start) : op.parameters?.[name];
  const [selKf, setSelKf] = useState(0);
  const isInt = spec.type === "integer";
  const put = (v: number) => edit(b => m.setKeyframe(b, op.id, name, tRel, isInt ? Math.round(v) : v, fps, spec.minimum, spec.maximum), `kf:${op.id}:${name}`);
  const onKey = animated && kfs!.some(k => Math.abs(k.time - (t - op.start)) < 0.5 / fps);
  const prev = animated ? [...kfs!].reverse().find(k => k.time < t - op.start - 0.5 / fps) : undefined;
  const next = animated ? kfs!.find(k => k.time > t - op.start + 0.5 / fps) : undefined;
  const toggleAnim = () => animated
    ? edit(b => m.disableAnimation(b, op.id, name, shown))
    : edit(b => m.enableAnimation(b, op.id, name, tRel, op.parameters?.[name] ?? spec.default ?? 0, fps, spec.minimum, spec.maximum));
  const toggleKey = () => { if (!animated) return toggleAnim(); const i = kfs!.findIndex(k => Math.abs(k.time - (t - op.start)) < 0.5 / fps); i >= 0 ? edit(b => m.removeKeyframe(b, op.id, name, i)) : put(shown); };
  return (
    <div className={`field pf ${animated ? "animated" : ""}`}>
      <ParamControl name={name} spec={spec} value={shown} bp={bp} onChange={v => (animated ? put(v) : edit(b => m.setParam(b, op.id, name, v), `p:${op.id}:${name}`))}
        extra={canAnimate ? (
          <span className="kf-ctl" role="group" aria-label={`Keyframes for ${name}`}>
            <button className="kfb" disabled={!prev} title="Previous keyframe" aria-label="Previous keyframe" onClick={() => prev && setT(op.start + prev.time)}><Icon n="chevL" size={12} /></button>
            <button className={`kfb dia ${onKey ? "on" : ""}`} title={!animated ? "Add a keyframe (starts animating)" : onKey ? "Remove the keyframe at the playhead" : "Add a keyframe at the playhead"} aria-label="Toggle keyframe at playhead" aria-pressed={onKey} onClick={toggleKey}><Icon n="diamond" size={12} /></button>
            <button className="kfb" disabled={!next} title="Next keyframe" aria-label="Next keyframe" onClick={() => next && setT(op.start + next.time)}><Icon n="chevR" size={12} /></button>
            <button className={`kfb clock ${animated ? "on" : ""}`} title={animated ? "Stop animating (keeps the value at the playhead)" : "Animate over time"} aria-label="Animate parameter" aria-pressed={animated} onClick={toggleAnim}><Icon n="clock" size={12} /></button>
          </span>) : null} />
      {animated && (
        <KeyframeGraph kfs={kfs!} length={opLen} min={spec.minimum} max={spec.maximum} integer={isInt} unit={spec.unit} tRel={t - op.start} fps={fps}
          selected={Math.min(selKf, kfs!.length - 1)} onSelect={setSelKf}
          onMove={(i, tt, v) => edit(b => m.moveKeyframe(b, op.id, name, i, tt, v, fps, spec.minimum, spec.maximum), `kfdrag:${op.id}:${name}:${i}`)}
          onAdd={(tt, v) => { edit(b => m.setKeyframe(b, op.id, name, tt, v, fps, spec.minimum, spec.maximum)); setSelKf(kfs!.filter(k => k.time < tt).length); }}
          onRemove={i => { edit(b => m.removeKeyframe(b, op.id, name, i)); setSelKf(Math.max(0, i - 1)); }}
          onEasing={(i, e) => edit(b => m.setKeyframeEasing(b, op.id, name, i, e))} />)}
    </div>);
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
          <label className="field"><span className="fl">in <em className="unit">(s)</em></span><input type="number" step={step} min={0} max={video.duration} value={+c.srcStart.toFixed(4)}
            onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) edit(b => m.trimClip(b, id, "start", v, video.duration, fps), `clipin:${id}`); }} /></label>
          <label className="field"><span className="fl">out <em className="unit">(s)</em></span><input type="number" step={step} min={0} max={video.duration} value={+c.srcEnd.toFixed(4)}
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

/** garbage / keep mask: what it does, what shape, quick placements; the shape itself is drawn on the picture */
function MaskTools({ op, edit }: { op: Op; edit: Edit }) {
  const pr = op.parameters ?? {}, mode = pr.mode ?? "garbage", shape = pr.shape ?? "rect";
  const patch = (o: Record<string, any>) => edit(b => Object.entries(o).reduce((acc, [k, v]) => m.setParam(acc, op.id, k, v), b), `mask:${op.id}`);
  const strips: [string, Record<string, number>][] = [["Left strip", { x: 0, y: 0, width: 0.15, height: 1 }], ["Right strip", { x: 0.85, y: 0, width: 0.15, height: 1 }], ["Top strip", { x: 0, y: 0, width: 1, height: 0.15 }], ["Bottom strip", { x: 0, y: 0.85, width: 1, height: 0.15 }], ["Centre", { x: 0.3, y: 0.2, width: 0.4, height: 0.6 }]];
  return (
    <section className="masktools">
      <h4>Mask</h4>
      <div className="seg2" role="group" aria-label="Mask type">
        <button className={mode === "garbage" ? "on bad" : ""} onClick={() => patch({ mode: "garbage" })} title="This area is always removed">Garbage — remove</button>
        <button className={mode === "keep" ? "on good" : ""} onClick={() => patch({ mode: "keep" })} title="This area is always kept">Keep — keep</button>
      </div>
      <div className="seg2" role="group" aria-label="Shape">
        {(["rect", "ellipse", "polygon"] as const).map(s => <button key={s} className={shape === s ? "on" : ""}
          onClick={() => patch(s === "polygon" && !(pr.polygon?.length >= 3) ? { shape: s, polygon: [[0.3, 0.2], [0.7, 0.2], [0.5, 0.75]] } : { shape: s })}>{s === "rect" ? "Rectangle" : s === "ellipse" ? "Ellipse" : "Polygon"}</button>)}
      </div>
      {shape !== "polygon" && <div className="chips">{strips.map(([n, v]) => <button key={n} className="chip" onClick={() => patch(v)}>{n}</button>)}</div>}
      {shape === "polygon" && <div className="chips"><button className="chip" onClick={() => patch({ polygon: [[0.3, 0.2], [0.7, 0.2], [0.5, 0.75]] })}>Reset to a triangle</button></div>}
      <p className="muted small">Draw it on the picture (set the preview to <b>Edited</b>): drag the shape to move it, drag the white squares to resize{shape === "polygon" ? "; click the picture to add a corner, double-click a corner to delete it" : ""}. Check it with <b>View → Matte (alpha)</b>. {mode === "garbage" ? "Everything inside is removed (lights, stands, the screen edge)." : "Everything inside is kept (a face or shirt the key made see-through)."}</p>
    </section>);
}

/** Floor: reflection and contact shadow, so the subject stands ON the floor of the new scene */
function FloorFx({ op, edit, note }: { op: Op; edit: Edit; note?: { y: number; cut: boolean } }) {
  const pr = op.parameters ?? {};
  const num = (k: string, d: number) => (typeof pr[k] === "number" ? (pr[k] as number) : d);
  const set = (k: string, v: number | undefined) => edit(b => m.setParam(b, op.id, k, v), `p:${op.id}:${k}`);
  const manual = typeof pr.floor_y === "number";
  const on = num("reflection", 0) > 0 || num("contact_shadow", 0) > 0;
  const slider = (k: string, label: string, d: number, max = 100, unit = "%") => (
    <label className="field"><span className="fl">{label} <em>{Math.round(num(k, d) * 100)}{unit}</em></span>
      <input type="range" min={0} max={max} value={Math.round(num(k, d) * 100)} onChange={e => set(k, +e.target.value / 100)} aria-label={label} /></label>);
  return (
    <section className="floorfx">
      <div className="master-head"><h4>Floor: reflection and contact shadow</h4><span className="muted small">{on ? "on" : "off"}</span></div>
      <p className="muted small">If the person stands on a floor, add a soft <b>reflection</b> and a tight <b>contact shadow</b> under the feet. Both are made from the subject itself, so they follow every step. The floor line is found automatically (the lowest point of the subject) or set by hand.</p>
      {slider("reflection", "Reflection", 0)}
      {num("reflection", 0) > 0 && <>{slider("reflection_length", "Reflection length", 0.25, 80)}
        <label className="field"><span className="fl">Reflection blur <em>{num("reflection_blur", 6).toFixed(0)} px</em></span><input type="range" min={0} max={40} value={num("reflection_blur", 6)} onChange={e => set("reflection_blur", +e.target.value)} aria-label="Reflection blur" /></label></>}
      {slider("contact_shadow", "Contact shadow", 0)}
      {num("contact_shadow", 0) > 0 && <label className="field"><span className="fl">Contact shadow softness <em>{num("contact_blur", 10).toFixed(0)} px</em></span><input type="range" min={1} max={60} value={num("contact_blur", 10)} onChange={e => set("contact_blur", +e.target.value)} aria-label="Contact shadow softness" /></label>}
      {on && <>
        <label className="check"><input type="checkbox" checked={manual} onChange={e => set("floor_y", e.target.checked ? Math.round((note?.y ?? 0.85) * 100) / 100 : undefined)} /> Set the floor line by hand</label>
        {manual && <label className="field"><span className="fl">Floor line <em>{Math.round(num("floor_y", 0.85) * 100)}% from the top</em></span><input type="range" min={0} max={100} value={Math.round(num("floor_y", 0.85) * 100)} onChange={e => set("floor_y", +e.target.value / 100)} aria-label="Floor line" /></label>}
        {!manual && note && !note.cut && <div className="issue ok"><Icon n="check" size={14} /> Floor found at {Math.round(note.y * 100)}% of the frame height (the lowest point of the subject).</div>}
        {!manual && note && note.cut && <div className="issue warning"><Icon n="warn" size={14} /> The subject touches the bottom of the frame, so no floor is in view and both effects are skipped. Scale the subject down / move it up, or set the floor line by hand.</div>}
        {!manual && !note && <p className="muted small">The floor is measured on the next preview frame.</p>}
      </>}
    </section>);
}

/** Hair detail: the AI model refines a band around the edge; the screen key stays in charge elsewhere */
function HairDetail({ op, edit, note }: { op: Op; edit: Edit; note?: { used: number; skipped: number } }) {
  const v = (op.parameters?.hair_detail as number | undefined) ?? 0;
  const w = (op.parameters?.hair_width as number | undefined) ?? 16;
  const set = (k: string, x: number) => edit(b => m.setParam(b, op.id, k, x), `p:${op.id}:${k}`);
  return (
    <section className="hairdetail">
      <div className="master-head"><h4>Hair detail</h4><span className="muted small">{v > 0 ? `${Math.round(v * 100)}%` : "off"}</span></div>
      <p className="muted small">Fine hair is where keys fail: strands lost against a shadowed screen, green-tinted hair that turns see-through. Hair detail runs the AI matting model as well and lets it <b>restore what the key lost</b>, only in a band around the subject's edge. It can only add: your key's crisp strands are never softened, the AI's wider halo over pure screen never comes in, and a frame where the AI does not find the same person uses the plain key. On clean footage the result is almost identical, so it is insurance for difficult hair. Downloads a 15 MB model on first use and is slower (the network runs on every frame; a graphics card helps).</p>
      <div className="master-row"><input type="range" min={0} max={100} value={Math.round(v * 100)} onChange={e => set("hair_detail", +e.target.value / 100)} onDoubleClick={() => set("hair_detail", 0)} aria-label="Hair detail strength" />
        <Btn label={v > 0 ? "Off" : "Turn on"} kind={v > 0 ? undefined : "primary"} onClick={() => set("hair_detail", v > 0 ? 0 : 0.7)} /></div>
      {v > 0 && <label className="field"><span className="fl">band width <em>px at 1080p</em></span><input type="range" min={2} max={60} value={w} onChange={e => set("hair_width", +e.target.value)} aria-label="Hair band width" /></label>}
      {v > 0 && note && note.used > 0 && <div className="issue ok"><Icon n="check" size={14} /> The AI refined the hair on {note.used} checked frame(s){note.skipped ? `; ${note.skipped} used the plain key (the AI did not find the same person)` : ""}.</div>}
      {v > 0 && note && note.used === 0 && <div className="issue warning"><Icon n="warn" size={14} /> On the checked frames the AI did not find the same person as the key, so the plain key was used. Hair detail needs a clearly visible person.</div>}
      {v > 0 && !note && <p className="muted small">It is checked on the next preview frame.</p>}
    </section>);
}

/** Light pattern: moving light (caustics / solid / particles) with colour, spread, bend, and where it may land */
function LightPattern({ op, edit }: { op: Op; edit: Edit }) {
  const pr = op.parameters ?? {};
  const num = (k: string, d: number) => (typeof pr[k] === "number" ? (pr[k] as number) : d);
  const str = (k: string, d: string) => (typeof pr[k] === "string" ? (pr[k] as string) : d);
  const set = (k: string, v: number | string | undefined) => edit(b => m.setParam(b, op.id, k, v), `p:${op.id}:${k}`);
  const preset = (o: Record<string, number | string>) => edit(b => Object.entries(o).reduce((acc, [k, v]) => m.setParam(acc, op.id, k, v as any), b), `p:${op.id}:lightpreset`);
  const on = num("light_pattern", 0) > 0;
  const style = str("light_pattern_style", "ripples");
  const where = str("light_pattern_on", "subject");
  const sl = (k: string, label: string, d: number, min: number, max: number, step: number, fmt: (v: number) => string) => (
    <label className="field"><span className="fl">{label} <em>{fmt(num(k, d))}</em></span>
      <input type="range" min={min} max={max} step={step} value={num(k, d)} onChange={e => set(k, +e.target.value)} aria-label={label} /></label>);
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const hasC2 = typeof pr.light_pattern_color2 === "string" && !!pr.light_pattern_color2;
  return (
    <section className="lightpattern">
      <div className="master-head"><h4>Light pattern</h4><span className="muted small">{on ? pct(num("light_pattern", 0)) : "off"}</span></div>
      <p className="muted small">Moving light over your scene: water ripples, a solid glow or floating particles. Pick up to <b>two colours</b> (the light, and an optional tint for the shadowed parts). With <b>Apply to: Subject only</b> the light is cut by the subject's own matte, so it can never appear on the background, even if it spreads. Needs a background replacement.</p>
      <div className="btn-row">
        <Btn label={on ? "Off" : "Underwater ripples"} kind={on ? undefined : "primary"} onClick={() => (on ? set("light_pattern", 0) : preset({ light_pattern: 0.6, light_pattern_scale: 1.3, light_pattern_speed: 1, light_pattern_falloff: 0.55, light_pattern_style: "ripples", light_pattern_on: "subject" }))} title="Sun-through-water light moving over the subject" />
      </div>
      {sl("light_pattern", "Strength", 0, 0, 1, 0.01, pct)}
      {on && <>
        <div className="btn-row" role="group" aria-label="Light style">
          {([["ripples", "Ripples"], ["solid", "Solid"], ["particles", "Particles"]] as const).map(([k, l]) =>
            <Btn key={k} label={l} kind={style === k ? "primary" : undefined} onClick={() => set("light_pattern_style", k)} title={k === "ripples" ? "Soft moving water-light web" : k === "solid" ? "Hard-edged light shapes" : "Floating specks of light"} />)}
        </div>
        <div className="btn-row" role="group" aria-label="Apply to">
          {([["subject", "Subject only (recommended)"], ["background", "Background only"], ["both", "Both"]] as const).map(([k, l]) =>
            <Btn key={k} label={l} kind={where === k ? "primary" : undefined} onClick={() => set("light_pattern_on", k)} title="Where the light may land. Subject only never touches the background." />)}
        </div>
        <label className="field"><span className="fl">Light colour</span>
          <span className="slider-row"><input type="color" value={str("light_pattern_color", "#ffffff")} onChange={e => set("light_pattern_color", e.target.value)} aria-label="Light colour" /><input type="text" value={str("light_pattern_color", "#ffffff")} onChange={e => set("light_pattern_color", e.target.value)} /></span></label>
        <label className="field"><span className="fl">Second colour (shadow tint) <em>{hasC2 ? "on" : "off"}</em></span>
          <span className="slider-row"><input type="color" value={str("light_pattern_color2", "#1a3cff")} onChange={e => set("light_pattern_color2", e.target.value)} aria-label="Second colour" />
            {hasC2 ? <Btn label="Remove" onClick={() => set("light_pattern_color2", undefined)} /> : <span className="muted small">pick a colour to turn on</span>}</span></label>
        {sl("light_pattern_scale", "Pattern size", 1, 0.3, 3, 0.05, v => v.toFixed(2))}
        {sl("light_pattern_speed", "Speed", 1, 0, 3, 0.05, v => v.toFixed(2))}
        {sl("light_pattern_spread", "Spread (softness)", 0.15, 0, 1, 0.01, pct)}
        {sl("light_pattern_warp", "Bend (warp)", 1, 0, 2, 0.05, v => v.toFixed(2))}
        {sl("light_pattern_falloff", "Fade towards the bottom", 0.6, 0, 1, 0.01, pct)}
        <p className="muted small">Your own pattern (a picture or looping video: bright shapes on a dark ground): choose it under <b>light pattern asset id</b> below. The studio look's Master amount scales this too.</p></>}
    </section>);
}

/** Colour lights: stage-style coloured lights from the sides, on the subject only */
function ColourLights({ op, edit }: { op: Op; edit: Edit }) {
  const pr = op.parameters ?? {};
  const num = (k: string, d: number) => (typeof pr[k] === "number" ? (pr[k] as number) : d);
  const str = (k: string, d: string) => (typeof pr[k] === "string" ? (pr[k] as string) : d);
  const set = (k: string, v: number | string | undefined) => edit(b => m.setParam(b, op.id, k, v), `p:${op.id}:${k}`);
  const preset = (o: Record<string, number | string>) => edit(b => Object.entries(o).reduce((acc, [k, v]) => m.setParam(acc, op.id, k, v), b), `p:${op.id}:colourpreset`);
  const on = num("colour_light", 0) > 0;
  const place = str("colour_light_place", "fixed");
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const sides = ["left", "right", "top", "bottom", "off"] as const;
  const light = (id: "a" | "b", title: string, dc: string, ds: string) => (
    <div className="cl-light" key={id}>
      <label className="field"><span className="fl">{title}</span>
        <span className="slider-row"><input type="color" value={str(`colour_light_color_${id}`, dc)} onChange={e => set(`colour_light_color_${id}`, e.target.value)} aria-label={`${title} colour`} />
          <select value={str(`colour_light_side_${id}`, ds)} onChange={e => set(`colour_light_side_${id}`, e.target.value)} aria-label={`${title} side`}>
            {sides.map(sd => <option key={sd} value={sd}>{sd === "off" ? "off" : `from the ${sd}`}</option>)}</select></span></label>
    </div>);
  const sl = (k: string, label: string, d: number) => (
    <label className="field"><span className="fl">{label} <em>{pct(num(k, d))}</em></span>
      <input type="range" min={0} max={1} step={0.01} value={num(k, d)} onChange={e => set(k, +e.target.value)} aria-label={label} /></label>);
  return (
    <section className="colourlights">
      <div className="master-head"><h4>Colour lights</h4><span className="muted small">{on ? pct(num("colour_light", 0)) : "off"}</span></div>
      <p className="muted small">Stage-style coloured lights on the <b>subject only</b>: for example cyan from the left and magenta from the right, with a glow along the edge facing each light (jaw, cheek, hair, fins). The background is never touched. It is placed by position and outline, not by the real 3D shape. Needs a background replacement.</p>
      <div className="btn-row">
        <Btn label={on ? "Off" : "Cyan / magenta portrait"} kind={on ? undefined : "primary"} onClick={() => (on ? set("colour_light", 0) : preset({ colour_light: 0.8, colour_light_color_a: "#19b8ff", colour_light_side_a: "left", colour_light_color_b: "#b01cff", colour_light_side_b: "right", colour_light_reach: 0.6, colour_light_edge: 0.7 }))} title="Cool cyan key from the left, magenta from the right" />
      </div>
      {sl("colour_light", "Light strength", 0)}
      {on && <>
        {light("a", "Light A", "#19b8ff", "left")}
        {light("b", "Light B", "#b01cff", "right")}
        <div className="btn-row" role="group" aria-label="Where the lights sit">
          {([["fixed", "Fixed (subject, middle frame)"], ["follow", "Follow the subject"], ["zone", "My own spot"]] as const).map(([k, l]) =>
            <Btn key={k} label={l} kind={place === k ? "primary" : undefined} onClick={() => set("colour_light_place", k)}
              title={k === "fixed" ? "A stage light: placed from the subject on the middle frame, stays put in the picture" : k === "follow" ? "The lights travel with the subject (measured about every quarter second)" : "Drag a rectangle on the picture: only inside it is lit"} />)}
        </div>
        {place === "zone" && <><p className="muted small">Drag the dashed rectangle on the preview to move it; drag its white handles to resize it. Only inside it is lit, and the subject is lit only where it is inside.</p>
          {sl("colour_light_zone_soft", "Soft edge of the spot", 0.3)}</>}
        {place === "follow" && <p className="muted small">The lights follow the subject through the clip: its position is measured about every quarter second and smoothed. Fast, sudden moves can lag a little between measurements.</p>}
        {place === "fixed" && <p className="muted small">Placed from where the subject is on the <b>middle</b> frame and then fixed in the picture, like a stage light. If the subject starts somewhere else, use <b>Follow the subject</b> or <b>My own spot</b>.</p>}
        {sl("colour_light_reach", "How far it reaches", 0.5)}
        {sl("colour_light_edge", "Edge glow", 0.6)}
        <p className="muted small">Where the two lights meet in the middle they mix. The studio look's Master amount scales this too.</p></>}
    </section>);
}

/** Auto enhance: ONE master slider. The clip is measured and the matching corrections are applied together. */
function AutoEnhance({ op, edit, note }: { op: Op; edit: Edit; note?: EnhanceNote }) {
  const pr = op.parameters ?? {};
  const v = typeof pr.auto_enhance === "number" ? pr.auto_enhance : 0;
  const style = typeof pr.enhance_style === "string" ? pr.enhance_style : "crisp";
  const where = typeof pr.apply_to === "string" ? pr.apply_to : "both";
  const set = (k: string, x: number | string) => edit(b => m.setParam(b, op.id, k, x), `p:${op.id}:${k}`);
  const full = note?.full, ms = note?.measured;
  const f = (x: number, d = 2) => (x >= 0 ? "+" : "") + x.toFixed(d);
  return (
    <section className="autoenhance">
      <div className="master-head"><h4>Auto enhance</h4><span className="muted small">{v > 0 ? `${Math.round(v * 100)}%` : "off"}</span></div>
      <p className="muted small">One slider for a flat, hazy, dim or colour-cast clip. The clip is measured and the right corrections (haze, blacks, whites, brightness, contrast, white balance, colour, clarity, detail) are applied together. Your own sliders below add on top.</p>
      <div className="master-row"><input type="range" min={0} max={150} value={Math.round(v * 100)} onChange={e => set("auto_enhance", +e.target.value / 100)} onDoubleClick={() => set("auto_enhance", 0)} aria-label="Auto enhance amount" />
        <Btn label={v > 0 ? "Off" : "Enhance"} kind={v > 0 ? undefined : "primary"} onClick={() => set("auto_enhance", v > 0 ? 0 : 1)} /></div>
      <div className="btn-row" role="group" aria-label="Enhance style">
        {([["natural", "Natural"], ["crisp", "Crisp"], ["vivid", "Vivid"]] as const).map(([k, l]) =>
          <Btn key={k} label={l} kind={style === k ? "primary" : undefined} onClick={() => set("enhance_style", k)}
            title={k === "natural" ? "Gentle: keeps the mood of the shot" : k === "crisp" ? "Clean, bright and a little cool: a clear-day look" : "More colour and punch"} />)}
      </div>
      <div className="btn-row" role="group" aria-label="Apply to">
        {([["both", "Both"], ["subject", "Subject only"], ["background", "Background only"]] as const).map(([k, l]) =>
          <Btn key={k} label={l} kind={where === k ? "primary" : undefined} onClick={() => set("apply_to", k)}
            title={k === "both" ? "The whole picture" : k === "subject" ? "Only the person / object you keyed: the background never changes" : "Only the picture behind the subject: the subject never changes"} />)}
      </div>
      {where !== "both" && <p className="muted small">Applies to the {where === "subject" ? "subject (your keyed person or object)" : "background behind the subject"} only, and measures only that part. Needs a removed background with a background replacement; this whole Color Grading (your own sliders too) follows this choice.</p>}
      {v > 0 && !note && <p className="muted small">Measuring the clip on the next preview frame…</p>}
      {v > 0 && ms && full && <ul className="cm-list">
        <li><b>Measured:</b> middle brightness {Math.round(ms.p50 * 100)}%, whites {Math.round((ms.white ?? ms.p99) * 100)}%, darkest {Math.round(ms.p1 * 100)}%, cast {ms.r > ms.b * 1.02 ? "warm" : ms.b > ms.r * 1.02 ? "cool" : "neutral"}</li>
        <li><b>Applying at {Math.round(v * 100)}%:</b> exposure {f(full.exposure * v)} · whites {f(full.whites * v)} · blacks {f(full.blacks * v)} · dehaze {f(full.dehaze * v)} · contrast ×{(1 + v * (full.contrast - 1)).toFixed(2)} · temperature {f(full.temperature * v, 0)} · tint {f(full.tint * v, 0)} · clarity {f(full.clarity * v)} · texture {f(full.texture * v)}</li>
      </ul>}
      <p className="muted small">It changes brightness and colour; it cannot turn a white sky blue or invent detail that is not in the video.</p>
    </section>);
}

/** Cinematic look: a real film grade in one control (colour by hue, split tone, filmic curve, vignette, grain) + cinema bars */
function Cinematic({ op, edit, video }: { op: Op; edit: Edit; video: VideoInfo | null }) {
  const pr = op.parameters ?? {};
  const v = typeof pr.cinematic === "number" ? pr.cinematic : 0;
  const style = typeof pr.cinematic_style === "string" ? pr.cinematic_style : "teal_orange";
  const bars = typeof pr.letterbox === "string" ? pr.letterbox : "none";
  const set = (k: string, x: number | string) => edit(b => m.setParam(b, op.id, k, x), `p:${op.id}:${k}`);
  const tall = !!video && video.height >= video.width * 0.75;
  return (
    <section className="cinematic">
      <div className="master-head"><h4>Cinematic look</h4><span className="muted small">{v > 0 ? `${Math.round(v * 100)}%` : "off"}</span></div>
      <p className="muted small">A film grade in one control: filmic tones (soft blacks, highlights that roll off), colour by hue (skin stays warm, greens go teal, magentas / blues calm down), teal shadows and warm highlights, restrained colour, a vignette and fine film grain.</p>
      <div className="master-row"><input type="range" min={0} max={150} value={Math.round(v * 100)} onChange={e => set("cinematic", +e.target.value / 100)} onDoubleClick={() => set("cinematic", 0)} aria-label="Cinematic amount" />
        <Btn label={v > 0 ? "Off" : "Make it cinematic"} kind={v > 0 ? undefined : "primary"} onClick={() => set("cinematic", v > 0 ? 0 : 1)} /></div>
      <div className="btn-row" role="group" aria-label="Cinematic style">
        {([["teal_orange", "Teal & orange"], ["moody", "Moody"], ["golden", "Golden"], ["bleach", "Bleach"]] as const).map(([k, l]) =>
          <Btn key={k} label={l} kind={style === k ? "primary" : undefined} onClick={() => set("cinematic_style", k)}
            title={k === "teal_orange" ? "The blockbuster look: teal shadows, warm skin" : k === "moody" ? "Darker, muted, cold shadows" : k === "golden" ? "Warm golden-hour film" : "Bleach bypass: low colour, high contrast"} />)}
      </div>
      <label className="field"><span className="fl">Cinema bars</span>
        <select value={bars} onChange={e => set("letterbox", e.target.value)} aria-label="Cinema bars">
          <option value="none">none</option><option value="2.39">2.39 : 1 (anamorphic widescreen)</option><option value="2.0">2 : 1</option><option value="1.85">1.85 : 1</option></select></label>
      {bars !== "none" && tall && <p className="muted small">This video is tall: bars would hide most of it, so they are not drawn. Use them on landscape (16:9) video.</p>}
      <p className="muted small">Tip: put <b>Auto enhance</b> (Natural, about 70 %) in the same grade first for a clean base; the look goes on top.</p>
    </section>);
}

/** AI face restoration: the model (348 MB) is downloaded on request, with progress; honest guidance on what it does */
function FaceRestoration({ op, edit, api }: { op: Op; edit: Edit; api?: Api }) {
  const [st, setSt] = useState<ModelStatus | null>(null);
  const [err, setErr] = useState("");
  const pr = op.parameters ?? {};
  const identity = typeof pr.identity === "number" ? pr.identity : 0.5;
  useEffect(() => {
    if (!api) return; let alive = true, timer: ReturnType<typeof setTimeout>;
    const tick = async () => { try { const r = await api.modelStatus("gfpgan"); if (alive) { setSt(r); if (r.downloading) timer = setTimeout(tick, 700); } } catch { /* engine busy */ } };
    tick(); return () => { alive = false; clearTimeout(timer); };
  }, [api, st?.downloading]);
  const start = async () => { if (!api) return; setErr(""); try { setSt(await api.installModel("gfpgan")); } catch (e: any) { setErr(e.message ?? String(e)); } };
  const mb = (n: number) => `${Math.round(n / 1e6)} MB`;
  return (
    <section className="facerestore">
      <div className="master-head"><h4>AI face restoration</h4><span className="muted small">{st?.installed ? "model ready" : "model needed"}</span></div>
      <p className="muted small">Rebuilds sharp eyes, eyebrows, hair strands, beard and skin detail of small, soft or noisy faces, like the "enhance" in phone apps. Only faces are changed; the rest of the picture stays exactly as it is.</p>
      {st && !st.installed && <div className="modelbox">
        {st.downloading ? <><div className="gpubar" aria-hidden><span style={{ width: `${Math.min(100, (100 * st.done) / Math.max(1, st.total))}%` }} /></div>
          <p className="muted small">Downloading the model: {mb(st.done)} of {mb(st.total)}…</p></>
          : <><Btn icon="export" kind="primary" label={`Download the model (${mb(st.size)})`} onClick={start} title={`GFPGAN v1.4, ${st.license}. Downloaded once from GitHub and converted on this computer.`} />
            <p className="muted small">Needed once ({st.license}). Until then this effect cannot show a picture.</p></>}
        {(st.error || err) && <div className="issue error">{st.error || err}</div>}
      </div>}
      <p className="muted small"><b>Honest limits:</b> the AI <b>re-draws</b> detail, so the result is plausible rather than exact. {identity < 0.35 ? <>At this <b>Identity</b> value the AI's face is used as it is: it can lighten skin and slightly change eyes. Raise Identity to keep your own colours and face shape.</> : <>Identity keeps your own colours and face shape and takes only the fine detail.</>} Small, blurry faces get a made-up face: that is why faces narrower than <b>Smallest face</b> are skipped.</p>
      <p className="muted small">Speed: about 1.3 s per face per frame on a 4-core processor (the live frame is one frame; a whole clip takes a while), much faster on a graphics card. The result is cached.</p>
    </section>);
}

/** Camera match: make the subject look as if the same camera shot it and the background */
function CameraMatch({ op, edit, note }: { op: Op; edit: Edit; note?: CameraNote }) {
  const v = (op.parameters?.camera_match as number | undefined) ?? 0;
  const set = (x: number) => edit(b => m.setParam(b, op.id, "camera_match", Math.round(x * 100) / 100), `p:${op.id}:camera_match`);
  const ms = note?.measured;
  return (
    <section className="cameramatch">
      <div className="master-head"><h4>Camera match</h4><span className="muted small">{v > 0 ? `${Math.round(v * 100)}%` : "off"}</span></div>
      <p className="muted small">Footage and a background picture are rarely shot by the same camera: one is sharper, grainier or has different blacks, and that is what makes a subject look pasted on. Camera match measures both and adjusts the <b>subject</b> (and, if needed, the background grain) so they agree. Needs a background replacement.</p>
      <div className="master-row"><input type="range" min={0} max={100} value={Math.round(v * 100)} onChange={e => set(+e.target.value / 100)} onDoubleClick={() => set(0)} aria-label="Camera match amount" />
        <Btn label={v > 0 ? "Off" : "Match now"} kind={v > 0 ? undefined : "primary"} onClick={() => set(v > 0 ? 0 : 1)} /></div>
      {v > 0 && !note && <p className="muted small">Measuring on the next preview frame…</p>}
      {v > 0 && note && ms && (<ul className="cm-list">
        <li><b>Sharpness:</b> subject edge blur {ms.subject_edge_blur_px.toFixed(1)} px, background {ms.background_edge_blur_px.toFixed(1)} px → {note.match_softness > 0.05 ? `softening the subject (${note.match_softness.toFixed(1)})` : note.match_softness < -0.05 ? `sharpening the subject (${(-note.match_softness).toFixed(1)})` : "already matched"}</li>
        <li><b>Grain:</b> subject {ms.subject_noise_levels.toFixed(1)}, background {ms.background_noise_levels.toFixed(1)} levels → {note.subject_grain > 0.02 ? `adding grain to the subject (${note.subject_grain.toFixed(2)})` : note.background_grain > 0.02 ? `adding grain to the background (${note.background_grain.toFixed(2)})` : "already matched"}</li>
        <li><b>Black level:</b> subject {ms.subject_black.toFixed(2)}, background {ms.background_black.toFixed(2)} → {Math.abs(note.match_blacks) > 0.005 ? `${note.match_blacks > 0 ? "lifting" : "deepening"} the subject's blacks (${note.match_blacks.toFixed(2)})` : "no change"}</li>
      </ul>)}
      <p className="muted small">Fine-tune below: <b>match softness</b>, <b>subject grain</b>, <b>background grain</b>, <b>match blacks</b> (your values always win over the measured ones) and <b>fringe</b> (lens colour fringing at the frame edges).</p>
    </section>);
}

/** Key doctor: measure the key on sample frames, say what is wrong, offer real fixes */
function KeyDoctor({ op, bp, schema, edit, setT, run, dur }: { op: Op; bp: Blueprint; schema: Schema; edit: Edit; setT: (t: number) => void; run?: (id: string) => Promise<any>; dur: number }) {
  const [res, setRes] = useState<any | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const check = async () => {
    if (!run) return; setBusy(true); setErr(""); setApplied(new Set());
    try { setRes(await run(op.id)); } catch (e: any) { setErr(e.message ?? String(e)); setRes(null); }
    setBusy(false);
  };
  const apply = (f: any) => {
    const fx = f.fix; if (!fx) return;
    edit(b => {
      let cur = b;
      for (const [k, v] of Object.entries(fx.set ?? {})) cur = m.setParam(cur, op.id, k, v);
      for (const add of fx.add_ops ?? []) {
        const spec = schema.operations[add.type]; if (!spec) continue;
        const r = m.addOp(cur, spec, op.start, op.end ?? dur); cur = r.bp;
        for (const [k, v] of Object.entries(add.params)) cur = m.setParam(cur, r.id, k, v);
      }
      return cur;
    }, `doctor:${op.id}:${f.id}`);
    setApplied(s => new Set(s).add(f.id));
  };
  const fixable = (res?.findings ?? []).filter((f: any) => f.fix && (f.fix.set || f.fix.add_ops) && !applied.has(f.id));
  return (
    <section className="doctor">
      <div className="master-head"><h4>Key doctor</h4>{res && <span className={"score " + (res.score >= 85 ? "good" : res.score >= 60 ? "mid" : "bad")} title="100 = nothing to fix">{res.score}/100</span>}</div>
      <p className="muted small">Measures the footage on sample frames and tells you what is wrong with the key: uneven screen, a screen that does not fill the frame, see-through spots, green spill, edge too hard or too wide, flicker. Each finding comes with a fix you can apply.</p>
      <div className="btn-row"><Btn icon="wand" kind="primary" label={busy ? "Checking…" : res ? "Check again" : "Check this key"} onClick={check} disabled={busy || !run} />
        {fixable.length > 1 && <Btn label={`Apply all fixes (${fixable.length})`} onClick={() => fixable.forEach(apply)} />}</div>
      {err && <div className="issue error"><Icon n="warn" size={14} /> {err}</div>}
      {res && <p className="muted small"><b>{res.summary}</b> ({res.frames_checked} frames checked)</p>}
      {res?.findings.map((f: any) => (
        <div key={f.id} className={`finding ${f.severity}`}>
          <div className="ft"><Icon n={f.severity === "info" ? "check" : "warn"} size={13} /> <b>{f.title}</b></div>
          <p>{f.detail}</p>
          <div className="btn-row">
            {f.time !== null && <Btn label={`Show frame (${f.time.toFixed(1)} s)`} icon="eye" onClick={() => setT(Math.max(0, f.time))} title="Move the playhead to the worst frame, then use View → Matte" />}
            {f.fix && (f.fix.set || f.fix.add_ops) && <Btn kind="primary" label={applied.has(f.id) ? "Applied ✓" : f.fix.label} disabled={applied.has(f.id)} onClick={() => apply(f)} />}
            {f.fix?.advice === "clean_plate" && <span className="muted small">→ use the Clean plate section below</span>}
          </div>
        </div>))}
    </section>);
}

/** clean plate: what it is, import it, and whether it actually fitted the footage */
function CleanPlateTools({ op, edit, importAsset, note }: { op: Op; edit: Edit; importAsset?: () => Promise<string | null>; note?: { used: number; rejected: number } }) {
  const has = !!op.parameters?.clean_plate_asset_id;
  const pick = async () => { const id = importAsset ? await importAsset() : null; if (id) edit(b => m.setParam(b, op.id, "clean_plate_asset_id", id)); };
  return (
    <section className="cleanplate">
      <h4>Clean plate</h4>
      <p className="muted small">Record 3–5 seconds of the <b>empty</b> green screen with the same camera, framing, zoom, focus and lights, then use it here. The key then knows every wrinkle, fold, hot spot and shadow of the screen instead of guessing, so edges around hair and the face come out cleaner.</p>
      <div className="btn-row"><Btn icon="plus" label={has ? "Replace clean plate…" : "Import clean plate…"} onClick={pick} disabled={!importAsset} title="A video or a picture of the empty screen" />
        {has && <Btn label="Remove" onClick={() => edit(b => m.setParam(b, op.id, "clean_plate_asset_id", undefined))} />}</div>
      {has && note && note.rejected === 0 && note.used > 0 && <div className="issue ok"><Icon n="check" size={14} /> The clean plate fits: used on every checked frame.</div>}
      {has && note && note.rejected > 0 && <div className="issue warning"><Icon n="warn" size={14} /> The clean plate does not fit {note.rejected} of {note.used + note.rejected} checked frames (camera moved, different framing or lighting). Those frames use the normal estimate. Record the plate again without touching the camera.</div>}
      {has && !note && <p className="muted small">It is checked against the footage the next time a frame is rendered.</p>}
    </section>);
}

/** Master amount: ONE slider that fades the whole effect between the original footage (0 %) and the full effect (100 %), and pushes it further (up to 150 % where the effect allows it). The sliders below keep their own values. */
function MasterAmount({ op, spec, edit }: { op: Op; spec: ParamSpec; edit: Edit }) {
  const max = Math.round((spec.maximum ?? 1.5) * 100);
  const v = Math.round(((op.parameters?.amount as number | undefined) ?? spec.default ?? 1) * 100);
  const set = (pct: number) => { const c = Math.min(max, Math.max(0, Math.round(pct))); edit(b => m.setParam(b, op.id, "amount", c / 100), `p:${op.id}:amount`); };
  const key = op.type === "background_removal" || op.type === "chroma_key";
  return (
    <section className="master" aria-label="Master amount">
      <div className="master-head"><h4>Master amount</h4>
        <button className="link" disabled={v === 100} onClick={() => set(100)} title="Back to 100 %">Reset</button></div>
      <div className="master-row">
        <input type="range" min={0} max={max} step={1} value={v} onChange={e => set(+e.target.value)} onDoubleClick={() => set(100)}
          className={v > 100 ? "over" : ""} aria-label="Master amount" style={{ ["--p100" as any]: `${(100 / max) * 100}%` }} />
        <span className="pct"><input type="number" min={0} max={max} value={v} onChange={e => { const n = parseFloat(e.target.value); if (!isNaN(n)) set(n); }} aria-label="Master amount in percent" />%</span>
      </div>
      <p className="muted small">{key ? "0 % = the original footage (nothing removed) · 100 % = fully removed. In between cross-fades the two." : max > 100 ? "0 % = the original footage · 100 % = the effect as set below · up to 150 % pushes it further." : "0 % = the original footage · 100 % = the effect as set below."} Double-click the slider to reset. The sliders below are your fine-tuning: they keep their values.</p>
    </section>);
}

export function Properties({ api, enhanceNotes, bp, schema, video, layout, sel, selClip, edit, t, setT, issues, onReapply, onSelect, onSelectClip, onCut, onDeleteClip, importAsset, plateNotes, keyDoctor, cameraNotes, hairNotes, floorNotes }: {
  floorNotes?: Record<string, { y: number; cut: boolean }>;
  hairNotes?: Record<string, { used: number; skipped: number }>;
  cameraNotes?: Record<string, CameraNote>;
  enhanceNotes?: Record<string, EnhanceNote>;
  api?: Api;
  keyDoctor?: (opId: string) => Promise<any>;
  importAsset?: () => Promise<string | null>; plateNotes?: Record<string, { used: number; rejected: number }>;
  bp: Blueprint; schema: Schema; video: VideoInfo | null; layout: m.Layout; sel: string | null; selClip: string | null; edit: Edit; t: number; setT: (t: number) => void; issues: Issue[];
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
      {op.keyframes && Object.keys(op.keyframes).length > 0 && <p className="muted kfsummary"><Icon n="diamond" size={11} /> Animated: {Object.entries(op.keyframes).map(([n, k]) => `${n.replace(/_/g, " ")} (${k.length})`).join(", ")}</p>}
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
          <label className="field"><span className="fl">Start <em className="unit">(s)</em></span><input type="number" step={1 / fps} min={0} max={dur} value={+op.start.toFixed(4)} onChange={e => { const s = parseFloat(e.target.value); if (!isNaN(s)) edit(b => m.setOpTime(b, op.id, Math.min(s, end - 1 / fps), end), "time:" + op.id); }} /></label>
          <label className="field"><span className="fl">end <em className="unit">(s)</em></span><input type="number" step={1 / fps} min={0} max={dur} value={+end.toFixed(4)} onChange={e => { const s = parseFloat(e.target.value); if (!isNaN(s)) edit(b => m.setOpTime(b, op.id, op.start, Math.max(s, op.start + 1 / fps)), "time:" + op.id); }} /></label>
        </div>
      </section>
      {op.type === "color_grading" && <AutoEnhance op={op} edit={edit} note={enhanceNotes?.[op.id]} />}
      {op.type === "color_grading" && <Cinematic op={op} edit={edit} video={video} />}
      {op.type === "face_restoration" && <FaceRestoration op={op} edit={edit} api={api} />}
      {op.type === "matte_mask" && <MaskTools op={op} edit={edit} />}
      {op.type === "background_replacement" && <FloorFx op={op} edit={edit} note={floorNotes?.[op.id]} />}
      {op.type === "studio_look" && <ColourLights op={op} edit={edit} />}
      {op.type === "studio_look" && <LightPattern op={op} edit={edit} />}
      {op.type === "studio_look" && <CameraMatch op={op} edit={edit} note={cameraNotes?.[op.id]} />}
      {(op.type === "background_removal" || op.type === "chroma_key") && <KeyDoctor op={op} bp={bp} schema={schema} edit={edit} setT={setT} run={keyDoctor} dur={dur} />}
      {op.type === "background_removal" && op.parameters?.mode === "screen" && <HairDetail op={op} edit={edit} note={hairNotes?.[op.id]} />}
      {op.type === "background_removal" && op.parameters?.mode === "screen" && <CleanPlateTools op={op} edit={edit} importAsset={importAsset} note={plateNotes?.[op.id]} />}
      {spec?.parameters.amount && <MasterAmount op={op} spec={spec.parameters.amount} edit={edit} />}
      {op.type === "color_grading" && <section><h4>Colour wheels</h4><ColorWheels op={op} edit={edit} /></section>}
      <section>
        <h4>{spec?.parameters.amount ? "Fine-tune" : "Parameters"}</h4>
        {spec ? Object.entries(spec.parameters).filter(([k]) => k !== "amount" && !(op.type === "color_grading" && (k === "lift" || k === "gamma_rgb" || k === "gain"))).map(([k, ps]) => (
          <ParamField key={k} name={k} spec={ps} op={op} bp={bp} t={t} fps={fps} opLen={Math.max(1 / fps, end - op.start)} edit={edit} setT={setT} />))
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
