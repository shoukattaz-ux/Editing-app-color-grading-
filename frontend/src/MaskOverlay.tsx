import React, { useEffect, useRef, useState } from "react";
import type { Op } from "./model";

type P = Record<string, any>;
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const r4 = (v: number) => Math.round(v * 10000) / 10000;

/** the part of the preview that actually shows the picture (the image is letterboxed inside the stage) */
export function useFitRect(stage: React.RefObject<HTMLElement>, vw: number, vh: number) {
  const [rect, setRect] = useState({ left: 0, top: 0, width: 0, height: 0 });
  useEffect(() => {
    const el = stage.current; if (!el || !vw || !vh) return;
    const calc = () => {
      const W = el.clientWidth, H = el.clientHeight, s = Math.min(W / vw, H / vh);
      const w = vw * s, h = vh * s; setRect({ left: (W - w) / 2, top: (H - h) / 2, width: w, height: h });
    };
    calc(); const ro = new ResizeObserver(calc); ro.observe(el); return () => ro.disconnect();
  }, [stage, vw, vh]);
  return rect;
}

const colour = (mode: string) => (mode === "keep" ? "#22c55e" : "#ef4444");

/**
 * Draw garbage / keep masks on the picture: drag a shape to move it, drag its handles to resize (rectangle, ellipse), or click to place the corners of a polygon.
 * The selected mask is editable; the other masks of the project are shown thin.
 */
export function MaskOverlay({ rect, masks, selected, onChange, onSelect }: {
  rect: { left: number; top: number; width: number; height: number }; masks: Op[]; selected: string | null;
  onChange: (id: string, patch: P) => void; onSelect: (id: string) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ kind: string; idx?: number; x0: number; y0: number; start: P } | null>(null);
  if (!rect.width) return null;
  const toN = (e: React.PointerEvent | PointerEvent) => { const b = svg.current!.getBoundingClientRect(); return { x: clamp((e.clientX - b.left) / b.width), y: clamp((e.clientY - b.top) / b.height) }; };
  const sel = masks.find(m => m.id === selected);

  const begin = (e: React.PointerEvent, kind: string, idx?: number) => {
    if (!sel) return; e.stopPropagation(); (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const n = toN(e); drag.current = { kind, idx, x0: n.x, y0: n.y, start: JSON.parse(JSON.stringify(sel.parameters ?? {})) };
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current; if (!d || !sel) return;
    const n = toN(e), dx = n.x - d.x0, dy = n.y - d.y0, s = d.start;
    if (d.kind === "move") {
      if (s.shape === "polygon") onChange(sel.id, { polygon: (s.polygon ?? []).map(([x, y]: number[]) => [r4(clamp(x + dx)), r4(clamp(y + dy))]) });
      else onChange(sel.id, { x: r4(clamp(s.x + dx, 0, 1 - s.width)), y: r4(clamp(s.y + dy, 0, 1 - s.height)) });
    } else if (d.kind === "pt") {
      const pts = (s.polygon ?? []).map((p: number[]) => [...p]); pts[d.idx!] = [r4(n.x), r4(n.y)]; onChange(sel.id, { polygon: pts });
    } else if (d.kind.startsWith("h")) {          // resize handle: h + (n|s|e|w combos)
      let x0 = s.x, y0 = s.y, x1 = s.x + s.width, y1 = s.y + s.height;
      if (d.kind.includes("w")) x0 = clamp(n.x, 0, x1 - 0.01); if (d.kind.includes("e")) x1 = clamp(n.x, x0 + 0.01, 1);
      if (d.kind.includes("n")) y0 = clamp(n.y, 0, y1 - 0.01); if (d.kind.includes("s")) y1 = clamp(n.y, y0 + 0.01, 1);
      onChange(sel.id, { x: r4(x0), y: r4(y0), width: r4(x1 - x0), height: r4(y1 - y0) });
    }
  };
  const end = () => { drag.current = null; };
  const addPoint = (e: React.PointerEvent) => {
    if (!sel || sel.parameters?.shape !== "polygon" || drag.current) return;
    const n = toN(e); onChange(sel.id, { polygon: [...(sel.parameters?.polygon ?? []), [r4(n.x), r4(n.y)]] });
  };

  const W = rect.width, H = rect.height;
  const shapeEl = (m: Op, active: boolean) => {
    const p = m.parameters ?? {}, c = colour(p.mode ?? "garbage"), common = {
      fill: c, fillOpacity: active ? 0.22 : 0.1, stroke: c, strokeWidth: active ? 2 : 1, strokeDasharray: active ? "6 4" : "3 3",
      style: { cursor: active ? "move" : "pointer", pointerEvents: "all" as const },
      onPointerDown: active ? (e: React.PointerEvent) => begin(e, "move") : (e: React.PointerEvent) => { e.stopPropagation(); onSelect(m.id); },
    };
    if (p.shape === "polygon") return <polygon key={m.id} points={(p.polygon ?? []).map(([x, y]: number[]) => `${x * W},${y * H}`).join(" ")} {...common} />;
    const x = (p.x ?? 0) * W, y = (p.y ?? 0) * H, w = (p.width ?? 0.15) * W, h = (p.height ?? 1) * H;
    return p.shape === "ellipse" ? <ellipse key={m.id} cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} {...common} /> : <rect key={m.id} x={x} y={y} width={w} height={h} {...common} />;
  };
  const handles: [string, number, number][] = [];
  if (sel && sel.parameters?.shape !== "polygon") {
    const p = sel.parameters ?? {}, x0 = p.x ?? 0, y0 = p.y ?? 0, x1 = x0 + (p.width ?? 0.15), y1 = y0 + (p.height ?? 1), mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    handles.push(["hnw", x0, y0], ["hn", mx, y0], ["hne", x1, y0], ["he", x1, my], ["hse", x1, y1], ["hs", mx, y1], ["hsw", x0, y1], ["hw", x0, my]);
  }
  const cur: Record<string, string> = { hnw: "nwse-resize", hse: "nwse-resize", hne: "nesw-resize", hsw: "nesw-resize", hn: "ns-resize", hs: "ns-resize", he: "ew-resize", hw: "ew-resize" };
  return (
    <svg ref={svg} className="mask-overlay" width={W} height={H} style={{ left: rect.left, top: rect.top }} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onPointerDown={addPoint}>
      {masks.filter(m => m.id !== selected).map(m => shapeEl(m, false))}
      {sel && shapeEl(sel, true)}
      {handles.map(([k, x, y]) => <rect key={k} x={x * W - 5} y={y * H - 5} width={10} height={10} fill="#fff" stroke={colour(sel!.parameters?.mode ?? "garbage")} strokeWidth={2}
        style={{ cursor: cur[k], pointerEvents: "all" }} onPointerDown={e => begin(e, k)} />)}
      {sel && sel.parameters?.shape === "polygon" && (sel.parameters?.polygon ?? []).map(([x, y]: number[], i: number) =>
        <circle key={i} cx={x * W} cy={y * H} r={6} fill="#fff" stroke={colour(sel.parameters?.mode ?? "garbage")} strokeWidth={2} style={{ cursor: "grab", pointerEvents: "all" }}
          onPointerDown={e => begin(e, "pt", i)} onDoubleClick={e => { e.stopPropagation(); onChange(sel.id, { polygon: (sel.parameters?.polygon ?? []).filter((_: number[], j: number) => j !== i) }); }} />)}
    </svg>);
}

/** Colour lights, "My own spot": drag the rectangle to move it, drag its handles to resize it. The lights are confined to it. */
export function ZoneOverlay({ rect, zone, onChange }: {
  rect: { left: number; top: number; width: number; height: number };
  zone: { x0: number; y0: number; x1: number; y1: number; colour: string };
  onChange: (patch: P) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ kind: string; x0: number; y0: number; start: typeof zone } | null>(null);
  if (!rect.width) return null;
  const toN = (e: React.PointerEvent) => { const b = svg.current!.getBoundingClientRect(); return { x: clamp((e.clientX - b.left) / b.width), y: clamp((e.clientY - b.top) / b.height) }; };
  const begin = (e: React.PointerEvent, kind: string) => { e.stopPropagation(); (e.currentTarget as Element).setPointerCapture(e.pointerId); const n = toN(e); drag.current = { kind, x0: n.x, y0: n.y, start: { ...zone } }; };
  const move = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return; const n = toN(e), s = d.start;
    if (d.kind === "move") {
      const w = s.x1 - s.x0, h = s.y1 - s.y0, x0 = clamp(s.x0 + n.x - d.x0, 0, 1 - w), y0 = clamp(s.y0 + n.y - d.y0, 0, 1 - h);
      onChange({ colour_light_zone_x0: r4(x0), colour_light_zone_y0: r4(y0), colour_light_zone_x1: r4(x0 + w), colour_light_zone_y1: r4(y0 + h) });
    } else {
      let x0 = s.x0, y0 = s.y0, x1 = s.x1, y1 = s.y1;
      if (d.kind.includes("w")) x0 = clamp(n.x, 0, x1 - 0.03); if (d.kind.includes("e")) x1 = clamp(n.x, x0 + 0.03, 1);
      if (d.kind.includes("n")) y0 = clamp(n.y, 0, y1 - 0.03); if (d.kind.includes("s")) y1 = clamp(n.y, y0 + 0.03, 1);
      onChange({ colour_light_zone_x0: r4(x0), colour_light_zone_y0: r4(y0), colour_light_zone_x1: r4(x1), colour_light_zone_y1: r4(y1) });
    }
  };
  const end = () => { drag.current = null; };
  const W = rect.width, H = rect.height, mx = (zone.x0 + zone.x1) / 2, my = (zone.y0 + zone.y1) / 2;
  const handles: [string, number, number, string][] = [["hnw", zone.x0, zone.y0, "nwse-resize"], ["hn", mx, zone.y0, "ns-resize"], ["hne", zone.x1, zone.y0, "nesw-resize"], ["he", zone.x1, my, "ew-resize"],
    ["hse", zone.x1, zone.y1, "nwse-resize"], ["hs", mx, zone.y1, "ns-resize"], ["hsw", zone.x0, zone.y1, "nesw-resize"], ["hw", zone.x0, my, "ew-resize"]];
  return (
    <svg ref={svg} className="mask-overlay zone-overlay" width={W} height={H} style={{ left: rect.left, top: rect.top }} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
      <rect x={zone.x0 * W} y={zone.y0 * H} width={(zone.x1 - zone.x0) * W} height={(zone.y1 - zone.y0) * H} fill={zone.colour} fillOpacity={0.12} stroke={zone.colour} strokeWidth={2} strokeDasharray="6 4"
        style={{ cursor: "move", pointerEvents: "all" }} onPointerDown={e => begin(e, "move")} aria-label="Light zone" />
      {handles.map(([k, x, y, c]) => <rect key={k} x={x * W - 5} y={y * H - 5} width={10} height={10} fill="#fff" stroke={zone.colour} strokeWidth={2} style={{ cursor: c, pointerEvents: "all" }} onPointerDown={e => begin(e, k)} />)}
    </svg>);
}
