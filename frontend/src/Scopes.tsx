import React, { useEffect, useMemo, useRef, useState } from "react";
import * as sc from "./scopes";
import type { Ctx } from "./Sidebar";
import { Empty } from "./ui";

type Kind = "waveform" | "parade" | "vector" | "histogram";
const KINDS: { id: Kind; label: string }[] = [{ id: "waveform", label: "Waveform" }, { id: "parade", label: "RGB Parade" }, { id: "vector", label: "Vectorscope" }, { id: "histogram", label: "Histogram" }];
const load = <T,>(k: string, d: T): T => { try { const v = localStorage.getItem("evp.scopes." + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const save = (k: string, v: unknown) => { try { localStorage.setItem("evp.scopes." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } };
const SAMPLE_W = 360;

/** the pixels being judged: while playing, the video the preview shows; otherwise an exact frame rendered by the engine at the playhead */
function useScopeImage(ctx: Ctx, original: boolean): { img: sc.Img | null; busy: boolean; error: string | null; live: boolean } {
  const [img, setImg] = useState<sc.Img | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const live = ctx.playing && !!ctx.videoRef.current;
  useEffect(() => {
    const c = document.createElement("canvas"), g = c.getContext("2d", { willReadFrequently: true })!;
    if (live) {
      const tick = () => {
        const v = ctx.videoRef.current; if (!v || v.readyState < 2 || !v.videoWidth) return;
        c.width = SAMPLE_W; c.height = Math.max(2, Math.round((SAMPLE_W * v.videoHeight) / v.videoWidth));
        try { g.drawImage(v, 0, 0, c.width, c.height); const d = g.getImageData(0, 0, c.width, c.height); setImg({ data: d.data, width: d.width, height: d.height }); setError(null); }
        catch { setError("The browser blocked reading the video (cross-origin). Pause to analyse a rendered frame."); }
      };
      const h = setInterval(tick, 120); tick(); return () => clearInterval(h);
    }
    if (!ctx.video) { setImg(null); return; }
    const my = ++seq.current;
    const h = setTimeout(async () => {
      setBusy(true);
      try {
        const blob = await ctx.api.frame(ctx.bp, ctx.t, original, SAMPLE_W);
        const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none" });      // measure the engine's real values, not the browser's colour-managed ones
        if (my !== seq.current) return;
        c.width = bmp.width; c.height = bmp.height; g.drawImage(bmp, 0, 0);
        const d = g.getImageData(0, 0, c.width, c.height); setImg({ data: d.data, width: d.width, height: d.height }); setError(null);
      } catch (e: any) { if (my === seq.current) setError(e.message ?? String(e)); }
      finally { if (my === seq.current) setBusy(false); }
    }, 250);
    return () => clearTimeout(h);
  }, [ctx.api, ctx.bp, Math.round(ctx.t * 1000), original, live, !!ctx.video]);
  return { img, busy, error, live };
}

// ------------------------------------------------------------------------------------------------ drawing
const GREEN: [number, number, number] = [110, 255, 150];
/** paints a count grid (cols x rows, row 0 = top) into an offscreen RGBA canvas with phosphor-like brightness */
function gridImage(counts: Uint32Array, cols: number, rows: number, k: number, rgb: [number, number, number], flipY: boolean): HTMLCanvasElement {
  const c = document.createElement("canvas"); c.width = cols; c.height = rows;
  const g = c.getContext("2d")!, im = g.createImageData(cols, rows), d = im.data;
  for (let col = 0; col < cols; col++) for (let l = 0; l < rows; l++) {
    const n = counts[col * rows + l]; if (!n) continue;
    const a = 1 - Math.exp(-n * k), y = flipY ? rows - 1 - l : l, i = (y * cols + col) * 4;
    d[i] = rgb[0]; d[i + 1] = rgb[1]; d[i + 2] = rgb[2]; d[i + 3] = Math.round(255 * a);
  }
  g.putImageData(im, 0, 0); return c;
}
function drawWaveGraticule(g: CanvasRenderingContext2D, x0: number, w: number, h: number, labels: boolean) {
  g.lineWidth = 1; g.font = "10px system-ui"; g.fillStyle = "#8b93a3";
  for (let p = 0; p <= 100; p += 10) {
    const y = Math.round(h - (p / 100) * (h - 1)) + 0.5;
    g.strokeStyle = p % 50 === 0 ? "#ffffff40" : "#ffffff18"; g.beginPath(); g.moveTo(x0, y); g.lineTo(x0 + w, y); g.stroke();
    if (labels && p % 20 === 0) g.fillText(String(p), 2, Math.min(h - 3, y + 3));
  }
}
function drawWaveform(c: HTMLCanvasElement, img: sc.Img, kind: "waveform" | "parade", gain: number, grat: boolean) {
  const g = c.getContext("2d")!, W = c.width, H = c.height; g.fillStyle = "#05070a"; g.fillRect(0, 0, W, H);
  const lab = 22, x0 = grat ? lab : 0, pw = W - x0;
  g.imageSmoothingEnabled = false;
  const k = (gain * 9) / img.height;
  if (kind === "waveform") {
    const cols = Math.min(pw, img.width); g.drawImage(gridImage(sc.waveform(img, cols, "y"), cols, 256, k, GREEN, true), x0, 0, pw, H);
  } else {
    const cw = Math.floor(pw / 3), cols = Math.min(cw, img.width);
    (["r", "g", "b"] as const).forEach((ch, i) => g.drawImage(gridImage(sc.waveform(img, cols, ch), cols, 256, k, ch === "r" ? [255, 90, 90] : ch === "g" ? [90, 255, 120] : [100, 150, 255], true), x0 + i * cw, 0, cw, H));
    if (grat) { g.strokeStyle = "#ffffff30"; for (let i = 1; i < 3; i++) { g.beginPath(); g.moveTo(x0 + i * cw + 0.5, 0); g.lineTo(x0 + i * cw + 0.5, H); g.stroke(); } }
  }
  if (grat) drawWaveGraticule(g, x0, pw, H, true);
}
function drawVector(c: HTMLCanvasElement, img: sc.Img, gain: number, grat: boolean, skin: boolean) {
  const g = c.getContext("2d")!, S = c.width; g.fillStyle = "#05070a"; g.fillRect(0, 0, S, S);
  const n = 256, counts = sc.vectorscope(img, n), k = gain * 0.55;
  const cv = document.createElement("canvas"); cv.width = n; cv.height = n; const cg = cv.getContext("2d")!, im = cg.createImageData(n, n);
  for (let i = 0; i < n * n; i++) { const v = counts[i]; if (!v) continue; im.data[i * 4] = 140; im.data[i * 4 + 1] = 255; im.data[i * 4 + 2] = 180; im.data[i * 4 + 3] = Math.round(255 * (1 - Math.exp(-v * k))); }
  cg.putImageData(im, 0, 0); g.imageSmoothingEnabled = false; g.drawImage(cv, 0, 0, S, S);
  if (!grat) return;
  const C = S / 2, R = S * 0.5;
  g.lineWidth = 1; g.strokeStyle = "#ffffff30"; g.beginPath(); g.arc(C, C, R - 0.5, 0, Math.PI * 2); g.stroke();
  g.strokeStyle = "#ffffff18"; g.beginPath(); g.arc(C, C, (R - 0.5) * 0.5, 0, Math.PI * 2); g.stroke();
  g.beginPath(); g.moveTo(C, 0); g.lineTo(C, S); g.moveTo(0, C); g.lineTo(S, C); g.stroke();
  g.font = "bold 10px system-ui"; g.textAlign = "center";
  for (const tg of sc.TARGETS) {
    const q = sc.ycbcr(tg.rgb[0], tg.rgb[1], tg.rgb[2]), x = (0.5 + q.cb) * S, y = (0.5 - q.cr) * S;
    g.strokeStyle = "#ffd36a"; g.strokeRect(x - 6, y - 6, 12, 12); g.fillStyle = "#ffd36a"; g.fillText(tg.name, x, y + (y < C ? -10 : 18));
  }
  if (skin) {
    const a = (sc.SKIN_ANGLE * Math.PI) / 180; g.strokeStyle = "#ff9d5c"; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(C, C); g.lineTo(C + Math.cos(a) * R, C - Math.sin(a) * R); g.stroke(); g.setLineDash([]);
    g.fillStyle = "#ff9d5c"; g.fillText("skin", C + Math.cos(a) * (R - 18), C - Math.sin(a) * (R - 18) - 4);
  }
  g.textAlign = "start";
}
function drawHistogram(c: HTMLCanvasElement, img: sc.Img, log: boolean, grat: boolean) {
  const g = c.getContext("2d")!, W = c.width, H = c.height; g.fillStyle = "#05070a"; g.fillRect(0, 0, W, H);
  const h = sc.histogram(img), f = (v: number) => (log ? Math.log1p(v) : v);
  const max = Math.max(1, ...[h.r, h.g, h.b, h.y].map(a => Math.max(...Array.from(a).map(f))));
  if (grat) { g.strokeStyle = "#ffffff1c"; for (let i = 0; i <= 4; i++) { const x = Math.round((i / 4) * (W - 1)) + 0.5; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); } }
  g.globalCompositeOperation = "lighter";
  const area = (a: Uint32Array, col: string) => { g.fillStyle = col; g.beginPath(); g.moveTo(0, H); for (let i = 0; i < 256; i++) g.lineTo((i / 255) * W, H - (f(a[i]) / max) * (H - 4)); g.lineTo(W, H); g.fill(); };
  area(h.r, "#ff3b3b88"); area(h.g, "#35d46b88"); area(h.b, "#4a7dff88");
  g.globalCompositeOperation = "source-over"; g.strokeStyle = "#ffffffcc"; g.lineWidth = 1; g.beginPath();
  for (let i = 0; i < 256; i++) { const x = (i / 255) * W, y = H - (f(h.y[i]) / max) * (H - 4); i ? g.lineTo(x, y) : g.moveTo(x, y); } g.stroke();
}

// ------------------------------------------------------------------------------------------------ panel
export function ScopesPanel({ ctx }: { ctx: Ctx }) {
  const [kind, setKind] = useState<Kind>(() => load("kind", "waveform"));
  const [original, setOriginal] = useState<boolean>(() => load("original", false));
  const [gain, setGain] = useState<number>(() => load("gain", 1));
  const [grat, setGrat] = useState<boolean>(() => load("grat", true));
  const [skin, setSkin] = useState<boolean>(() => load("skin", true));
  const [log, setLog] = useState<boolean>(() => load("log", false));
  useEffect(() => { save("kind", kind); save("original", original); save("gain", gain); save("grat", grat); save("skin", skin); save("log", log); }, [kind, original, gain, grat, skin, log]);
  const { img, busy, error, live } = useScopeImage(ctx, original);
  const box = useRef<HTMLDivElement>(null), cv = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(300);
  useEffect(() => { const ro = new ResizeObserver(e => setWidth(Math.max(200, Math.floor(e[0].contentRect.width)))); if (box.current) ro.observe(box.current); return () => ro.disconnect(); }, []);
  const st = useMemo(() => (img ? sc.stats(img) : null), [img]);
  const size = kind === "vector" ? Math.min(width, 340) : width, height = kind === "vector" ? size : kind === "histogram" ? 150 : 230;
  useEffect(() => {
    const c = cv.current; if (!c || !img) return;
    c.width = size; c.height = height;
    if (kind === "waveform" || kind === "parade") drawWaveform(c, img, kind, gain, grat);
    else if (kind === "vector") drawVector(c, img, gain, grat, skin);
    else drawHistogram(c, img, log, grat);
  }, [img, kind, gain, grat, skin, log, size, height]);
  return (
    <>
      <h3>Scopes</h3>
      <p className="muted">Measured from the {live ? "playing video" : original ? "original frame" : "graded frame"} at the playhead (BT.709).</p>
      <div className="seg scope-seg" role="tablist" aria-label="Scope type">{KINDS.map(k => <button key={k.id} className={kind === k.id ? "on" : ""} role="tab" aria-selected={kind === k.id} onClick={() => setKind(k.id)}>{k.label}</button>)}</div>
      <div className="scope-opts">
        <label><input type="radio" name="scope-src" checked={!original} onChange={() => setOriginal(false)} /> Graded</label>
        <label><input type="radio" name="scope-src" checked={original} onChange={() => setOriginal(true)} /> Original</label>
        <label className="grow">Intensity <input type="range" min={0.3} max={3} step={0.1} value={gain} onChange={e => setGain(parseFloat(e.target.value))} aria-label="Scope intensity" /></label>
        <label><input type="checkbox" checked={grat} onChange={e => setGrat(e.target.checked)} /> Graticule</label>
        {kind === "vector" && <label><input type="checkbox" checked={skin} onChange={e => setSkin(e.target.checked)} /> Skin line</label>}
        {kind === "histogram" && <label><input type="checkbox" checked={log} onChange={e => setLog(e.target.checked)} /> Log</label>}
      </div>
      <div ref={box} className="scope-box">
        {!ctx.video ? <Empty icon="scope" title="No video">Import a video to see its scopes.</Empty>
          : <><canvas ref={cv} className="scope-canvas" width={size} height={height} role="img" aria-label={`${KINDS.find(k => k.id === kind)!.label} of the current frame`} />
            {!img && !error && <div className="scope-wait">Measuring…</div>}
            {busy && img && <div className="spin" title="Updating" />}</>}
      </div>
      {error && <div className="issue warning">{error}</div>}
      {st && <div className="scope-stats" aria-live="off">
        <span className={st.clipHighPct > 1 ? "warn" : ""}>Clipped highlights <b>{st.clipHighPct.toFixed(1)}%</b></span>
        <span className={st.clipLowPct > 1 ? "warn" : ""}>Crushed blacks <b>{st.clipLowPct.toFixed(1)}%</b></span>
        <span>Average luma <b>{(st.meanY * 100).toFixed(0)}%</b></span>
      </div>}
      <p className="muted scope-help">{kind === "waveform" ? "Brightness (up) across the picture (left to right). Keep whites near the top without a flat line at 100%; blacks near 0." :
        kind === "parade" ? "Red, green and blue levels side by side. Matching heights in neutral areas = a balanced white balance." :
        kind === "vector" ? "Colour hue (angle) and saturation (distance). Neutrals sit at the centre; skin tones gather around the dashed skin line; yellow boxes mark 75% colour bars." :
        "How many pixels have each level, left = black, right = white. Spikes at either end mean clipping."}</p>
    </>);
}
