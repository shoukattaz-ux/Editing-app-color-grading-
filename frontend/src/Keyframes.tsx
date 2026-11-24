import React, { useMemo, useRef, useState } from "react";
import * as m from "./model";
import { Btn } from "./ui";

const W = 300, H = 120, PAD = { l: 34, r: 8, t: 8, b: 16 };

/** Curve editor for ONE animated parameter: value (y) over time (x, seconds after the effect's start). */
export function KeyframeGraph({ kfs, length, min, max, integer, tRel, fps, selected, onSelect, onMove, onAdd, onRemove, onEasing, unit }: {
  kfs: m.Keyframe[]; length: number; min?: number; max?: number; integer?: boolean; tRel: number; fps: number; unit?: string;
  selected: number; onSelect: (i: number) => void;
  onMove: (i: number, t: number, v: number) => void; onAdd: (t: number, v: number) => void; onRemove: (i: number) => void; onEasing: (i: number, e: m.Easing) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<number | null>(null);
  // x range covers the whole effect and any keyframe outside it (those still shape the curve; drawn in the dimmed margins)
  const x0 = Math.min(0, kfs[0].time), x1 = Math.max(length, kfs[kfs.length - 1].time, x0 + 1e-3);
  // y range auto-fits the keyframe values (padded, never narrower than 15% of the parameter's range) and is frozen while dragging
  const fitted = useMemo(() => {
    const vals = kfs.map(k => k.value), dmin = Math.min(...vals), dmax = Math.max(...vals);
    const full = min !== undefined && max !== undefined ? max - min : Math.max(1, dmax - dmin);
    const span = Math.max(dmax - dmin, full * 0.15), mid = (dmin + dmax) / 2;
    let a = mid - span * 0.75, b = mid + span * 0.75;
    if (min !== undefined) a = Math.max(a, min - full * 0.02);
    if (max !== undefined) b = Math.min(b, max + full * 0.02);
    return [a, b] as const;
  }, [kfs, min, max]);
  const frozen = useRef<readonly [number, number] | null>(null);
  const [yLo, yHi] = drag.current !== null && frozen.current ? frozen.current : fitted;
  const lo = yLo, hi = yHi;
  const X = (t: number) => PAD.l + ((t - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
  const Y = (v: number) => PAD.t + (1 - (v - yLo) / (yHi - yLo)) * (H - PAD.t - PAD.b);
  const fromPointer = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W, py = ((e.clientY - r.top) / r.height) * H;
    const t = x0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (x1 - x0);
    let v = yLo + (1 - (py - PAD.t) / (H - PAD.t - PAD.b)) * (yHi - yLo);
    v = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v)); if (integer) v = Math.round(v);
    return { t: m.snap(t, fps), v };
  };
  const path = useMemo(() => {
    const n = 160, pts: string[] = [];
    for (let i = 0; i <= n; i++) { const t = x0 + ((x1 - x0) * i) / n; pts.push(`${X(t).toFixed(1)},${Y(m.valueAt(kfs, t)).toFixed(1)}`); }
    return pts.join(" ");
  }, [kfs, x0, x1, yLo, yHi]);
  const sel = kfs[selected];
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));
  const ticks = [lo + (hi - lo) * 0.04, (lo + hi) / 2, hi - (hi - lo) * 0.04];
  return (
    <div className="kfgraph">
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="application" aria-label="Keyframe graph" tabIndex={0}
        onKeyDown={e => { if ((e.key === "Delete" || e.key === "Backspace") && sel) { e.preventDefault(); e.stopPropagation(); onRemove(selected); } }}
        onPointerDown={e => { if (e.target === e.currentTarget || (e.target as Element).classList.contains("bg")) { const p = fromPointer(e); onAdd(p.t, p.v); } }}
        onPointerMove={e => { if (drag.current !== null) { const p = fromPointer(e); onMove(drag.current, p.t, p.v); } }}
        onPointerUp={() => { drag.current = null; frozen.current = null; }}>
        <rect className="bg" x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} />
        {x0 < 0 && <rect className="outside" x={PAD.l} y={PAD.t} width={X(0) - PAD.l} height={H - PAD.t - PAD.b} />}
        {x1 > length && <rect className="outside" x={X(length)} y={PAD.t} width={W - PAD.r - X(length)} height={H - PAD.t - PAD.b} />}
        {ticks.map((v, i) => <g key={i}><line className="grid" x1={PAD.l} x2={W - PAD.r} y1={Y(v)} y2={Y(v)} /><text className="tick" x={PAD.l - 4} y={Y(v) + 3} textAnchor="end">{fmt(v)}</text></g>)}
        <text className="tick" x={PAD.l} y={H - 3}>{x0.toFixed(1)}s</text><text className="tick" x={W - PAD.r} y={H - 3} textAnchor="end">{x1.toFixed(1)}s</text>
        <polyline className="curve" points={path} />
        {tRel >= x0 && tRel <= x1 && <line className="ph" x1={X(tRel)} x2={X(tRel)} y1={PAD.t} y2={H - PAD.b} />}
        {kfs.map((k, i) => (
          <g key={i} transform={`translate(${X(k.time)},${Y(k.value)}) rotate(45)`} className={`kfpt ${i === selected ? "sel" : ""}`}
            onPointerDown={e => { e.stopPropagation(); onSelect(i); drag.current = i; frozen.current = fitted; (svg.current as SVGSVGElement).setPointerCapture(e.pointerId); }}
            onDoubleClick={e => { e.stopPropagation(); onRemove(i); }}>
            <rect x={-5} y={-5} width={10} height={10} />
          </g>))}
      </svg>
      <div className="kfbar">
        {sel ? <>
          <span className="muted">Keyframe {selected + 1}/{kfs.length} · {sel.time.toFixed(2)}s · {fmt(sel.value)}{unit ? ` ${unit}` : ""}</span>
          <select value={sel.easing ?? "linear"} onChange={e => onEasing(selected, e.target.value as m.Easing)} aria-label="Easing to the next keyframe" title="Shape of the change from this keyframe to the next">
            {m.EASINGS.map(x => <option key={x} value={x}>{x.replace(/_/g, " ")}</option>)}
          </select>
          <Btn icon="trash" title="Delete this keyframe (Del, or double-click it)" onClick={() => onRemove(selected)} />
        </> : <span className="muted">Click the graph to add a keyframe</span>}
      </div>
    </div>);
}
