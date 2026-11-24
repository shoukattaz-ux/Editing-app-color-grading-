import { describe, it, expect } from "vitest";
import * as w from "./colorwheel";

const lift = w.WHEELS[0], gamma = w.WHEELS[1];

describe("wheel mapping is a bijection", () => {
  it("compose then decompose returns the same master, hue and amount", () => {
    for (const master of [-0.3, 0, 0.12, 0.5]) for (const hue of [0, 17, 90, 120, 200, 240, 333]) for (const amount of [0, 0.02, 0.15, 0.4]) {
      const d = w.decompose(w.compose(master, hue, amount));
      expect(d.master).toBeCloseTo(master, 9); expect(d.amount).toBeCloseTo(amount, 9);
      if (amount > 1e-9) expect(d.hue).toBeCloseTo(hue, 6);
    }
  });
  it("any triplet survives triplet -> decompose -> compose", () => {
    for (const t of [[0.1, -0.2, 0.3], [0.5, 0, 0], [-0.4, 0.2, 0.2], [0, 0, 0], [0.05, 0.05, 0.05], [1, -1, 0.5]]) {
      const d = w.decompose(t), back = w.compose(d.master, d.hue, d.amount);
      t.forEach((v, i) => expect(back[i]).toBeCloseTo(v, 9));
    }
  });
  it("master is exactly the mean, and the cast has zero mean", () => {
    const t = [0.3, -0.1, 0.2], d = w.decompose(t), c = w.compose(0, d.hue, d.amount);
    expect(d.master).toBeCloseTo(0.4 / 3, 12); expect(c[0] + c[1] + c[2]).toBeCloseTo(0, 12);
  });
  it("hue is the colour that gets added: red at 0, green at 120, blue at 240", () => {
    const at = (h: number) => w.compose(0, h, 0.2);
    expect(at(0)[0]).toBeGreaterThan(Math.max(at(0)[1], at(0)[2])); expect(at(120)[1]).toBeGreaterThan(Math.max(at(120)[0], at(120)[2])); expect(at(240)[2]).toBeGreaterThan(Math.max(at(240)[0], at(240)[1]));
  });
  it("a neutral triplet has no cast and sits at the centre", () => {
    const p = w.toPuck([0.2, 0.2, 0.2], gamma); expect(p.r).toBeCloseTo(0, 12); expect(p.master).toBeCloseTo(0.2, 12);
    expect(w.isNeutral([0, 0, 0])).toBe(true); expect(w.isNeutral(null)).toBe(true); expect(w.isNeutral([0, 0.01, 0])).toBe(false);
  });
});

describe("puck <-> triplet", () => {
  it("round trips and clamps to the rim", () => {
    const t = w.fromPuck(0.5, -0.3, 0.05, gamma), p = w.toPuck(t, gamma);
    expect(p.x).toBeCloseTo(0.5, 3); expect(p.y).toBeCloseTo(-0.3, 3); expect(p.master).toBeCloseTo(0.05, 4);
    const far = w.toPuck(w.fromPuck(5, 0, 0, lift), lift); expect(far.r).toBeCloseTo(1, 3);           // dragging beyond the wheel stops at the rim
  });
  it("stays inside the parameter limits for every wheel at its extremes", () => {
    for (const spec of w.WHEELS) for (const h of [0, 60, 120, 180, 240, 300]) for (const m of [-spec.masterRange, spec.masterRange]) {
      const t = w.fromPuck(Math.cos(h * Math.PI / 180), Math.sin(h * Math.PI / 180), m, spec);
      for (const v of t) expect(Math.abs(v)).toBeLessThanOrEqual(1);
    }
  });
  it("an out-of-range value from a blueprint is reported beyond the rim without being altered", () => {
    expect(w.toPuck([0.9, 0, 0], lift).r).toBeGreaterThan(1);
  });
  it("withMaster keeps the cast and changes only the brightness", () => {
    const t = w.fromPuck(0.4, 0.2, 0, gamma), t2 = w.withMaster(t, 0.3);
    const a = w.decompose(t), b = w.decompose(t2);
    expect(b.master).toBeCloseTo(0.3, 3); expect(b.hue).toBeCloseTo(a.hue, 1); expect(b.amount).toBeCloseTo(a.amount, 3);
  });
  it("describes the setting in words for screen readers", () => {
    expect(w.describe([0, 0, 0], lift)).toContain("no colour cast"); expect(w.describe(w.compose(0, 240, 0.075), lift)).toContain("hue 240");
  });
});
