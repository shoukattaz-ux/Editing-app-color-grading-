import React, { useEffect, useRef, useState } from "react";
import type { Api, VideoInfo } from "./api";
import type { Blueprint } from "./model";
import * as m from "./model";
import { Btn, Empty, Icon } from "./ui";

export type Mode = "original" | "edited" | "compare";

/** Processed still frame at time t (same engine as export). Debounced; stale responses are dropped. */
function useFrame(api: Api, bp: Blueprint, t: number, original: boolean, enabled: boolean) {
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
        const blob = await api.frame(bp, t, original);
        if (my !== seq.current) return;
        setUrl(old => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); }); setErr(null);
      } catch (e: any) { if (my === seq.current) setErr(e.message); }
      finally { if (my === seq.current) setBusy(false); }
    }, 220);
    return () => clearTimeout(h);
  }, [api, bp, Math.round(t * 1000), original, enabled]);
  return { url, err, busy };
}

export function Preview({ api, bp, video, t, setT, mode, setMode, proxyUrl, stale, onUpdateProxy, proxyBusy, videoRef }: {
  api: Api; bp: Blueprint; video: VideoInfo | null; t: number; setT: (t: number) => void; mode: Mode; setMode: (m: Mode) => void;
  proxyUrl: string | null; stale: boolean; onUpdateProxy: () => void; proxyBusy: boolean; videoRef: React.RefObject<HTMLVideoElement>;
}) {
  const fps = video?.fps ?? 30, dur = video?.duration ?? 0;
  const [playing, setPlaying] = useState(false);
  const [wipe, setWipe] = useState(50);
  const wrap = useRef<HTMLDivElement>(null);
  const playable = mode === "original" || (mode === "edited" && !!proxyUrl && !stale);
  const needEditedFrame = mode === "compare" || (mode === "edited" && !playable);
  const edited = useFrame(api, bp, t, false, needEditedFrame && !!video);
  const orig = useFrame(api, bp, t, true, mode === "compare" && !!video);
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

  const badge = mode === "original" ? "Original · source quality" : playable ? "Edited · 640px proxy preview (final render is full quality)"
    : mode === "compare" ? "Compare · 960px live frames" : "Edited · live frame 960px — update the preview to play";
  return (
    <div className="preview">
      <div className="stage" ref={wrap}>
        {playable && src && <video ref={videoRef} key={src} src={src} className="media" playsInline preload="auto" onLoadedMetadata={e => { (e.target as HTMLVideoElement).currentTime = t; }} />}
        {mode === "edited" && !playable && (edited.url ? <img className="media" src={edited.url} alt="Edited frame" /> : <div className="media ph">{edited.err ? <span className="err-text">{edited.err}</span> : "Rendering frame…"}</div>)}
        {mode === "compare" && (
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
        <span className="badge">{badge}</span>
        <Btn icon="full" title="Full screen" onClick={() => wrap.current?.requestFullscreen?.()} />
      </div>
    </div>
  );
}
