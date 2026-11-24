// Video scopes: the maths only (no drawing). BT.709 definitions, verified against an independent NumPy implementation
// (shared/scope_vectors.json) and against FFmpeg's own YCbCr conversion (backend/tests/test_scopes.py).
export interface Img { data: ArrayLike<number>; width: number; height: number }   // RGBA bytes, row-major
export const KR = 0.2126, KG = 0.7152, KB = 0.0722, CB_DIV = 1.8556, CR_DIV = 1.5748;
/** where skin tones sit on a vectorscope (Cb right, Cr up), degrees counter-clockwise from +Cb */
export const SKIN_ANGLE = 123;

/** r,g,b 0..255 -> y 0..1, cb/cr -0.5..0.5 (full range) */
export function ycbcr(r: number, g: number, b: number) {
  const y = (KR * r + KG * g + KB * b) / 255;
  return { y, cb: (b / 255 - y) / CB_DIV, cr: (r / 255 - y) / CR_DIV };
}
export const level = (y: number) => Math.floor(y * 255 + 0.5);          // 0..255 bin of a luma value (round half up)
/** polar position of a colour on the vectorscope: angle in degrees [0,360), radius 1 = the +-0.5 Cb/Cr limit */
export function vectorPoint(r: number, g: number, b: number) {
  const { cb, cr } = ycbcr(r, g, b);
  return { angle: ((Math.atan2(cr, cb) * 180) / Math.PI + 360) % 360, radius: Math.hypot(cb, cr) / 0.5 };
}

export interface Histogram { r: Uint32Array; g: Uint32Array; b: Uint32Array; y: Uint32Array }
export function histogram(img: Img): Histogram {
  const h: Histogram = { r: new Uint32Array(256), g: new Uint32Array(256), b: new Uint32Array(256), y: new Uint32Array(256) };
  const d = img.data, n = img.width * img.height;
  for (let i = 0; i < n; i++) {
    const r = d[i * 4], g = d[i * 4 + 1], b = d[i * 4 + 2];
    h.r[r]++; h.g[g]++; h.b[b]++; h.y[level(ycbcr(r, g, b).y)]++;
  }
  return h;
}

export type WaveChannel = "y" | "r" | "g" | "b";
/** counts[col * 256 + level]: how many pixels of the image columns mapped to `col` have that level */
export function waveform(img: Img, cols: number, ch: WaveChannel): Uint32Array {
  const out = new Uint32Array(cols * 256), d = img.data, { width: w, height: h } = img;
  for (let x = 0; x < w; x++) {
    const col = Math.floor((x * cols) / w);
    for (let y = 0; y < h; y++) {
      const i = (y * w + x) * 4, r = d[i], g = d[i + 1], b = d[i + 2];
      const v = ch === "y" ? level(ycbcr(r, g, b).y) : ch === "r" ? r : ch === "g" ? g : b;
      out[col * 256 + v]++;
    }
  }
  return out;
}
/** counts[row * n + col]: Cb to the right, Cr upward (row 0 = +Cr), an n x n plot covering +-0.5 */
export function vectorscope(img: Img, n: number): Uint32Array {
  const out = new Uint32Array(n * n), d = img.data, px = img.width * img.height;
  for (let i = 0; i < px; i++) {
    const { cb, cr } = ycbcr(d[i * 4], d[i * 4 + 1], d[i * 4 + 2]);
    const bx = Math.min(n - 1, Math.max(0, Math.floor((cb + 0.5) * n))), by = Math.min(n - 1, Math.max(0, Math.floor((0.5 - cr) * n)));
    out[by * n + bx]++;
  }
  return out;
}
export interface ScopeStats { clipHighPct: number; clipLowPct: number; meanY: number }
/** clipHigh/Low: percent of pixels where any channel is at 255 / all-channel minimum is 0 */
export function stats(img: Img): ScopeStats {
  const d = img.data, n = img.width * img.height;
  let hi = 0, lo = 0, sy = 0;
  for (let i = 0; i < n; i++) {
    const r = d[i * 4], g = d[i * 4 + 1], b = d[i * 4 + 2];
    if (Math.max(r, g, b) === 255) hi++;
    if (Math.min(r, g, b) === 0) lo++;
    sy += ycbcr(r, g, b).y;
  }
  return { clipHighPct: (100 * hi) / n, clipLowPct: (100 * lo) / n, meanY: sy / n };
}
/** the deterministic image used by the shared vectors */
export function testImage(w: number, h: number): Img {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4;
    data[i] = (x * 37 + y * 11) % 256; data[i + 1] = (x * 5 + y * 91) % 256; data[i + 2] = (x * 17 + y * 3 + x * y) % 256; data[i + 3] = 255;
  }
  return { data, width: w, height: h };
}
export const solid = (w: number, h: number, r: number, g: number, b: number): Img => {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) { data[i * 4] = r; data[i * 4 + 1] = g; data[i * 4 + 2] = b; data[i * 4 + 3] = 255; }
  return { data, width: w, height: h };
};
/** the 75% colour-bar targets of the vectorscope graticule */
export const TARGETS: { name: string; rgb: [number, number, number] }[] = [
  { name: "R", rgb: [191, 0, 0] }, { name: "Mg", rgb: [191, 0, 191] }, { name: "B", rgb: [0, 0, 191] },
  { name: "Cy", rgb: [0, 191, 191] }, { name: "G", rgb: [0, 191, 0] }, { name: "Yl", rgb: [191, 191, 0] },
];
