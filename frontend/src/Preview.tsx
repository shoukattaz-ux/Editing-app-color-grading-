import React, { useEffect, useRef, useState } from "react";
import type { Api, VideoInfo } from "./api";
import type { Blueprint } from "./model";
import * as m from "./model";
import { Btn, Empty, Icon } from "./ui";
import { MaskOverlay, ZoneOverlay, useFitRect } from "./MaskOverlay";
import type { Op } from "./model";

export type Mode = "original" | "edited" | "compare";

/** Processed still frame at time t (same engine as export). Debounced; stale responses are dropped. */
function useFrame(api: Api, bp: Blueprint, t: number, original: boolean, enabled: boolean, width: number | null = 960, view?: string) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const my = ++seq.current;
    const h = setTimeout(async () => {
      setBusy(true);
      try {
        const blob = await api.frame(bp, t, original, width, view);
        if (my !== seq.current) return;
        setUrl(old => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); }); setErr(null);
      } catch (e: any) { if (my === seq.current) setErr(e.message); }
      finally { if (my === seq.current) setBusy(false); }
    }, 220);
    return () => clearTimeout(h);
  }, [api, bp, Math.round(t * 1000), original, enabled, width, view]);
  return { url, err, busy };
}

export function Preview({ api, bp, video, layout, t, setT, mode, setMode, proxyUrl, stale, onUpdateProxy, proxyBusy, videoRef, playing, setPlaying, auto, setAuto, masks, zone }: {
  zone?: { x0: number; y0: number; x1: number; y1: number; colour: string; onChange: (patch: Record<string, any>) => void } | null;
  masks?: { ops: Op[]; selected: string | null; onChange: (id: string, patch: Record<string, any>) => void; onSelect: (id: string) => void };
  auto: boolean; setAuto: (v: boolean) => void;
  playing: boolean; setPlaying: (v: boolean | ((p: boolean) => boolean)) => void;
  api: Api; bp: Blueprint; video: VideoInfo | null; layout: m.Layout; t: number; setT: (t: number) => void; mode: Mode; setMode: (m: Mode) => void;
  proxyUrl: string | null; stale: boolean; onUpdateProxy: () => void; proxyBusy: boolean; videoRef: React.RefObject<HTMLVideoElement>;
}) {
  const fps = layout.fps, dur = layout.duration;
  const clipIdx = useRef(0);                       // clip being played in Original mode (the cut timeline is played by jumping between clips)
  const [wipe, setWipe] = useState(50);
  const [loupe, setLoupe] = useState<number>(0);          // 0 = off, else zoom factor (1 = 100% / 1:1 pixels)
  const wrap = useRef<HTMLDivElement>(null);
  // key QC views (matte / edge band / subject on a solid colour): judge a key by looking at the matte, not at the finished picture
  const [qc, setQc] = useState("normal");
  const [qcZoom, setQcZoom] = useState(0);                        // 0 = fit, else 1:1 / 2x / 4x with the normal picture beside it
  const hasKey = bp.operations.some(o => (o.type === "chroma_key" || o.type === "background_removal") && o.enabled !== false);
  const qcOn = qc !== "normal" && mode !== "original" && hasKey;
  const playable = !qcOn && (mode === "original" || (mode === "edited" && !!proxyUrl && !stale));
  const needEditedFrame = qcOn ? qcZoom > 0 : (mode === "compare" || (mode === "edited" && !playable));
  const full = qcOn ? (qcZoom > 0 ? null : 960) : (mode === "compare" && loupe > 0 ? null : 960);
  const view = useFrame(api, bp, t, false, qcOn && !!video, full, qc);
  const fit = useFitRect(wrap, video?.width ?? 0, video?.height ?? 0);
  const edited = useFrame(api, bp, t, false, needEditedFrame && !!video, full);
  const orig = useFrame(api, bp, t, true, mode === "compare" && !!video, full);
  const src = mode === "original" ? api.sourceUrl() : proxyUrl;

  // keep the <video> element and the shared playhead in sync
  // the time the <video> should show for timeline time t: Original = the source file (through the cuts), Edited = the rendered proxy
  const mediaTime = (tt: number) => (mode === "original" ? m.timelineToSource(layout, tt).src : tt);
  useEffect(() => {
    const v = videoRef.current; if (!v || !playable) return;
    if (!playing && Math.abs(v.currentTime - mediaTime(t)) > 0.5 / fps) v.currentTime = mediaTime(t);
    if (!playing) clipIdx.current = m.timelineToSource(layout, t).index;
  }, [t, playable, src, playing, fps, layout]);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v && mode === "original" && layout.cut) {
        const c = layout.clips[clipIdx.current], s = v.currentTime;
        if (c && s >= c.srcEnd - 0.5 / fps) {
          const next = layout.clips[clipIdx.current + 1];
          if (next) { clipIdx.current += 1; v.currentTime = next.srcStart; setT(next.tStart); }
          else { v.pause(); setPlaying(false); setT(Math.max(0, c.tEnd - 1 / fps)); return; }
        } else if (c) setT(c.tStart + Math.max(0, s - c.srcStart));
      } else if (v) { setT(v.currentTime); if (v.ended) setPlaying(false); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick); return () => cancelAnimationFrame(raf);
  }, [playing, mode, layout]);
  useEffect(() => { const v = videoRef.current; if (!v) return; if (playing && playable) v.play().catch(() => setPlaying(false)); else v.pause(); }, [playing, playable]);
  useEffect(() => { if (!playable) setPlaying(false); }, [playable]);

  const step = (n: number) => { setPlaying(false); setT(m.snap(Math.min(dur, Math.max(0, t + n / fps)), fps)); };
  const toggle = () => { if (!playable) return; if (!playing && t >= dur - 1 / fps) setT(0); setPlaying(p => !p); };
  const stop = () => { setPlaying(false); setT(0); };
  (window as any).__evpToggle = toggle; (window as any).__evpStep = step;

  if (!video) return <div className="preview"><Empty icon="film" title="No video imported">Import a source video from the Media tab to start.</Empty></div>;

  const badge = mode === "compare" && loupe > 0 ? `Before/After · full-resolution frames at ${loupe * 100}%` : mode === "original" ? "Original · source quality" : playable ? "Edited · 640px proxy preview (final render is full quality)"
    : mode === "compare" ? "Compare · 960px live frames" : "Edited · live frame 960px — playback after the preview video updates";
  return (
    <div className="preview">
      <div className="stage" ref={wrap}>
        {qcOn && qcZoom === 0 && (view.url ? <img className="media" src={view.url} alt="Key view" /> : <div className="media ph">{view.err ? <span className="err-text">{view.err}</span> : "Rendering…"}</div>)}
        {qcOn && qcZoom > 0 && <Loupe before={edited.url} after={view.url} zoom={qcZoom} labels={["NORMAL", qc === "alpha" ? "MATTE" : qc === "edge" ? "EDGE BAND" : "ON " + qc.toUpperCase()]} />}
        {playable && src && <video ref={videoRef} key={src} src={src} className="media" playsInline preload="auto" crossOrigin="anonymous" onLoadedMetadata={e => { (e.target as HTMLVideoElement).currentTime = mediaTime(t); }} />}
        {mode === "edited" && !playable && !qcOn && (edited.url ? <img className="media" src={edited.url} alt="Edited frame" /> : <div className="media ph">{edited.err ? <span className="err-text">{edited.err}</span> : "Rendering frame…"}</div>)}
        {mode === "compare" && !qcOn && loupe > 0 && <Loupe before={orig.url} after={edited.url} zoom={loupe} />}
        {mode === "compare" && !qcOn && loupe === 0 && (
          <div className="compare">
            {orig.url && <img className="media" src={orig.url} alt="Original frame" />}
            {edited.url && <img className="media top" src={edited.url} alt="Edited frame" style={{ clipPath: `inset(0 0 0 ${wipe}%)` }} />}
            <div className="wipe-line" style={{ left: `${wipe}%` }} />
            <input className="wipe" type="range" min={0} max={100} value={wipe} onChange={e => setWipe(+e.target.value)} aria-label="Before/after divider" />
            <span className="tag l">BEFORE</span><span className="tag r">AFTER</span>
            {edited.err && <div className="err-banner">{edited.err}</div>}
          </div>)}
        {(edited.busy || orig.busy || view.busy || proxyBusy) && <div className="spin" title="Rendering" />}
        {zone && !(qcOn && qcZoom > 0) && !(mode === "compare" && !qcOn) && <ZoneOverlay rect={fit} zone={zone} onChange={zone.onChange} />}
        {masks && masks.ops.length > 0 && !(qcOn && qcZoom > 0) && !(mode === "compare" && !qcOn) && <MaskOverlay rect={fit} masks={masks.ops} selected={masks.selected} onChange={masks.onChange} onSelect={masks.onSelect} />}
        {mode === "edited" && !playable && !qcOn && (          // small, in the corner: never covers the picture, which is already live
          <div className="chip-top">{proxyBusy ? <><span className="mini-spin" /> Updating preview video…</>
            : <>{auto ? "Preview video updates automatically" : "Preview video out of date"}<Btn label="Update now" icon="bolt" onClick={onUpdateProxy} disabled={proxyBusy} /></>}</div>)}
      </div>
      <div className="transport">
        <div className="seg" role="tablist" aria-label="Preview mode">
          {(["original", "edited", "compare"] as Mode[]).map(k => <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)} role="tab" aria-selected={mode === k}>{k === "compare" ? "Before/After" : k[0].toUpperCase() + k.slice(1)}</button>)}
        </div>
        <Btn icon="stop" title="Stop (return to start)" onClick={stop} />
        <Btn icon="prev" title="Previous frame (←)" onClick={() => step(-1)} />
        <Btn icon={playing ? "pause" : "play"} kind="primary" title={playable ? "Play / pause (Space)" : "Playback needs the Original or an up-to-date Edited preview"} onClick={toggle} disabled={!playable} />
        <Btn icon="next" title="Next frame (→)" onClick={() => step(1)} />
        <code className="tc">{m.formatTime(t, fps)}</code>
        <input className="scrub" type="range" min={0} max={Math.max(1, Math.round(dur * fps))} value={Math.round(t * fps)} onChange={e => { setPlaying(false); setT(+e.target.value / fps); }} aria-label="Scrub" />
        {mode === "compare" && (
          <select className="loupe-sel" value={loupe} onChange={e => setLoupe(+e.target.value)} aria-label="Zoom loupe" title="Pixel-level before/after at full output resolution">
            <option value={0}>Wipe</option><option value={1}>1:1 zoom</option><option value={2}>200%</option><option value={4}>400%</option>
          </select>)}
        <select className="loupe-sel" value={hasKey ? qc : "normal"} disabled={!hasKey || mode === "original"} onChange={e => setQc(e.target.value)} aria-label="Key view"
          title={!hasKey ? "Add a chroma key or background removal to use the key views" : mode === "original" ? "Switch to Edited or Before/After to look at the key" : "Look at the matte instead of the finished picture: halos and holes are easy to see"}>
          <option value="normal">View: normal</option><option value="alpha">Matte (alpha)</option><option value="edge">Edge band</option>
          <option value="red">On red</option><option value="white">On white</option><option value="grey">On grey</option><option value="checker">On checkerboard</option>
        </select>
        {qcOn && <select className="loupe-sel" value={qcZoom} onChange={e => setQcZoom(+e.target.value)} aria-label="Key view zoom" title="Zoom in on the edge: the normal picture is shown beside the key view">
          <option value={0}>Fit</option><option value={1}>1:1</option><option value={2}>200%</option><option value={4}>400%</option></select>}
        <button className={"autobtn" + (auto ? " on" : "")} onClick={() => setAuto(!auto)} aria-pressed={auto}
          title="Auto: re-render the preview video by itself shortly after you stop editing (videos up to 90 s). The picture itself always follows your sliders live.">Auto</button>
        <span className="badge">{badge}</span>
        <Btn icon="full" title="Full screen" onClick={() => wrap.current?.requestFullscreen?.()} />
      </div>
    </div>
  );
}

/** Side-by-side magnifier: both panes show the same spot of full-resolution frames at 1:1 (or 2x/4x) pixels.
 *  Drag either pane to move; the two panes stay locked together. */
function Loupe({ before, after, zoom, labels = ["BEFORE", "AFTER"] }: { before: string | null; after: string | null; zoom: number; labels?: [string, string] }) {
  const [focus, setFocus] = useState({ x: 0.5, y: 0.4 });
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const drag = useRef<{ x: number; y: number; f: { x: number; y: number } } | null>(null);
  const pane = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!after) return; const i = new Image(); i.onload = () => setNat({ w: i.naturalWidth, h: i.naturalHeight }); i.src = after; }, [after]);
  const style = (url: string | null): React.CSSProperties => {
    if (!url || !nat || !pane.current) return {};
    const pw = pane.current.clientWidth, ph = pane.current.clientHeight, w = nat.w * zoom, h = nat.h * zoom;
    return { backgroundImage: `url(${url})`, backgroundSize: `${w}px ${h}px`, imageRendering: zoom > 1 ? "pixelated" : "auto",
             backgroundPosition: `${pw / 2 - focus.x * w}px ${ph / 2 - focus.y * h}px`, backgroundRepeat: "no-repeat" };
  };
  const down = (e: React.PointerEvent) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, f: focus }; };
  const move = (e: React.PointerEvent) => {
    const d = drag.current; if (!d || !nat) return;
    setFocus({ x: Math.min(1, Math.max(0, d.f.x - (e.clientX - d.x) / (nat.w * zoom))), y: Math.min(1, Math.max(0, d.f.y - (e.clientY - d.y) / (nat.h * zoom))) });
  };
  return (
    <div className="loupe">
      {[[labels[0], before], [labels[1], after]].map(([label, url]) => (
        <div key={label as string} ref={label === labels[1] ? pane : undefined} className="loupe-pane" style={style(url as string | null)}
          onPointerDown={down} onPointerMove={move} onPointerUp={() => { drag.current = null; }} role="img" aria-label={`${label} at ${zoom * 100}%`}>
          <span className="tag l">{label} · {zoom * 100}%</span>
          {!url && <span className="loupe-wait">Rendering full-resolution frame…</span>}
        </div>))}
    </div>);
}
