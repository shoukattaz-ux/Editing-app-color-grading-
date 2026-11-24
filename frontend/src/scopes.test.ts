import { describe, it, expect } from "vitest";
import * as s from "./scopes";
import vec from "../../shared/scope_vectors.json";

const img = () => s.testImage(vec.image.width, vec.image.height);

describe("scopes reproduce the independent NumPy reference exactly", () => {
  it("histogram (R, G, B, luma)", () => {
    const h = s.histogram(img());
    for (const k of ["r", "g", "b", "y"] as const) expect(Array.from(h[k]), k).toEqual((vec.histogram as any)[k]);
  });
  it("waveform of luma and of each channel", () => {
    for (const ch of ["y", "r", "g", "b"] as const) expect(Array.from(s.waveform(img(), vec.waveform_cols, ch)), ch).toEqual((vec.waveform as any)[ch]);
  });
  it("vectorscope bins", () => { expect(Array.from(s.vectorscope(img(), vec.vectorscope_n))).toEqual(vec.vectorscope); });
  it("stats", () => {
    const st = s.stats(img());
    expect(st.clipHighPct).toBeCloseTo(vec.stats.clipHighPct, 9); expect(st.clipLowPct).toBeCloseTo(vec.stats.clipLowPct, 9); expect(st.meanY).toBeCloseTo(vec.stats.meanY, 9);
  });
  it("Y/Cb/Cr and polar position of reference colours", () => {
    for (const c of vec.colors) {
      const q = s.ycbcr(c.rgb[0], c.rgb[1], c.rgb[2]), p = s.vectorPoint(c.rgb[0], c.rgb[1], c.rgb[2]);
      expect(q.y, c.name).toBeCloseTo(c.y, 12); expect(q.cb, c.name).toBeCloseTo(c.cb, 12); expect(q.cr, c.name).toBeCloseTo(c.cr, 12);
      if (c.radius > 1e-6) expect(p.angle, c.name).toBeCloseTo(c.angle, 9);
      expect(p.radius, c.name).toBeCloseTo(c.radius, 9);
    }
  });
});

describe("scope behaviour on simple images", () => {
  it("mid grey: one histogram bin, one waveform row, vectorscope dead centre", () => {
    const g = s.solid(16, 16, 128, 128, 128);
    const h = s.histogram(g); expect(h.y[128]).toBe(256); expect(h.r[128]).toBe(256);
    const w = s.waveform(g, 4, "y"); for (let c = 0; c < 4; c++) expect(w[c * 256 + 128]).toBe(64);
    const v = s.vectorscope(g, 64); expect(v[32 * 64 + 32]).toBe(256);
  });
  it("a horizontal ramp draws a diagonal in the waveform", () => {
    const w = 64, data = new Uint8ClampedArray(w * 4 * 4);
    for (let y = 0; y < 4; y++) for (let x = 0; x < w; x++) { const i = (y * w + x) * 4, v = Math.round((x / (w - 1)) * 255); data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255; }
    const wf = s.waveform({ data, width: w, height: 4 }, w, "y");
    let prev = -1; for (let col = 0; col < w; col++) { let row = -1; for (let l = 0; l < 256; l++) if (wf[col * 256 + l]) row = l; expect(row).toBeGreaterThan(prev); prev = row; }
  });
  it("pure primaries land on their standard vectorscope angles; neutrals at the centre", () => {
    const a = (r: number, g: number, b: number) => s.vectorPoint(r, g, b);
    expect(a(255, 0, 0).angle).toBeCloseTo(102.9, 0); expect(a(255, 0, 255).angle).toBeCloseTo(49.7, 0); expect(a(0, 0, 255).angle).toBeCloseTo(354.8, 0);
    expect(a(77, 77, 77).radius).toBeLessThan(1e-9);
  });
  it("skin tones cluster around the skin line: a band of roughly +-25 degrees, centred close to it", () => {
    // measured spread of common skin swatches is ~115-144 degrees; warmer/tanned skin sits counter-clockwise of the 123 degree reference line
    const swatches = [[247, 225, 205], [224, 172, 105], [198, 134, 66], [141, 85, 36], [91, 54, 32], [255, 219, 172], [240, 200, 190], [210, 160, 120], [110, 70, 50], [146, 128, 119]];
    const off = swatches.map(c => s.vectorPoint(c[0], c[1], c[2]).angle - s.SKIN_ANGLE);
    for (const o of off) expect(Math.abs(o)).toBeLessThan(25);
    const sorted = [...off].sort((a, b) => a - b), median = sorted[Math.floor(sorted.length / 2)];
    expect(Math.abs(median)).toBeLessThan(15);
    expect(Math.abs(s.vectorPoint(146, 128, 119).angle - s.SKIN_ANGLE)).toBeLessThan(5);        // a typical un-warmed webcam skin patch lies on the line
  });
  it("clipping is reported", () => {
    const d = new Uint8ClampedArray(4 * 4 * 4); for (let i = 0; i < 16; i++) { const hi = i < 4; d[i * 4] = hi ? 255 : 100; d[i * 4 + 1] = hi ? 255 : 100; d[i * 4 + 2] = i >= 12 ? 0 : 100; d[i * 4 + 3] = 255; }
    const st = s.stats({ data: d, width: 4, height: 4 });
    expect(st.clipHighPct).toBe(25); expect(st.clipLowPct).toBe(25);
  });
  it("targets are 75% bars", () => { expect(s.vectorPoint(191, 0, 0).radius).toBeCloseTo(0.75 * s.vectorPoint(255, 0, 0).radius, 1); });
});
