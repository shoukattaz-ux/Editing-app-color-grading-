import React, { useEffect, useRef, useState } from "react";
import type { Api, VideoInfo } from "./api";
import type { Blueprint } from "./model";
import * as m from "./model";
import { Btn, Empty, Icon } from "./ui";

export type Mode = "original" | "edited" | "compare";

/** Processed still frame at time t (same engine as export). Debounced; stale responses are dropped. */
function useFrame(api: Api, bp: Blueprint, t: number, original: boolean, enabled: boolean, width: number | null = 960) {
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
        const blob = await api.frame(bp, t, original, width);
        if (my !== seq.current) return;
        setUrl(old => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); }); setErr(null);
      } catch (e: any) { if (my === seq.current) setErr(e.message); }
      finally { if (my === seq.current) setBusy(false); }
    }, 220);
    return () => clearTimeout(h);
  }, [api, bp, Math.round(t * 1000), original, enabled, width]);
  return { url, err, busy };
}

export function Preview({ api, bp, video, t, setT, mode, setMode, proxyUrl, stale, onUpdateProxy, proxyBusy, videoRef }: {
  api: Api; bp: Blueprint; video: VideoInfo | null; t: number; setT: (t: number) => void; mode: Mode; setMode: (m: Mode) => void;
  proxyUrl: string | null; stale: boolean; onUpdateProxy: () => void; proxyBusy: boolean; videoRef: React.RefObject<HTMLVideoElement>;
}) {
  const fps = video?.fps ?? 30, dur = video?.duration ?? 0;
  const [playing, setPlaying] = useState(false);
  const [wipe, setWipe] = useState(50);
  const [loupe, setLoupe] = useState<number>(0);          // 0 = off, else zoom factor (1 = 100% / 1:1 pixels)
  const wrap = useRef<HTMLDivElement>(null);
  const playable = mode === "original" || (mode === "edited" && !!proxyUrl && !stale);
  const needEditedFrame = mode === "compare" || (mode === "edited" && !playable);
  const full = mode === "compare" && loupe > 0 ? null : 960;
  const edited = useFrame(api, bp, t, false, needEditedFrame && !!video, full);
  const orig = useFrame(api, bp, t, true, mode === "compare" && !!video, full);
  const src = mode === "original" ? api.sourceUrl() : proxyUrl;

  // keep the <video> element and the shared playhead in sync
  useEffect(() => {
    const v = videoRef.current; if (!v || !playable) return;
    if (!playing && Math.abs(v.currentTime - t) > 0.5 / fps) v.currentTime = t;
  }, [t, playable, src, playing, fps]);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => { const v = videoRef.current; if (v) { setT(v.currentTime); if (v.ended) setPlaying(false); } raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick); return () => cancelAnimationFrame(raf);
  }, [playing]);
  useEffect(() => { const v = videoRef.current; if (!v) return; if (playing && playable) v.play().catch(() => setPlaying(false)); else v.pause(); }, [playing, playable]);
  useEffect(() => { if (!playable) setPlaying(false); }, [playable]);

  const step = (n: number) => { setPlaying(false); setT(m.snap(Math.min(dur, Math.max(0, t + n / fps)), fps)); };
  const toggle = () => { if (!playable) return; if (!playing && t >= dur - 1 / fps) setT(0); setPlaying(p => !p); };
  const stop = () => { setPlaying(false); setT(0); };
  (window as any).__evpToggle = toggle; (window as any).__evpStep = step;

  if (!video) return <div className="preview"><Empty icon="film" title="No video imported">Import a source video from the Media tab to start.</Empty></div>;

  const badge = mode === "compare" && loupe > 0 ? `Before/After · full-resolution frames at ${loupe * 100}%` : mode === "original" ? "Original · source quality" : playable ? "Edited · 640px proxy preview (final render is full quality)"
    : mode === "compare" ? "Compare · 960px live frames" : "Edited · live frame 960px — update the preview to play";
  return (
    <div className="preview">
      <div className="stage" ref={wrap}>
        {playable && src && <video ref={videoRef} key={src} src={src} className="media" playsInline preload="auto" onLoadedMetadata={e => { (e.target as HTMLVideoElement).currentTime = t; }} />}
        {mode === "edited" && !playable && (edited.url ? <img className="media" src={edited.url} alt="Edited frame" /> : <div className="media ph">{edited.err ? <span className="err-text">{edited.err}</span> : "Rendering frame…"}</div>)}
        {mode === "compare" && loupe > 0 && <Loupe before={orig.url} after={edited.url} zoom={loupe} />}
        {mode === "compare" && loupe === 0 && (
          <div className="compare">
            {orig.url && <img className="media" src={orig.url} alt="Original frame" />}
            {edited.url && <img className="media top" src={edited.url} alt="Edited frame" style={{ clipPath: `inset(0 0 0 ${wipe}%)` }} />}
            <div className="wipe-line" style={{ left: `${wipe}%` }} />
            <input className="wipe" type="range" min={0} max={100} value={wipe} onChange={e => setWipe(+e.target.value)} aria-label="Before/after divider" />
            <span className="tag l">BEFORE</span><span className="tag r">AFTER</span>
            {edited.err && <div className="err-banner">{edited.err}</div>}
          </div>)}
        {(edited.busy || orig.busy || proxyBusy) && <div className="spin" title="Rendering" />}
        {mode === "edited" && !playable && (
          <div className="banner"><Icon n="warn" size={14} /> {proxyUrl ? "Preview video is out of date with your edits." : "No preview video yet."}
            <Btn label="Update preview video" icon="bolt" kind="primary" onClick={onUpdateProxy} disabled={proxyBusy} /></div>)}
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
        <span className="badge">{badge}</span>
        <Btn icon="full" title="Full screen" onClick={() => wrap.current?.requestFullscreen?.()} />
      </div>
    </div>
  );
}

/** Side-by-side magnifier: both panes show the same spot of full-resolution frames at 1:1 (or 2x/4x) pixels.
 *  Drag either pane to move; the two panes stay locked together. */
function Loupe({ before, after, zoom }: { before: string | null; after: string | null; zoom: number }) {
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
      {[["BEFORE", before], ["AFTER", after]].map(([label, url]) => (
        <div key={label as string} ref={label === "AFTER" ? pane : undefined} className="loupe-pane" style={style(url as string | null)}
          onPointerDown={down} onPointerMove={move} onPointerUp={() => { drag.current = null; }} role="img" aria-label={`${label} at ${zoom * 100}%`}>
          <span className="tag l">{label} · {zoom * 100}%</span>
          {!url && <span className="loupe-wait">Rendering full-resolution frame…</span>}
        </div>))}
    </div>);
}
