import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Blueprint, Op, Schema } from "./model";
import * as m from "./model";
import type { Issue, VideoInfo } from "./api";
import { Btn, Icon } from "./ui";
import { MEDIA_MIME, MediaDrag } from "./Media";

const TRACKS: { id: string; label: string; icon: string }[] = [
  { id: "video", label: "Video", icon: "film" }, { id: "background", label: "Background", icon: "image" },
  { id: "effects", label: "Effects", icon: "wand" }, { id: "overlay", label: "Text / Graphics", icon: "text" }, { id: "audio", label: "Audio", icon: "audio" },
];
const CAT_COLOR: Record<string, string> = { Keying: "#2fb57a", Compositing: "#7a6cf0", Color: "#e3913a", Enhance: "#3aa6e3", Graphics: "#e35a8a", Audio: "#a0a63a" };

/** distinct keyframe times (relative to the effect's start) across all its animated parameters */
const kfTimes = (op: Op): number[] => [...new Set(Object.values(op.keyframes ?? {}).flatMap(k => k.map(x => +x.time.toFixed(4))))].sort((a, b) => a - b);

export function Timeline({ bp, schema, video, layout, t, setT, sel, setSel, selClip, setSelClip, edit, issues, zoom, setZoom, sourceName, onCut, onDeleteClip, history, onDropMedia }: {
  onDropMedia: (d: MediaDrag, t: number, lane: string) => void;
  history: { canUndo: boolean; canRedo: boolean; undoCount: number; redoCount: number; undoLabel: string; redoLabel: string; onUndo: () => void; onRedo: () => void };
  bp: Blueprint; schema: Schema; video: VideoInfo | null; layout: m.Layout; t: number; setT: (t: number) => void; sel: string | null; setSel: (id: string | null) => void;
  selClip: string | null; setSelClip: (id: string | null) => void;
  edit: (fn: (b: Blueprint) => Blueprint, key?: string) => void; issues: Issue[]; zoom: number; setZoom: (z: number) => void; sourceName: string;
  onCut: () => void; onDeleteClip: () => void;
}) {
  const fps = layout.fps, dur = layout.duration || 10;
  const clipDrag = useRef<{ id: string; side: "start" | "end"; x0: number; edge0: number } | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [dropLane, setDropLane] = useState("");
  const drag = useRef<{ id: string; mode: "move" | "start" | "end"; x0: number; op: Op } | null>(null);
  const width = Math.max(300, dur * zoom + 40);
  useEffect(() => { const w = (scroller.current?.clientWidth ?? 900) - 160; if (video) setZoom(Math.max(8, Math.min(600, w / video.duration))); }, [video?.duration]);
  const srcDur = video?.duration ?? 0;
  const downClip = (e: React.PointerEvent, c: m.LaidClip) => {
    e.stopPropagation(); setSel(null); setSelClip(c.id);
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const side = e.clientX - r.left < 8 ? "start" : r.right - e.clientX < 8 ? "end" : null;
    if (!side) return;
    clipDrag.current = { id: c.id, side, x0: e.clientX, edge0: side === "start" ? c.srcStart : c.srcEnd };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const moveClipDrag = (e: React.PointerEvent) => {
    const d = clipDrag.current; if (!d) return;
    const target = d.edge0 + (e.clientX - d.x0) / zoom;
    edit(b => m.trimClip(b, d.id, d.side, target, srcDur, fps), `trim:${d.id}:${d.side}`);
  };

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
    e.stopPropagation(); setSel(op.id); setSelClip(null);
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
        <div className="undo-group" role="group" aria-label="Undo and redo">
          <button className="ur" onClick={history.onUndo} disabled={!history.canUndo} aria-label="Undo"
            title={history.canUndo ? `Undo: ${history.undoLabel} (Ctrl+Z)` : "Nothing to undo"}>
            <Icon n="undo" size={15} /><span>Undo</span>{history.undoCount > 0 && <em>{history.undoCount}</em>}</button>
          <button className="ur" onClick={history.onRedo} disabled={!history.canRedo} aria-label="Redo"
            title={history.canRedo ? `Redo: ${history.redoLabel} (Ctrl+Y)` : "Nothing to redo"}>
            <Icon n="redo" size={15} /><span>Redo</span>{history.redoCount > 0 && <em>{history.redoCount}</em>}</button>
        </div>
        <span className="sep" />
        <Btn icon="scissors" label="Cut" title="Cut the footage at the playhead (C)" disabled={!video} onClick={onCut} />
        <Btn icon="trash" label="Delete clip" kind="danger" title="Delete the selected clip; later footage and effects move earlier (Del)" disabled={!selClip || layout.clips.length < 2} onClick={onDeleteClip} />
        <span className="sep" />
        <Btn icon="split" label="Split effect" title="Split the selected effect at the playhead" disabled={!sel} onClick={() => { if (!sel) return; const r = m.splitOp(bp, sel, t, fps); if (r.ids) { edit(() => r.bp); setSel(r.ids[1]); } }} />
        <Btn icon="trash" label="Delete effect" title="Delete the selected effect (Del)" disabled={!sel} onClick={() => { if (sel) { edit(b => m.deleteOp(b, sel)); setSel(null); } }} />
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
                <div className={"tl-lane" + (dropLane === tr.id ? " dropok" : "")} style={{ width }}
                  onDragOver={e => { if (e.dataTransfer.types.includes(MEDIA_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; if (dropLane !== tr.id) setDropLane(tr.id); } }}
                  onDragLeave={() => setDropLane(l => (l === tr.id ? "" : l))}
                  onDrop={e => {
                    const raw = e.dataTransfer.getData(MEDIA_MIME); setDropLane(""); if (!raw) return;
                    e.preventDefault(); const r = e.currentTarget.getBoundingClientRect();
                    try { onDropMedia(JSON.parse(raw) as MediaDrag, m.xToTime(e.clientX - r.left, zoom, fps, dur), tr.id); } catch { /* not ours */ }
                  }} onPointerDown={e => { setSel(null); setSelClip(null); seek(e, e.currentTarget as HTMLElement); }}>
                  {tr.id === "video" && video && layout.clips.map((c, i) => (
                    <div key={c.id} className={`clip source footage ${selClip === c.id ? "sel" : ""}`} style={{ left: c.tStart * zoom, width: Math.max(10, (c.tEnd - c.tStart) * zoom - 1), top: 4 }}
                      title={`${c.label || sourceName} · source ${m.formatTime(c.srcStart, fps).slice(3)} – ${m.formatTime(c.srcEnd, fps).slice(3)} · drag an edge to trim`}
                      onPointerDown={e => downClip(e, c)} onPointerMove={moveClipDrag} onPointerUp={() => { clipDrag.current = null; }}>
                      <i className="handle l" /><Icon n="film" size={12} /><span>{layout.cut ? (c.label || `${sourceName} · ${m.formatTime(c.srcStart, fps).slice(3, 8)}–${m.formatTime(c.srcEnd, fps).slice(3, 8)}`) : sourceName}</span><i className="handle r" />
                    </div>))}
                  {tr.id === "video" && layout.cut && layout.clips.slice(1).map(c => <i key={"cut" + c.id} className="cutmark" style={{ left: c.tStart * zoom - 1 }} />)}
                  {tr.id === "audio" && video?.has_audio && items.length === 0 && <div className="clip source audio" style={{ left: 0, width: dur * zoom, top: 4 }}><Icon n="audio" size={12} /> Source audio{layout.cut ? " (follows the cuts)" : ""}</div>}
                  {items.map(({ op, lane }) => {
                    const spec = schema.operations[op.type]; const end = op.end ?? dur;
                    return (
                      <div key={op.id} className={`clip op ${sel === op.id ? "sel" : ""} ${op.enabled === false ? "off" : ""} ${errIds.has(op.id) ? "err" : ""}`}
                        style={{ left: op.start * zoom, width: Math.max(10, (end - op.start) * zoom), top: 4 + lane * 28, background: CAT_COLOR[spec?.category ?? ""] ?? "#667" }}
                        title={`${spec?.title ?? op.type} · ${op.start.toFixed(2)}–${end.toFixed(2)}s`}
                        onPointerDown={e => down(e, op)} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null; }}>
                        <i className="handle l" /><span>{op.label || spec?.title || op.type}</span><i className="handle r" />
                        {kfTimes(op).filter(k => k >= 0 && k <= end - op.start).map(k => <i key={k} className="kfd" style={{ left: k * zoom - 4 }} title={`keyframe at ${(op.start + k).toFixed(2)}s`} />)}
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
