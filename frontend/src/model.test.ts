import { describe, it, expect } from "vitest";
import * as m from "./model";

const spec = (type: string): m.OpSpec => ({ type, title: type, category: "", track: "effects", description: "", supported: true, unsupported_reason: "", provides_alpha: false, parameters: { contrast: { type: "number", default: 1 } } });
const base = (): m.Blueprint => ({ blueprint_version: "1.0", assets: [], operations: [
  { id: "k", type: "chroma_key", start: 0, end: 10 },
  { id: "r", type: "background_replacement", start: 2, end: 8, depends_on: ["k"], parameters: { fit: "cover" } }] });

describe("blueprint edits are immutable", () => {
  it("delete removes the op and dangling dependencies", () => {
    const b = base(), n = m.deleteOp(b, "k");
    expect(n.operations.map(o => o.id)).toEqual(["r"]);
    expect(n.operations[0].depends_on).toEqual([]);
    expect(b.operations).toHaveLength(2);
  });
  it("duplicate deep-copies and gets a fresh id", () => {
    const { bp, id } = m.duplicateOp(base(), "r");
    expect(id).toBe("background_replacement_001");
    bp.operations[2].parameters!.fit = "contain";
    expect(bp.operations[1].parameters!.fit).toBe("cover");
  });
  it("toggle, setParam, reset", () => {
    let b = m.toggleOp(base(), "k"); expect(b.operations[0].enabled).toBe(false);
    b = m.toggleOp(b, "k"); expect(b.operations[0].enabled).toBe(true);
    b = m.setParam(b, "r", "blur", 3); expect(b.operations[1].parameters).toEqual({ fit: "cover", blur: 3 });
    b = m.resetParams(b, "r"); expect(b.operations[1].parameters).toEqual({});
  });
  it("addOp picks unique ids", () => {
    let b = base(); const a = m.addOp(b, spec("color_grading"), 1, 2); const c = m.addOp(a.bp, spec("color_grading"), 1, 2);
    expect([a.id, c.id]).toEqual(["color_grading_001", "color_grading_002"]);
  });
  it("split divides on the frame grid and rejects out-of-range", () => {
    const { bp, ids } = m.splitOp(base(), "k", 4.02, 30);
    expect(ids).toBeTruthy(); expect(bp.operations[0].end).toBeCloseTo(4.0333, 3); expect(bp.operations[1].start).toBeCloseTo(4.0333, 3);
    expect(m.splitOp(base(), "k", 0, 30).ids).toBeUndefined(); expect(m.splitOp(base(), "k", 10, 30).ids).toBeUndefined();
  });
  it("parseBlueprintText reports errors", () => {
    expect(m.parseBlueprintText("{bad").error).toBeTruthy();
    expect(m.parseBlueprintText("[]").error).toBeTruthy();
    expect(m.parseBlueprintText('{"blueprint_version":"1.0"}').bp?.operations).toEqual([]);
  });
});

describe("timeline math", () => {
  it("snaps to frames and converts px", () => {
    expect(m.snap(1.02, 30)).toBeCloseTo(1.0333, 3);
    expect(m.xToTime(304, 100, 30, 60)).toBeCloseTo(3.0333, 3);
    expect(m.xToTime(-50, 100, 30, 60)).toBe(0); expect(m.xToTime(1e6, 100, 30, 60)).toBe(60);
  });
  it("formats timecode", () => { expect(m.formatTime(61.5, 30)).toBe("00:01:01:15"); expect(m.formatTime(0, 24)).toBe("00:00:00:00"); });
  it("drag move/resize clamps to video and min length", () => {
    const o: m.Op = { id: "a", type: "x", start: 2, end: 4 };
    expect(m.dragOp(o, "move", -10, 30, 10)).toEqual({ start: 0, end: 2 });
    expect(m.dragOp(o, "move", 100, 30, 10)).toEqual({ start: 8, end: 10 });
    expect(m.dragOp(o, "start", 100, 30, 10).start).toBeCloseTo(4 - 1 / 30, 6);
    expect(m.dragOp(o, "end", -100, 30, 10).end).toBeCloseTo(2 + 1 / 30, 6);
    expect(m.dragOp(o, "end", 100, 30, 10).end).toBe(10);
  });
});

describe("history", () => {
  it("undo / redo / branching", () => {
    let h = m.newHistory(0); h = m.commit(h, 1); h = m.commit(h, 2);
    h = m.undo(h); expect(h.present).toBe(1); h = m.undo(h); expect(h.present).toBe(0); h = m.undo(h); expect(h.present).toBe(0);
    h = m.redo(h); expect(h.present).toBe(1);
    h = m.commit(h, 9); expect(h.future).toEqual([]); expect(m.redo(h).present).toBe(9);
  });
  it("coalesces rapid edits with the same key", () => {
    let h = m.newHistory(0);
    h = m.commit(h, 1, "p", 1000); h = m.commit(h, 2, "p", 1200); h = m.commit(h, 3, "p", 1400);
    expect(h.past).toEqual([0]); expect(m.undo(h).present).toBe(0);
    h = m.commit(h, 4, "p", 5000); expect(h.past).toEqual([0, 3]);
  });
  it("ignores no-op commits and caps length", () => {
    let h = m.newHistory(0); expect(m.commit(h, 0)).toBe(h);
    for (let i = 1; i <= 300; i++) h = m.commit(h, i);
    expect(h.past.length).toBe(200);
  });
});
