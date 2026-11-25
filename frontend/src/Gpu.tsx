import React, { useEffect, useState } from "react";
import type { Api, GpuStatus } from "./api";

/** how the chip looks for a status from the engine (pure, tested) */
export function gpuView(s: GpuStatus | null): { cls: "active" | "ready" | "cpu" | "unknown"; text: string } {
  if (!s) return { cls: "unknown", text: "GPU …" };
  return { cls: s.state === "active" ? "active" : s.state === "ready" ? "ready" : "cpu", text: s.label };
}

export function GpuChip({ api, working }: { api: Api; working: boolean }) {
  const [s, setS] = useState<GpuStatus | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (document.visibilityState === "visible") { try { const r = await api.gpu(); if (alive) setS(r); } catch { /* engine busy: keep the last value */ } }
      if (alive) timer = setTimeout(tick, working ? 1200 : 2500);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [api, working]);
  const v = gpuView(s);
  return (
    <span className="menu-wrap">
      <button className={`gpuchip ${v.cls}`} onClick={() => setOpen(o => !o)} title={s?.detail ?? "Checking the graphics card…"} aria-label={`Graphics card status: ${v.text}`}>
        <i className="dot" />{v.text}
      </button>
      {open && s && <div className="menu gpupop" role="dialog" onMouseLeave={() => setOpen(false)}>
        <b>{s.adapter ?? "No graphics card in use"}</b>
        <p>{s.detail}</p>
        {s.measurable && s.percent !== null && <div className="gpubar" aria-hidden><span style={{ width: `${Math.min(100, s.percent)}%` }} /></div>}
        {s.encoder && <p className="muted">Export encoder: {s.encoder}{s.encoder_on_gpu ? " (GPU)" : " (processor)"}</p>}
      </div>}
    </span>);
}
