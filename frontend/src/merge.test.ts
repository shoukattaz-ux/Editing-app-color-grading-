import { describe, expect, it } from "vitest";
import { mergeBlueprint } from "./merge";
import type { Blueprint, Op, Schema } from "./model";

const op = (id: string, type: string, extra: Partial<Op> = {}): Op => ({ id, type, start: 0, parameters: {}, ...extra });
const bp = (operations: Op[], extra: Partial<Blueprint> = {}): Blueprint => ({ blueprint_version: "1.0", assets: [], operations, ...extra });
const schema = { operations: { background_replacement: { parameters: { asset_id: { type: "asset" } } } } } as unknown as Schema;
const OPT = { applyOutput: false };

describe("mergeBlueprint", () => {
  const existing = bp([op("key", "background_removal"), op("bg", "background_replacement", { depends_on: ["key"], parameters: { asset_id: "sea" } })],
    { assets: [{ id: "sea", type: "image", path: "assets/sea.png" }], output: { codec: "h264", crf: 20 }, clips: [{ id: "a", start: 0, end: 4 }, { id: "b", start: 4, end: 9 }] });

  it("keeps every existing operation, asset, cut and output setting; appends the new colour grade", () => {
    const r = mergeBlueprint(existing, bp([op("grade", "color_grading", { parameters: { contrast: 1.2 } })], { output: { codec: "h265", crf: 17 } }), schema, OPT, 9);
    expect(r.bp.operations.map(o => o.id)).toEqual(["key", "bg", "grade"]);
    expect(r.bp.assets).toEqual(existing.assets);
    expect(r.bp.clips).toEqual(existing.clips);
    expect(r.bp.output).toEqual({ codec: "h264", crf: 20 });
    expect(r.notes.join(" ")).toContain("output settings were not applied");
    expect(r.bp.operations[2].parameters).toEqual({ contrast: 1.2 });
  });
  it("can take the blueprint's output settings when asked", () => {
    const r = mergeBlueprint(existing, bp([], { output: { crf: 17 } }), schema, { applyOutput: true }, 9);
    expect(r.bp.output).toEqual({ codec: "h264", crf: 17 });
  });
  it("renames clashing ids and keeps the links between the new operations", () => {
    const r = mergeBlueprint(existing, bp([op("key", "chroma_key"), op("look", "studio_look", { depends_on: ["key"] })]), schema, OPT, 9);
    const ids = r.bp.operations.map(o => o.id);
    expect(ids).toEqual(["key", "bg", "key_2", "look"]);
    expect(r.bp.operations[3].depends_on).toEqual(["key_2"]);
    expect(r.bp.operations[1].depends_on).toEqual(["key"]);                       // the existing links are untouched
  });
  it("same asset id with another file is renamed and the new operation follows", () => {
    const inc = bp([op("bg2", "background_replacement", { start: 6, depends_on: ["k2"], parameters: { asset_id: "sea" } }), op("k2", "background_removal", { start: 6 })],
      { assets: [{ id: "sea", type: "image", path: "other/sea2.png" }] });
    const short = bp([op("key", "background_removal", { end: 4 }), op("bg", "background_replacement", { end: 4, depends_on: ["key"], parameters: { asset_id: "sea" } })], { assets: existing.assets });
    const r = mergeBlueprint(short, inc, schema, OPT, 9);
    expect(r.bp.assets.map(a => a.id)).toEqual(["sea", "sea_2"]);
    const added = r.bp.operations.find(o => o.id === "bg2")!;
    expect(added.parameters!.asset_id).toBe("sea_2");
  });
  it("a second background over an existing one is left out together with what hangs on it, and reported", () => {
    const inc = bp([op("k", "background_removal"), op("b", "background_replacement", { depends_on: ["k"] }), op("l", "studio_look", { depends_on: ["b"] }), op("g", "color_grading")]);
    const r = mergeBlueprint(existing, inc, schema, OPT, 9);
    expect(r.bp.operations.map(o => o.id)).toEqual(["key", "bg", "g"]);
    expect(r.notes.join(" ")).toContain("already has a background layer");
  });
  it("applies to the selected clip only: times are relative to the clip and cut at its end", () => {
    const inc = bp([op("g", "color_grading", { start: 1, end: 99 }), op("h", "color_grading", { start: 0 }), op("late", "color_grading", { start: 20 })]);
    const r = mergeBlueprint(existing, inc, schema, { applyOutput: false, clip: { tStart: 4, tEnd: 9 } }, 9);
    const g = r.bp.operations.find(o => o.id === "g")!, h = r.bp.operations.find(o => o.id === "h")!;
    expect([g.start, g.end]).toEqual([5, 9]);
    expect([h.start, h.end]).toEqual([4, 9]);
    expect(r.bp.operations.some(o => o.id === "late")).toBe(false);
    expect(r.notes.join(" ")).toContain("'late'");
  });
  it("does not modify its inputs", () => {
    const before = JSON.stringify(existing), inc = bp([op("key", "chroma_key")]), incBefore = JSON.stringify(inc);
    mergeBlueprint(existing, inc, schema, OPT, 9);
    expect(JSON.stringify(existing)).toBe(before); expect(JSON.stringify(inc)).toBe(incBefore);
  });
});
