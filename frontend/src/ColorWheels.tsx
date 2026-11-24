import React, { useRef } from "react";
import * as w from "./colorwheel";
import type { Blueprint, Op } from "./model";
import * as m from "./model";
import { Btn } from "./ui";

// hue h (0 red, 120 green, 240 blue) is drawn at screen angle h counter-clockwise from the right; CSS conic angles run clockwise from the top
const RING = `conic-gradient(${Array.from({ length: 37 }, (_, i) => `hsl(${(((90 - i * 10) % 360) + 360) % 360} 85% 52%) ${i * 10}deg`).join(",")})`;

function Wheel({ spec, value, onChange }: { spec: w.WheelSpec; value: w.Triplet | null; onChange: (t: w.Triplet | null) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x0: number; y0: number; puck: { x: number; y: number } } | null>(null);
  const cur: w.Triplet = value ?? [0, 0, 0];
  const puck = w.toPuck(cur, spec), master = puck.master;
  const rr = Math.min(1, puck.r), px = puck.r > 1e-9 ? (puck.x / puck.r) * rr : 0, py = puck.r > 1e-9 ? (puck.y / puck.r) * rr : 0;   // drawn clamped to the rim
  const commit = (t: w.Triplet) => onChange(w.isNeutral(t) ? null : t);
  const pos = (e: React.PointerEvent) => { const r = el.current!.getBoundingClientRect(); const R = r.width / 2; return { x: (e.clientX - r.left - R) / R, y: -(e.clientY - r.top - R) / R }; };
  const down = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const p = pos(e); drag.current = { x0: p.x, y0: p.y, puck: { x: px, y: py } };
    if (!e.shiftKey) commit(w.fromPuck(p.x, p.y, master, spec));            // shift = fine adjust relative to where the puck is
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return;
    const p = pos(e);
    commit(e.shiftKey ? w.fromPuck(d.puck.x + (p.x - d.x0) * 0.2, d.puck.y + (p.y - d.y0) * 0.2, master, spec) : w.fromPuck(p.x, p.y, master, spec));
  };
  const key = (e: React.KeyboardEvent) => {
    const s = e.shiftKey ? 0.1 : 0.02;
    const d: Record<string, [number, number]> = { ArrowLeft: [-s, 0], ArrowRight: [s, 0], ArrowUp: [0, s], ArrowDown: [0, -s] };
    if (d[e.key]) { e.preventDefault(); e.stopPropagation(); commit(w.fromPuck(px + d[e.key][0], py + d[e.key][1], master, spec)); }
    else if (e.key === "Delete" || e.key === "Backspace" || e.key === "Home") { e.preventDefault(); e.stopPropagation(); onChange(null); }
  };
  const setChannel = (i: number, v: number) => { const t = [...cur] as w.Triplet; t[i] = Math.min(1, Math.max(-1, v)); commit(w.round4(t)); };
  return (
    <div className="wheel">
      <div className="wheel-title"><b>{spec.label}</b><em>{spec.sub}</em></div>
      <div ref={el} className="wheel-disc" style={{ background: RING }} tabIndex={0} role="application" aria-label={`${spec.label} colour wheel (${spec.sub}). Arrow keys move, Delete resets.`}
        aria-description={w.describe(cur, spec)} onPointerDown={down} onPointerMove={move} onPointerUp={() => { drag.current = null; }} onKeyDown={key} onDoubleClick={() => onChange(null)} title="Drag to tint · Shift = fine · double-click to reset">
        <div className="wheel-fade" />
        <div className="wheel-puck" data-testid={`puck-${spec.param}`} style={{ left: `${50 + px * 50}%`, top: `${50 - py * 50}%`, outline: puck.r > 1.001 ? "2px solid #ff4d6d" : undefined }} />
      </div>
      <label className="wheel-master"><span>Master</span>
        <input type="range" min={-spec.masterRange} max={spec.masterRange} step={0.005} value={Math.max(-spec.masterRange, Math.min(spec.masterRange, master))} aria-label={`${spec.label} master`}
          onChange={e => commit(w.withMaster(cur, parseFloat(e.target.value)))} onDoubleClick={() => commit(w.withMaster(cur, 0))} />
      </label>
      <div className="wheel-rgb">
        {["R", "G", "B"].map((c, i) => <label key={c}><span style={{ color: ["#ff6b6b", "#5fd38d", "#6ea8ff"][i] }}>{c}</span>
          <input type="number" step={0.01} min={-1} max={1} value={+cur[i].toFixed(3)} aria-label={`${spec.label} ${c}`} onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) setChannel(i, v); }} /></label>)}
        <Btn label="Reset" onClick={() => onChange(null)} disabled={value === null} title={`Reset ${spec.label}`} />
      </div>
    </div>);
}

/** Lift / Gamma / Gain colour wheels for a color_grading operation. Values are the operation's [r,g,b] parameters. */
export function ColorWheels({ op, edit }: { op: Op; edit: (fn: (b: Blueprint) => Blueprint, key?: string) => void }) {
  return (
    <div className="wheels" role="group" aria-label="Colour wheels">
      {w.WHEELS.map(spec => (
        <Wheel key={spec.param} spec={spec} value={(op.parameters?.[spec.param] as w.Triplet | undefined) ?? null}
          onChange={t => edit(b => m.setParam(b, op.id, spec.param, t ?? undefined), `wheel:${op.id}:${spec.param}`)} />))}
    </div>);
}
