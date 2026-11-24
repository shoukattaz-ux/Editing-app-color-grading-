// Colour-wheel mapping: an RGB triplet [r,g,b] (each -1..1, as stored in lift / gamma_rgb / gain) <-> master + hue + amount.
//   triplet = master*(1,1,1) + amount*(cos(h), cos(h-120deg), cos(h-240deg))
// The chroma part has zero mean, so "master" is exactly the mean of the triplet (equal changes of R,G,B = brightness) and the
// rest is a pure colour cast whose direction is the hue (red at 0, green at 120, blue at 240 - the same angle as the colour added).
export type Triplet = [number, number, number];
export interface WheelSpec { param: "lift" | "gamma_rgb" | "gain"; label: string; sub: string; maxChroma: number; masterRange: number }
/** wheel rim = maxChroma; master slider spans +-masterRange. Chosen so that the edge of each wheel is a strong but usable grade. */
export const WHEELS: WheelSpec[] = [
  { param: "lift", label: "Lift", sub: "shadows", maxChroma: 0.15, masterRange: 0.3 },
  { param: "gamma_rgb", label: "Gamma", sub: "midtones", maxChroma: 0.4, masterRange: 0.6 },
  { param: "gain", label: "Gain", sub: "highlights", maxChroma: 0.35, masterRange: 0.5 },
];
const S3 = Math.sqrt(3), RAD = Math.PI / 180;

export function decompose(rgb: readonly number[]): { master: number; hue: number; amount: number } {
  const master = (rgb[0] + rgb[1] + rgb[2]) / 3;
  const r = rgb[0] - master, g = rgb[1] - master, b = rgb[2] - master;
  const u = r - (g + b) / 2, w = (S3 / 2) * (g - b);
  return { master, hue: ((Math.atan2(w, u) / RAD) + 360) % 360, amount: Math.hypot(u, w) / 1.5 };
}
export function compose(master: number, hue: number, amount: number): Triplet {
  const t = hue * RAD;
  return [master + amount * Math.cos(t), master + amount * Math.cos(t - 120 * RAD), master + amount * Math.cos(t - 240 * RAD)];
}
const clamp1 = (v: number) => Math.min(1, Math.max(-1, v));
export const clampTriplet = (t: readonly number[]): Triplet => [clamp1(t[0]), clamp1(t[1]), clamp1(t[2])];
export const round4 = (t: readonly number[]): Triplet => [Math.round(t[0] * 1e4) / 1e4, Math.round(t[1] * 1e4) / 1e4, Math.round(t[2] * 1e4) / 1e4];

/** puck position on the wheel: x right, y UP, rim = 1. r may exceed 1 for out-of-range values (drawn on the rim). */
export function toPuck(rgb: readonly number[], spec: WheelSpec): { x: number; y: number; r: number; master: number } {
  const d = decompose(rgb), r = d.amount / spec.maxChroma;
  return { x: r * Math.cos(d.hue * RAD), y: r * Math.sin(d.hue * RAD), r, master: d.master };
}
/** inverse: a puck position (clamped to the rim) and the master value */
export function fromPuck(x: number, y: number, master: number, spec: WheelSpec): Triplet {
  const r = Math.min(1, Math.hypot(x, y)), hue = ((Math.atan2(y, x) / RAD) + 360) % 360;
  return round4(clampTriplet(compose(master, hue, r * spec.maxChroma)));
}
/** keep the colour cast, change only the brightness (master) */
export function withMaster(rgb: readonly number[], master: number): Triplet {
  const d = decompose(rgb); return round4(clampTriplet(compose(master, d.hue, d.amount)));
}
export const isNeutral = (t: readonly number[] | null | undefined) => !t || (Math.abs(t[0]) < 1e-9 && Math.abs(t[1]) < 1e-9 && Math.abs(t[2]) < 1e-9);
export const describe = (rgb: readonly number[], spec: WheelSpec) => {
  const d = decompose(rgb), pct = Math.round((d.amount / spec.maxChroma) * 100);
  return pct < 1 ? `no colour cast, master ${d.master.toFixed(2)}` : `hue ${Math.round(d.hue)} degrees, ${pct} percent, master ${d.master.toFixed(2)}`;
};
