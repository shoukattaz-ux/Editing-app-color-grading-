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

// ---------------------------------------------------------------- cutting
const F = 20, SRC = 4;   // 20 fps (0.05 s grid): every time used below is on the grid; 80-frame source
const cutBp = (clips?: m.Clip[], ops: m.Op[] = []): m.Blueprint => ({ blueprint_version: "1.0", assets: [], operations: ops, ...(clips ? { clips } : {}) });
const op = (id: string, start: number, end?: number): m.Op => ({ id, type: "color_grading", start, ...(end !== undefined ? { end } : {}) });
const cl = (id: string, a: number, b: number): m.Clip => ({ id, start: a, end: b });

describe("clip layout", () => {
  it("no clips = one implicit clip over the whole source", () => {
    const l = m.layoutClips(cutBp(), SRC, F);
    expect(l.cut).toBe(false); expect(l.duration).toBe(4); expect(l.frames).toBe(80); expect(l.clips[0].implicit).toBe(true);
  });
  it("matches the backend's numbers (same example as the Python test)", () => {
    const l = m.layoutClips(cutBp([cl("a", 0.4, 1.4), cl("b", 2.0, 3.0)]), SRC, 25);
    expect(l.clips.map(c => [c.sf, c.ef])).toEqual([[10, 35], [50, 75]]);
    expect(l.duration).toBeCloseTo(2.0); expect(l.clips[1].tStart).toBeCloseTo(1.0); expect(l.cut).toBe(true);
  });
  it("snaps to frames, clamps to the source, and keeps at least one frame", () => {
    const l = m.layoutClips(cutBp([cl("a", 0.41, 9), cl("b", 1, 1)]), SRC, F);
    expect(l.clips[0].sf).toBe(8); expect(l.clips[0].ef).toBe(80); expect(l.clips[1].ef - l.clips[1].sf).toBe(1);
  });
  it("one clip covering everything is not a cut", () => { expect(m.layoutClips(cutBp([cl("a", 0, 4)]), SRC, F).cut).toBe(false); });
  it("timelineToSource follows reordered clips", () => {
    const l = m.layoutClips(cutBp([cl("a", 2, 3), cl("b", 0, 0.4)]), SRC, F);       // a = 20 frames, b = 8 frames
    expect(m.timelineToSource(l, 0)).toEqual({ index: 0, src: 2 });
    expect(m.timelineToSource(l, 19 / F).src).toBeCloseTo(59 / F);
    expect(m.timelineToSource(l, 20 / F)).toEqual({ index: 1, src: 0 });
    expect(m.timelineToSource(l, 99).index).toBe(1);
  });
  it("gridFps uses numeric output fps else the source's", () => {
    expect(m.gridFps(cutBp(), 30)).toBe(30); expect(m.gridFps({ ...cutBp(), output: { fps: 24 } }, 30)).toBe(24); expect(m.gridFps({ ...cutBp(), output: { fps: "source" } }, 30)).toBe(30);
  });
});

describe("cut / delete / trim / duplicate / move", () => {
  it("cutAt splits the clip without changing the picture or effect times", () => {
    const b = cutBp(undefined, [op("g", 1, 3)]);
    const r = m.cutAt(b, 1.5, SRC, F);
    expect(r.ids).toBeTruthy();
    const l = m.layoutClips(r.bp, SRC, F);
    expect(l.clips.map(c => [c.sf, c.ef])).toEqual([[0, 30], [30, 80]]);
    expect(l.duration).toBe(4); expect(r.bp.operations).toEqual(b.operations);          // picture and effect times unchanged
  });
  it("cutAt at the very start/end or on an existing boundary does nothing", () => {
    const b = m.cutAt(cutBp(), 2, SRC, F).bp;
    expect(m.cutAt(b, 0, SRC, F).ids).toBeUndefined(); expect(m.cutAt(b, 4, SRC, F).ids).toBeUndefined(); expect(m.cutAt(b, 2, SRC, F).ids).toBeUndefined();
  });
  it("a split without removing anything is still two clips; only a lone full-length clip is simplified away", () => {
    const split = m.cutAt(cutBp(), 2, SRC, F).bp;
    expect(m.simplifyClips(split, SRC, F).clips).toHaveLength(2);
    expect(m.simplifyClips(cutBp([cl("only", 0, 4)]), SRC, F).clips).toBeUndefined();
  });
  it("deleteClip ripples effects: after shifts, spanning shrinks, inside disappears", () => {
    // timeline 0-4: clips a [0,1) b [1,2) c [2,4); delete b (timeline 1..2)
    const b0 = cutBp([cl("a", 0, 1), cl("b", 1, 2), cl("c", 2, 4)], [op("before", 0, 1), op("inside", 1.2, 1.8), op("span", 0.5, 3), op("after", 2, 3), op("tail", 3)]);
    const r = m.deleteClip(b0, "b", SRC, F);
    expect(r.removedOps).toEqual(["inside"]);
    const byId = Object.fromEntries(r.bp.operations.map(o => [o.id, o]));
    expect(byId.before).toEqual(op("before", 0, 1)); expect(byId.span).toEqual(op("span", 0.5, 2)); expect(byId.after).toEqual(op("after", 1, 2)); expect(byId.tail).toEqual(op("tail", 2));
    expect(r.bp.clips!.map(c => c.id)).toEqual(["a", "c"]); expect(m.layoutClips(r.bp, SRC, F).duration).toBe(3);
  });
  it("the last clip cannot be deleted", () => {
    const b = cutBp([cl("a", 0, 1)]); expect(m.deleteClip(b, "a", SRC, F).bp).toBe(b);
    expect(m.deleteClip(cutBp(), "clip_001", SRC, F).bp.clips).toBeUndefined();
  });
  it("trimClip shrinking the end pulls later footage and effects earlier; growing pushes them later", () => {
    const b0 = cutBp([cl("a", 0, 2), cl("b", 2, 4)], [op("onA", 0, 2), op("onB", 2, 4)]);
    const shorter = m.trimClip(b0, "a", "end", 1.5, SRC, F);
    expect(shorter.clips![0].end).toBeCloseTo(1.5);
    expect(shorter.operations[0]).toEqual(op("onA", 0, 1.5)); expect(shorter.operations[1]).toEqual(op("onB", 1.5, 3.5));
    const longer = m.trimClip(shorter, "a", "end", 2.0, SRC, F);
    expect(longer.operations[0].end).toBeCloseTo(2); expect(longer.operations[1].start).toBeCloseTo(2);
  });
  it("trimming the start in/out is clamped and incremental drags equal one big drag", () => {
    const b0 = cutBp([cl("a", 1, 3)], [op("g", 0, 2)]);
    const step = m.trimClip(m.trimClip(m.trimClip(b0, "a", "start", 1.2, SRC, F), "a", "start", 1.4, SRC, F), "a", "start", 1.6, SRC, F);
    const once = m.trimClip(b0, "a", "start", 1.6, SRC, F);
    expect(step.clips).toEqual(once.clips); expect(step.operations).toEqual(once.operations);
    expect(m.trimClip(b0, "a", "start", -5, SRC, F).clips![0].start).toBe(0);
    expect(m.trimClip(b0, "a", "end", 99, SRC, F).clips![0].end).toBe(4);
    expect(m.trimClip(b0, "a", "end", 0.2, SRC, F).clips![0].end).toBeCloseTo(1 + 1 / F);     // at least one frame
  });
  it("an effect with no end keeps following the end of the timeline", () => {
    const r = m.trimClip(cutBp([cl("a", 0, 4)], [op("tail", 1)]), "a", "end", 3, SRC, F);
    expect(r.operations[0].end).toBeUndefined(); expect(r.operations[0].start).toBe(1);
  });
  it("duplicateClip inserts a copy and shifts later effects", () => {
    const b0 = cutBp([cl("a", 0, 1), cl("b", 1, 2)], [op("onB", 1, 2), op("onA", 0, 1)]);
    const r = m.duplicateClip(b0, "a", SRC, F);
    expect(r.bp.clips!.map(c => [c.start, c.end])).toEqual([[0, 1], [0, 1], [1, 2]]);
    expect(r.bp.operations).toEqual([op("onB", 2, 3), op("onA", 0, 1)]);
    expect(new Set(r.bp.clips!.map(c => c.id)).size).toBe(3);
  });
  it("moveClip reorders; effects keep their timeline times; edges are no-ops", () => {
    const b0 = cutBp([cl("a", 0, 1), cl("b", 2, 3)], [op("g", 0, 1)]);
    const r = m.moveClip(b0, "b", -1, SRC, F);
    expect(r.clips!.map(c => c.id)).toEqual(["b", "a"]); expect(r.operations).toEqual(b0.operations);
    expect(m.moveClip(b0, "a", -1, SRC, F)).toBe(b0); expect(m.moveClip(b0, "b", 1, SRC, F)).toBe(b0);
  });
});

describe("ripple helpers", () => {
  it("rippleRemove / rippleInsert are inverse for effects away from the edit", () => {
    const ops = [op("a", 0, 1), op("b", 3, 4)];
    const removed = m.rippleRemove(ops, 1.5, 1, F).ops;
    expect(removed[1]).toEqual(op("b", 2, 3));
    expect(m.rippleInsert(removed, 1.5, 1, F, false)).toEqual(ops);
  });
  it("attach extends an effect that ends exactly at the insertion point", () => {
    expect(m.rippleInsert([op("g", 0, 2)], 2, 1, F, true)[0].end).toBe(3);
    expect(m.rippleInsert([op("g", 0, 2)], 2, 1, F, false)[0].end).toBe(2);
  });
});

describe("timecodes in AI-written blueprints", () => {
  it("parseTime accepts numbers, seconds, mm:ss and hh:mm:ss", () => {
    expect(m.parseTime(1.5)).toBe(1.5); expect(m.parseTime("3.25")).toBe(3.25); expect(m.parseTime("1:02.5")).toBe(62.5); expect(m.parseTime("00:01:02.500")).toBe(62.5);
    expect(m.parseTime("abc")).toBeUndefined(); expect(m.parseTime(-1)).toBeUndefined(); expect(m.parseTime(true)).toBeUndefined();
  });
  it("parseBlueprintText converts timecodes in operations and clips", () => {
    const { bp } = m.parseBlueprintText(JSON.stringify({ blueprint_version: "1.0", clips: [{ id: "a", start: "0:01", end: "0:03.5" }], operations: [{ id: "x", type: "color_grading", start: "00:00:02.000", end: 3 }] }));
    expect(bp!.clips![0]).toMatchObject({ start: 1, end: 3.5 }); expect(bp!.operations[0]).toMatchObject({ start: 2, end: 3 });
  });
});
