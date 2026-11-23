import React, { useEffect, useMemo, useRef } from "react";
import type { Blueprint, Op, Schema } from "./model";
import * as m from "./model";
import type { Issue, VideoInfo } from "./api";
import { Btn, Icon } from "./ui";

const TRACKS: { id: string; label: string; icon: string }[] = [
  { id: "video", label: "Video", icon: "film" }, { id: "background", label: "Background", icon: "image" },
  { id: "effects", label: "Effects", icon: "wand" }, { id: "overlay", label: "Text / Graphics", icon: "text" }, { id: "audio", label: "Audio", icon: "audio" },
];
const CAT_COLOR: Record<string, string> = { Keying: "#2fb57a", Compositing: "#7a6cf0", Color: "#e3913a", Enhance: "#3aa6e3", Graphics: "#e35a8a", Audio: "#a0a63a" };

export function Timeline({ bp, schema, video, t, setT, sel, setSel, edit, issues, zoom, setZoom, sourceName }: {
  bp: Blueprint; schema: Schema; video: VideoInfo | null; t: number; setT: (t: number) => void; sel: string | null; setSel: (id: string | null) => void;
  edit: (fn: (b: Blueprint) => Blueprint, key?: string) => void; issues: Issue[]; zoom: number; setZoom: (z: number) => void; sourceName: string;
}) {
  const fps = video?.fps ?? 30, dur = video?.duration ?? 10;
  const scroller = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; mode: "move" | "start" | "end"; x0: number; op: Op } | null>(null);
  const width = Math.max(300, dur * zoom + 40);
  useEffect(() => { const w = (scroller.current?.clientWidth ?? 900) - 160; if (video) setZoom(Math.max(8, Math.min(600, w / video.duration))); }, [video?.duration]);

  const lanes = useMemo(() => {
    const res: Record<string, { op: Op; lane: number }[]> = {};
    const ends: Record<string, number[]> = {};
    [...bp.operations].sort((a, b) => a.start - b.start).forEach(op => {
      const tr = schema.operations[op.type]?.track ?? "effects";
      const e = ends[tr] ?? (ends[tr] = []);
      const end = op.end ?? dur;
      let lane = e.findIndex(x => x <= op.start + 1e-6);
      if (lane < 0) { lane = e.length; e.push(end); } else e[lane] = end;
      (res[tr] ?? (res[tr] = [])).push({ op, lane });
    });
    return res;
  }, [bp.operations, schema, dur]);

  const errIds = new Set(issues.filter(i => i.level === "error" && i.op_id).map(i => i.op_id));
  const seek = (e: React.PointerEvent, el: HTMLElement) => { const r = el.getBoundingClientRect(); setT(m.xToTime(e.clientX - r.left, zoom, fps, dur)); };

  const ticks: number[] = [];
  const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find(s => s * zoom >= 70) ?? 600;
  for (let s = 0; s <= dur; s += step) ticks.push(s);

  const onWheel = (e: React.WheelEvent) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); setZoom(Math.min(600, Math.max(8, zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))); } };

  const down = (e: React.PointerEvent, op: Op) => {
    e.stopPropagation(); setSel(op.id);
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const mode = e.clientX - r.left < 7 ? "start" : r.right - e.clientX < 7 ? "end" : "move";
    drag.current = { id: op.id, mode, x0: e.clientX, op };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const moveDrag = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return;
    const r = m.dragOp(d.op, d.mode, (e.clientX - d.x0) / zoom, fps, dur);
    edit(b => m.setOpTime(b, d.id, r.start, r.end), "drag:" + d.id);
  };

  return (
    <div className="timeline">
      <div className="tl-bar">
        <span className="tl-title"><Icon n="layers" /> Timeline</span>
        <Btn icon="split" label="Split" title="Split the selected operation at the playhead" disabled={!sel} onClick={() => { if (!sel) return; const r = m.splitOp(bp, sel, t, fps); if (r.ids) { edit(() => r.bp); setSel(r.ids[1]); } }} />
        <Btn icon="trash" label="Delete" title="Delete selected operation (Del)" disabled={!sel} onClick={() => { if (sel) { edit(b => m.deleteOp(b, sel)); setSel(null); } }} />
        <span className="spacer" />
        <label className="zoom">Zoom <input type="range" min={8} max={600} value={zoom} onChange={e => setZoom(parseInt(e.target.value))} aria-label="Timeline zoom" /></label>
        <Btn label="Fit" onClick={() => { const w = (scroller.current?.clientWidth ?? 800) - 150; setZoom(Math.max(8, Math.min(600, w / dur))); }} />
      </div>
      <div className="tl-body" ref={scroller} onWheel={onWheel}>
        <div className="tl-inner" style={{ width: width + 120 }}>
          <div className="tl-row ruler-row">
            <div className="tl-label" />
            <div className="ruler" style={{ width }} onPointerDown={e => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); seek(e, e.currentTarget as HTMLElement); }}
              onPointerMove={e => { if (e.buttons) seek(e, e.currentTarget as HTMLElement); }}>
              {ticks.map(s => <span key={s} className="tick" style={{ left: s * zoom }}>{m.formatTime(s, fps).slice(3)}</span>)}
            </div>
          </div>
          {TRACKS.map(tr => {
            const items = lanes[tr.id] ?? [];
            const nl = Math.max(1, ...items.map(i => i.lane + 1));
            return (
              <div className="tl-row" key={tr.id} style={{ height: 8 + nl * 28 }}>
                <div className="tl-label"><Icon n={tr.icon} size={14} /> {tr.label}</div>
                <div className="tl-lane" style={{ width }} onPointerDown={e => { setSel(null); seek(e, e.currentTarget as HTMLElement); }}>
                  {tr.id === "video" && video && <div className="clip source" style={{ left: 0, width: dur * zoom, top: 4 }}><Icon n="film" size={12} /> {sourceName}</div>}
                  {tr.id === "audio" && video?.has_audio && items.length === 0 && <div className="clip source audio" style={{ left: 0, width: dur * zoom, top: 4 }}><Icon n="audio" size={12} /> Source audio</div>}
                  {items.map(({ op, lane }) => {
                    const spec = schema.operations[op.type]; const end = op.end ?? dur;
                    return (
                      <div key={op.id} className={`clip op ${sel === op.id ? "sel" : ""} ${op.enabled === false ? "off" : ""} ${errIds.has(op.id) ? "err" : ""}`}
                        style={{ left: op.start * zoom, width: Math.max(10, (end - op.start) * zoom), top: 4 + lane * 28, background: CAT_COLOR[spec?.category ?? ""] ?? "#667" }}
                        title={`${spec?.title ?? op.type} · ${op.start.toFixed(2)}–${end.toFixed(2)}s`}
                        onPointerDown={e => down(e, op)} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null; }}>
                        <i className="handle l" /><span>{op.label || spec?.title || op.type}</span><i className="handle r" />
                      </div>);
                  })}
                </div>
              </div>);
          })}
          <div className="playhead" style={{ left: 96 + t * zoom }}><i /></div>
        </div>
      </div>
    </div>
  );
}
