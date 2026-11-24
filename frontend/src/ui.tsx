import React from "react";

const P: Record<string, string> = {
  play: "M8 5v14l11-7z", pause: "M6 5h4v14H6zM14 5h4v14h-4z", stop: "M6 6h12v12H6z", prev: "M15 6l-9 6 9 6zM17 6h2v12h-2z", next: "M9 6l9 6-9 6zM5 6h2v12H5z",
  undo: "M12 5a7 7 0 016.6 4.7l-1.9.6A5 5 0 007.4 11H11l-4 4-4-4h3.2A7 7 0 0112 5z", redo: "M12 5a7 7 0 00-6.6 4.7l1.9.6A5 5 0 0116.6 11H13l4 4 4-4h-3.2A7 7 0 0012 5z",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6", folder: "M3 6h7l2 2h9v11H3z", film: "M4 4h16v16H4zM8 4v16M16 4v16M4 9h4M4 15h4M16 9h4M16 15h4",
  bolt: "M13 2L4 14h6l-1 8 9-12h-6z", export: "M12 3v12M7 8l5-5 5 5M5 21h14", trash: "M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13", copy: "M8 8h11v12H8zM5 16V4h11",
  split: "M12 3v18M5 8l4 4-4 4M19 8l-4 4 4 4", full: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5", eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z",
  reset: "M4 12a8 8 0 108-8M4 4v6h6", image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4", color: "M12 3a9 9 0 100 18c1.5 0 2-1 1.5-2s0-2 1.5-2h2a3 3 0 003-3A9 9 0 0012 3z",
  check: "M5 13l4 4L19 7", x: "M6 6l12 12M18 6L6 18", plus: "M12 5v14M5 12h14", wand: "M4 20L16 8M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 11l.7 1.3L21 13l-1.3.7L19 15l-.7-1.3L17 13l1.3-.7z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5", text: "M5 6V4h14v2M12 4v16M9 20h6", audio: "M4 10v4h4l5 4V6L8 10zM16 9a4 4 0 010 6", warn: "M12 3l10 18H2zM12 10v5M12 18v.5",
  compare: "M4 4h16v16H4zM12 4v16", scope: "M3 12h3l3-7 4 14 3-7h5", diamond: "M12 3l7 9-7 9-7-9z", clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2", chevL: "M15 6l-6 6 6 6", chevR: "M9 6l6 6-6 6", scissors: "M6 6a2 2 0 100 .01M6 18a2 2 0 100 .01M8 7l12 10M8 17L20 7", skipOff: "M4 4l16 16M12 3a9 9 0 019 9",
  gear: "M12 8.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM19.4 13a7.6 7.6 0 000-2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 00-1.7-1L15 3.4h-4l-.3 2.6a7.6 7.6 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 000 2l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 001.7 1l.3 2.6h4l.3-2.6a7.6 7.6 0 001.7-1l2.4 1 2-3.4z",
};
export const Icon = ({ n, size = 16 }: { n: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={P[n] ?? P.film} />
  </svg>
);

export const Btn = ({ icon, label, onClick, disabled, kind, title, active }: { icon?: string; label?: string; onClick?: () => void; disabled?: boolean; kind?: "primary" | "danger" | "ghost"; title?: string; active?: boolean }) => (
  <button className={`btn ${kind ?? ""} ${active ? "active" : ""}`} onClick={onClick} disabled={disabled} title={title ?? label} aria-label={title ?? label}>
    {icon && <Icon n={icon} />}{label && <span>{label}</span>}
  </button>
);

export const Empty = ({ icon, title, children }: { icon: string; title: string; children?: React.ReactNode }) => (
  <div className="empty"><Icon n={icon} size={28} /><h4>{title}</h4><p>{children}</p></div>
);

export const fmtSec = (s: number) => `${s.toFixed(2)}s`;
export function fmtEta(s: number | null) { if (s == null || !isFinite(s)) return "—"; const m = Math.floor(s / 60); return m ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`; }

/** Output resolution choices matching the source orientation (portrait phone video gets portrait sizes). */
export function resolutionOptions(video: { width: number; height: number } | null, current?: string): string[] {
  const land = ["1280x720", "1920x1080", "2560x1440", "3840x2160"];
  const portrait = video ? video.height > video.width : false;
  const list = ["source", ...(portrait ? land.map(r => r.split("x").reverse().join("x")) : land)];
  if (video) {
    const two = `${Math.round(video.width * 2 / 2) * 2}x${Math.round(video.height * 2 / 2) * 2}`;
    if (!list.includes(two) && video.width * 2 <= 7680 && video.height * 2 <= 7680) list.splice(1, 0, two);
  }
  if (current && !list.includes(current)) list.push(current);
  return list;
}
