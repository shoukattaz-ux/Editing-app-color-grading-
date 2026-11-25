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

// ---------------------------------------------------------------- keyframes
import vectors from "../../shared/keyframe_vectors.json";

const kfop = (kf: Record<string, m.Keyframe[]>, start = 1, end = 5, extra: Partial<m.Op> = {}): m.Op => ({ id: "g", type: "color_grading", start, end, keyframes: kf, ...extra });
const kfbp = (o: m.Op[]): m.Blueprint => ({ blueprint_version: "1.0", assets: [], operations: o });
const KF = (...a: [number, number, m.Easing?][]) => a.map(([time, value, easing]) => ({ time, value, ...(easing ? { easing } : {}) }));

describe("keyframe maths", () => {
  it("matches the Python implementation on every shared vector", () => {
    expect(vectors.cases.length).toBeGreaterThan(8);
    for (const c of vectors.cases) for (const s of c.samples)
      expect(m.valueAt(c.keyframes as m.Keyframe[], s.t), `${c.name} @ ${s.t}`).toBeCloseTo(s.v, 9);
  });
  it("holds outside the keyframes and honours easing/hold", () => {
    const k = KF([1, 2, "linear"], [3, 6]);
    expect([m.valueAt(k, 0), m.valueAt(k, 2), m.valueAt(k, 9)]).toEqual([2, 4, 6]);
    expect(m.valueAt(KF([0, 1, "hold"], [2, 5]), 1.99)).toBe(1);
    expect(m.ease("ease_in", 0.5)).toBeLessThan(0.5); expect(m.ease("ease_out", 0.5)).toBeGreaterThan(0.5); expect(m.ease("ease_in_out", 0.5)).toBeCloseTo(0.5);
  });
  it("paramAt: keyframed, static or default; time is timeline time", () => {
    const o = kfop({ exposure: KF([0, 0], [2, 1]) }, 10, 14, { parameters: { contrast: 1.4 } });
    expect(m.paramAt(o, "exposure", 11, 0)).toBeCloseTo(0.5); expect(m.paramAt(o, "contrast", 11, 1)).toBe(1.4); expect(m.paramAt(o, "gamma", 11, 1)).toBe(1);
  });
});

describe("keyframe editing", () => {
  const F = 20;
  it("setKeyframe creates, updates, sorts, clamps and limits", () => {
    let b = kfbp([{ id: "g", type: "color_grading", start: 1, end: 5, parameters: { exposure: 0.2 } }]);
    b = m.setKeyframe(b, "g", "exposure", 2, 0.5, F, -3, 3);
    b = m.setKeyframe(b, "g", "exposure", 0.5, -9, F, -3, 3);                       // clamped to the minimum
    expect(b.operations[0].keyframes!.exposure.map(k => [k.time, k.value])).toEqual([[0.5, -3], [2, 0.5]]);
    b = m.setKeyframe(b, "g", "exposure", 2.01, 1, F, -3, 3);                       // within half a frame: updates, does not add
    expect(b.operations[0].keyframes!.exposure).toHaveLength(2); expect(b.operations[0].keyframes!.exposure[1].value).toBe(1);
    let many = b; for (let i = 0; i < 80; i++) many = m.setKeyframe(many, "g", "exposure", 3 + i * 0.05, 0.1, F);
    expect(many.operations[0].keyframes!.exposure.length).toBe(m.MAX_KEYFRAMES);
  });
  it("removing the last keyframe makes the parameter static at that value", () => {
    let b = m.setKeyframe(kfbp([{ id: "g", type: "x", start: 0, end: 4 }]), "g", "exposure", 1, 0.7, F);
    b = m.removeKeyframe(b, "g", "exposure", 0);
    expect(b.operations[0].keyframes).toBeUndefined(); expect(b.operations[0].parameters!.exposure).toBe(0.7);
  });
  it("stopwatch on/off", () => {
    let b = m.enableAnimation(kfbp([{ id: "g", type: "x", start: 0, end: 4, parameters: { exposure: 0.3 } }]), "g", "exposure", 1, 0.3, F);
    expect(m.isAnimated(b.operations[0], "exposure")).toBe(true);
    b = m.disableAnimation(b, "g", "exposure", 0.9);
    expect(m.isAnimated(b.operations[0], "exposure")).toBe(false); expect(b.operations[0].parameters!.exposure).toBe(0.9);
  });
  it("moveKeyframe cannot cross neighbours and snaps to frames", () => {
    const b = kfbp([kfop({ e: KF([0, 0], [1, 1], [2, 2]) })]);
    const r = m.moveKeyframe(b, "g", "e", 1, 5, 9, F).operations[0].keyframes!.e;
    expect(r[1].time).toBeCloseTo(2 - 1 / F); expect(r[1].value).toBe(9);
    expect(m.moveKeyframe(b, "g", "e", 1, -5, 1, F).operations[0].keyframes!.e[1].time).toBeCloseTo(1 / F);
  });
  it("setKeyframeEasing and resetParams (clears animation)", () => {
    let b = kfbp([kfop({ e: KF([0, 0], [1, 1]) })]);
    expect(m.setKeyframeEasing(b, "g", "e", 0, "ease_out").operations[0].keyframes!.e[0].easing).toBe("ease_out");
    expect(m.resetParams(b, "g").operations[0].keyframes).toBeUndefined();
  });
});

describe("keyframes when effects are retimed, split, duplicated or rippled", () => {
  const F = 20;
  const base = () => kfbp([kfop({ e: KF([0, 0], [2, 1, "ease_out"], [4, 3]) }, 10, 14)]);   // op 10..14, keyframes at abs 10, 12, 14
  it("moving an effect carries its animation (relative times unchanged)", () => {
    const r = m.setOpTime(base(), "g", 20, 24).operations[0];
    expect(r.keyframes!.e.map(k => k.time)).toEqual([0, 2, 4]);
  });
  it("trimming only the START edge keeps keyframes where they are on the timeline", () => {
    const r = m.setOpTime(base(), "g", 11, 14).operations[0];
    expect(r.keyframes!.e.map(k => k.time)).toEqual([-1, 1, 3]);                         // abs 10, 12, 14 unchanged; the first now lies before the start
    expect(m.valueAt(r.keyframes!.e, 12 - 11)).toBeCloseTo(1);
  });
  it("trimming the END edge changes nothing in the keyframes", () => {
    expect(m.setOpTime(base(), "g", 10, 12).operations[0].keyframes!.e.map(k => k.time)).toEqual([0, 2, 4]);
  });
  it("splitting keeps the original curve exactly on both halves", () => {
    const b0 = base(), r = m.splitOp(b0, "g", 11.3, F);
    expect(r.ids).toBeTruthy();
    const [L, R] = [r.bp.operations[0], r.bp.operations[1]], s = R.start;
    for (let t = 10; t <= 14; t += 0.05) {
      const expect_ = m.valueAt(b0.operations[0].keyframes!.e, t - 10);
      const got = t < s ? m.valueAt(L.keyframes!.e, t - L.start) : m.valueAt(R.keyframes!.e, t - R.start);
      expect(got).toBeCloseTo(expect_, 9);
    }
    L.keyframes!.e[0].value = 99;                                                          // halves are independent copies
    expect(R.keyframes!.e.every(k => k.value !== 99)).toBe(true);
  });
  it("duplicating an effect copies the animation independently", () => {
    const r = m.duplicateOp(base(), "g");
    r.bp.operations[1].keyframes!.e[0].value = 7;
    expect(r.bp.operations[0].keyframes!.e[0].value).toBe(0);
  });
  it("cutting a range out of the footage drops keyframes inside it and moves later ones earlier", () => {
    // timeline 10..14 effect, keyframes at abs 10, 12, 14 ; remove [11,13): kf@12 vanishes, kf@14 -> 12
    const r = m.rippleRemove(base().operations, 11, 2, F).ops[0];
    expect(r.start).toBe(10); expect(r.end).toBe(12);
    expect(r.keyframes!.e.map(k => [k.time, k.value])).toEqual([[0, 0], [2, 3]]);
    expect(m.valueAt(r.keyframes!.e, 1)).toBeCloseTo(1.5);                                // smooth across the gap, no jump
  });
  it("an effect that starts inside the removed range keeps its later keyframes attached to the footage", () => {
    const o = kfop({ e: KF([0, 0], [2, 1], [4, 3]) }, 11.5, 14);                           // keyframes at abs 11.5, 13.5, 15.5
    const r = m.rippleRemove([o], 11, 2, F).ops[0];                                        // remove [11,13): effect now starts at 11
    expect(r.start).toBe(11);
    expect(r.keyframes!.e.map(k => [k.time, k.value])).toEqual([[0.5, 1], [2.5, 3]]);     // abs 13.5->11.5, 15.5->13.5 (kf@11.5 was in the cut)
  });
  it("if every keyframe falls in the cut, the animation is kept as a hold rather than lost", () => {
    const o = kfop({ e: KF([0.5, 2], [1, 4]) }, 10, 14);
    const r = m.rippleRemove([o], 10.2, 1.5, F).ops[0];
    expect(r.keyframes!.e).toHaveLength(1);
  });
  it("making room moves keyframes after the insertion point", () => {
    const r = m.rippleInsert(base().operations, 11, 2, F, false)[0];
    expect(r.end).toBe(16); expect(r.keyframes!.e.map(k => k.time)).toEqual([0, 4, 6]);
  });
});


describe("describeChange", () => {
  const op = (id: string, type: string, extra: Partial<m.Op> = {}): m.Op => ({ id, type, start: 0, parameters: {}, ...extra });
  const bp = (ops: m.Op[], clips?: m.Clip[]): m.Blueprint => ({ blueprint_version: "1.0", assets: [], operations: ops, ...(clips ? { clips } : {}) });
  const title = (t: string) => ({ color_grading: "Color Grading", studio_look: "Studio Look" } as Record<string, string>)[t] ?? t;
  it("names what an undo step did", () => {
    const a = bp([op("g", "color_grading")]);
    expect(m.describeChange(bp([]), a, title)).toBe("Add Color Grading");
    expect(m.describeChange(a, bp([]), title)).toBe("Delete Color Grading");
    expect(m.describeChange(a, bp([op("g", "color_grading", { parameters: { contrast: 1.2 } })]), title)).toBe("Change contrast (Color Grading)");
    expect(m.describeChange(a, bp([op("g", "color_grading", { start: 1 })]), title)).toBe("Move or trim Color Grading");
    expect(m.describeChange(a, bp([op("g", "color_grading", { enabled: false })]), title)).toBe("Disable Color Grading");
    expect(m.describeChange(bp([], [{ id: "a", start: 0, end: 5 }]), bp([], [{ id: "a", start: 0, end: 2 }, { id: "b", start: 2, end: 5 }]), title)).toBe("Cut footage");
    expect(m.describeChange(bp([], [{ id: "a", start: 0, end: 2 }, { id: "b", start: 2, end: 5 }]), bp([], [{ id: "a", start: 0, end: 2 }]), title)).toBe("Delete clip");
    expect(m.describeChange(a, a, title)).toBe("Change");
  });
});
