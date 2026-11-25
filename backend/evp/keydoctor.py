"""Key doctor: look at the footage like a compositor and say what is wrong with the key, and what to change.

It samples frames across the clip (plus a short burst of consecutive frames for flicker), runs the same screen keyer the editor uses, and measures
the things that make a key look fake: an uneven or wrinkled screen, a screen that does not fill the frame, see-through spots inside the
subject, green spill on the subject's edge, an edge that is too hard or too wide, and edge flicker. Every finding says where (a time on the
timeline), how bad, and carries a fix made of real settings (or a mask to add) that can be applied with one click.

Everything here is measured on the pixels. Nothing is guessed from the file name or the settings alone.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import cv2

from .ml.screenkey import ScreenKeyer, _dom

ANALYSIS_WIDTH = 640


@dataclass
class Finding:
    id: str
    severity: str                       # problem | warn | info
    title: str
    detail: str
    time: float | None = None           # timeline seconds of the worst frame (the editor jumps there)
    fix: dict | None = None             # {"label", "set": {param: value}, "add_ops": [{"type", "params"}], "advice": str}
    measure: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "severity": self.severity, "title": self.title, "detail": self.detail, "time": self.time, "fix": self.fix, "measure": self.measure}


def _screen_mask(f: np.ndarray, K: np.ndarray, c: int):
    def chroma(x): return _dom(x, c) / np.maximum(x[..., c], 0.08)
    C0 = max(float(chroma(K[None, None, :])[0, 0]), 0.1)
    return chroma(f) > 0.6 * C0


def _frame_stats(rgb: np.ndarray, params: dict) -> dict:
    """one frame -> measurements"""
    keyer = ScreenKeyer({**params, "edge_color_fix": False})
    a, _ = keyer(rgb)
    f = rgb.astype(np.float32) / 255.0
    h, w = a.shape
    c, K = keyer.c, keyer.K
    S = _screen_mask(f, K, c)
    solid = (a > 0.5).astype(np.uint8)
    # silhouette = the subject with enclosed holes filled (gaps connected to the outside are real gaps, not holes)
    pad = np.ones((h + 2, w + 2), np.uint8)                    # one background pixel all around, so the fill always starts outside the subject
    pad[1:-1, 1:-1] = 1 - solid
    cv2.floodFill(pad, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 2)
    sil = (pad[1:-1, 1:-1] != 2).astype(np.uint8)
    sil_area = float(sil.sum())
    st: dict = {"a": a, "f": f, "S": S, "c": c, "K": K, "sil_area": sil_area, "screen_cov": float(S.mean())}
    if sil_area > 0.01 * h * w:
        inner = cv2.erode(sil, np.ones((9, 9), np.uint8)).astype(bool)
        st["see_through"] = float(((a < 0.9) & inner).sum() / max(1.0, float(inner.sum())))
        cnts, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perim = float(sum(cv2.arcLength(k, True) for k in cnts)) or 1.0
        st["edge_width"] = float(((a > 0.03) & (a < 0.97)).sum() / perim)
        dist = cv2.distanceTransform(solid, cv2.DIST_L2, 3)
        band = (a > 0.8) & (dist < 8)
        st["spill"] = float(np.clip(_dom(f, c), 0, 1)[band].mean()) if band.sum() > 50 else 0.0
    return st


def _uneven(st: dict) -> tuple[float, float]:
    f, S, c = st["f"], st["S"], st["c"]
    if S.sum() < 500: return 0.0, 0.0
    D = _dom(f, c)[S]
    lo, med = float(np.percentile(D, 8)), float(np.median(D))
    low_ratio = lo / max(med, 1e-3)                      # 1 = even; small = dark spots / folds / shadows on the screen
    g = f.mean(2)
    res = g - cv2.GaussianBlur(g, (0, 0), 2.0)
    er = cv2.erode(S.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    noise = float(res[er].std()) if er.sum() > 200 else 0.0
    return low_ratio, noise


def _strips(st: dict) -> dict[str, float]:
    """for each frame side: how much of the outer strip is NOT screen and NOT subject (the room, a light, the floor ...)"""
    S, a = st["S"], st["a"]
    h, w = a.shape
    m = max(3, int(0.06 * min(h, w)))
    other = ~S                              # whatever is not screen: the room, a light, the floor (kept by the key, so it is judged on the picture, not on the matte)
    return {"left": float(other[:, :m].mean()), "right": float(other[:, -m:].mean()), "top": float(other[:m].mean()), "bottom": float(other[-m:].mean())}


def _extent(st: dict, side: str) -> float:
    """how far (fraction of the frame) the non-screen area reaches in from the given side"""
    S, a = st["S"], st["a"]
    other = (~S).astype(np.float32)
    h, w = a.shape
    prof = other.mean(0) if side in ("left", "right") else other.mean(1)      # columns / rows
    if side in ("right", "bottom"): prof = prof[::-1]
    n = len(prof)
    for i in range(n):
        if prof[i] < 0.5: return min(0.5, (i + 2) / n + 0.01)
    return 0.5


def diagnose_frames(samples: list[tuple[float, np.ndarray]], burst: list[np.ndarray], params: dict, fps: float = 25.0) -> dict:
    """samples: [(timeline seconds, rgb uint8 HxWx3)]; burst: consecutive frames for the flicker test; params: the key's current settings."""
    out: list[Finding] = []
    stats = [(t, _frame_stats(rgb, params)) for t, rgb in samples]
    if not stats: return {"findings": [], "score": 0, "frames_checked": 0, "summary": "No frames could be read."}
    cov = float(np.median([s["screen_cov"] for _, s in stats]))
    if cov < 0.08:
        out.append(Finding("NO_SCREEN", "problem", "No green or blue screen found", "Less than 8% of the picture looks like a keying screen. This key is made for a green or blue screen; for ordinary footage use Background Removal with mode 'ai'.",
                           fix={"label": "Use AI background removal", "set": {"mode": "ai"}}))
        return _finish(out, len(stats))
    cur = params
    # ---- screen quality
    un = [(t, *_uneven(s)) for t, s in stats]
    worst_t, worst_low, _ = min(un, key=lambda x: x[1])
    med_low = float(np.median([u[1] for u in un])); med_noise = float(np.median([u[2] for u in un]))
    if med_low < 0.55:
        out.append(Finding("UNEVEN_SCREEN", "problem" if med_low < 0.4 else "warn", "The screen is uneven (folds, shadows, dark corners)",
                           f"Parts of the screen are only {med_low * 100:.0f}% as green as the rest. A smooth estimate cannot follow wrinkles, so haze or holes appear next to the person. "
                           "The best fix is a clean plate: record 3-5 s of the empty screen with the same camera and lights and use it in the Clean plate section.",
                           time=worst_t, fix={"label": "Add a clean plate (see Clean plate below)", "advice": "clean_plate"}, measure={"screen_evenness": round(med_low, 2)}))
    if med_noise > 0.02:
        out.append(Finding("NOISY_SCREEN", "warn", "The footage is noisy",
                           f"The screen has visible grain/compression noise ({med_noise * 255:.1f} levels). Noise makes the edge shimmer. More temporal smoothing calms it; a clean plate video also averages the noise away.",
                           time=worst_t, fix={"label": "Smooth the edge over time", "set": {"temporal_smooth": min(1.0, max(float(cur.get("temporal_smooth", 0.5)), 0.5) + 0.3)}}, measure={"noise_levels": round(med_noise * 255, 1)}))
    # ---- the screen does not fill the frame
    for side in ("left", "right", "top", "bottom"):
        fr = [s2 for s2 in (_strips(s) for _, s in stats)]
        v = float(np.median([x[side] for x in fr]))
        if v > 0.8:                                           # (a person cut by the frame edge never fills a whole side)
            ts = max(range(len(stats)), key=lambda i: fr[i][side]); ext = float(np.median([_extent(s, side) for _, s in stats]))
            rect = {"left": (0.0, 0.0, ext, 1.0), "right": (1.0 - ext, 0.0, ext, 1.0), "top": (0.0, 0.0, 1.0, ext), "bottom": (0.0, 1.0 - ext, 1.0, ext)}[side]
            out.append(Finding(f"SCREEN_EDGE_{side.upper()}", "problem", f"The screen does not fill the frame on the {side}",
                               f"{v * 100:.0f}% of the {side} edge is something other than the screen (the room, a light, the floor), which the key leaves in. A garbage mask removes it. (If that strip is really the person, ignore this.)",
                               time=stats[ts][0], fix={"label": f"Add a garbage mask on the {side} ({ext * 100:.0f}% of the frame)",
                                                       "add_ops": [{"type": "matte_mask", "params": {"mode": "garbage", "shape": "rect", "x": round(rect[0], 3), "y": round(rect[1], 3), "width": round(rect[2], 3), "height": round(rect[3], 3), "feather": 6}}]},
                               measure={"side": side, "not_screen": round(v, 2)}))
    # ---- the subject
    withsub = [(t, s) for t, s in stats if "see_through" in s]
    if not withsub:
        out.append(Finding("NO_SUBJECT", "info", "No subject found in the checked frames", "Nothing solid was found in front of the screen, so the edge checks could not run. Check frames where the person is in view."))
    else:
        st_t, st_worst = max(withsub, key=lambda x: x[1]["see_through"])
        med_st = float(np.median([s["see_through"] for _, s in withsub]))
        if med_st > 0.015 or st_worst["see_through"] > 0.04:
            level = max(med_st, st_worst["see_through"] * 0.6)
            rec = float(np.clip(0.25 + level * 6, 0.3, 0.7))
            out.append(Finding("SEE_THROUGH", "problem" if level > 0.04 else "warn", "Parts of the subject are see-through",
                               f"{max(med_st, st_worst['see_through']) * 100:.1f}% of the inside of the subject is partly transparent (worst around {st_t:.1f} s): motion-blurred hands, fine fabric, or clothing close to the screen colour. "
                               "Solidity makes those areas solid; a Keep mask can fix one stubborn spot.",
                               time=st_t, fix={"label": f"Raise solidity to {rec:.2f}", "set": {"solidity": round(max(rec, float(cur.get("solidity", 0.0))), 2)}}, measure={"see_through": round(max(med_st, st_worst['see_through']), 3)}))
        sp_t, sp_worst = max(withsub, key=lambda x: x[1]["spill"])
        med_sp = float(np.median([s["spill"] for _, s in withsub]))
        if med_sp > 0.03:
            sup = bool(cur.get("spill_suppression", True)); cur_str = float(cur.get("spill_strength", 0.5))
            new_str = round(min(1.0, max(cur_str, 0.5) + 0.3), 2)
            out.append(Finding("SPILL", "problem" if med_sp > 0.07 else "warn", "Green light spills onto the subject's edge",
                               f"The subject's edge pixels carry {med_sp * 100:.0f}% extra green (worst around {sp_t:.1f} s), which gives a green rim. "
                               + ("Spill removal is off." if not sup else f"Raise the spill strength (now {cur_str:.1f}). At the shoot: stand further from the screen and add a rim light from behind."),
                               time=sp_t, fix={"label": "Turn spill removal on" + (f" and raise it to {new_str}" if sup else ""), "set": {"spill_suppression": True, "spill_strength": new_str}}, measure={"spill": round(med_sp, 3)}))
        # edge width (analysis frames are 640 wide)
        ew = float(np.median([s["edge_width"] for _, s in withsub]))
        ew_t = max(withsub, key=lambda x: x[1]["edge_width"])[0]
        if ew > 6.0:
            out.append(Finding("EDGE_WIDE", "warn", "The edge is wide and soft",
                               f"The semi-transparent edge is about {ew:.1f} px thick. That comes from motion blur, a soft focus or heavy compression, and shows as a ghost halo. Pulling the edge in a little removes it.",
                               time=ew_t, fix={"label": "Pull the edge in by 1 px", "set": {"edge_shrink": round(max(float(cur.get("edge_shrink", 0.0)), 1.0), 1)}}, measure={"edge_px": round(ew, 1)}))
        elif ew < 0.7:
            out.append(Finding("EDGE_HARD", "warn", "The edge is very hard (cut-out look)",
                               f"The edge is only {ew:.1f} px thick, so the subject looks cut out. A touch of edge softness blends it into the new background.",
                               time=ew_t, fix={"label": "Soften the edge slightly", "set": {"edge_soften": round(max(float(cur.get("edge_soften", 0.0)), 0.6), 1)}}, measure={"edge_px": round(ew, 1)}))
    # ---- flicker (consecutive frames; judged where the picture itself did not change)
    if len(burst) >= 4:
        keyer = ScreenKeyer({**cur, "edge_color_fix": False})
        alphas = [keyer(b)[0] for b in burst]
        rgbs = [b.astype(np.float32) / 255.0 for b in burst]
        diffs = []
        for i in range(1, len(burst)):
            static = np.abs(rgbs[i] - rgbs[i - 1]).max(2) < 0.04
            band = ((alphas[i] > 0.02) & (alphas[i] < 0.98)) | ((alphas[i - 1] > 0.02) & (alphas[i - 1] < 0.98))
            m = static & band
            if m.sum() > 100: diffs.append(float(np.abs(alphas[i] - alphas[i - 1])[m].mean()))
        if diffs and float(np.median(diffs)) > 0.05:
            ts = float(cur.get("temporal_smooth", 0.5))
            out.append(Finding("FLICKER", "problem" if np.median(diffs) > 0.1 else "warn", "The edge flickers from frame to frame",
                               f"In parts of the picture that did not move, the edge opacity still changes by {np.median(diffs) * 100:.0f}% between frames (grain or compression). More temporal smoothing calms it.",
                               fix={"label": f"Raise temporal smoothing to {min(1.0, max(ts, 0.5) + 0.3):.1f}", "set": {"temporal_smooth": round(min(1.0, max(ts, 0.5) + 0.3), 2)}}, measure={"edge_change": round(float(np.median(diffs)), 3)}))
    return _finish(out, len(stats))


def _finish(findings: list[Finding], n: int) -> dict:
    score = 100 - sum({"problem": 22, "warn": 9, "info": 0}[f.severity] for f in findings)
    score = int(max(5, min(100, score)))
    order = {"problem": 0, "warn": 1, "info": 2}
    findings = sorted(findings, key=lambda f: order[f.severity])
    summary = ("The key looks healthy: nothing needs fixing." if not any(f.severity != "info" for f in findings)
               else f"{sum(f.severity == 'problem' for f in findings)} problem(s) and {sum(f.severity == 'warn' for f in findings)} warning(s) found. Apply the suggested fixes, then look at the matte again.")
    return {"findings": [f.to_json() for f in findings], "score": score, "frames_checked": n, "summary": summary}


def diagnose(source: str, params: dict, start_frame: int, end_frame: int, fps: float, out_w: int, out_h: int, edl: list | None, n_samples: int = 10) -> dict:
    """decode frames of the keyed range (timeline frames) and run the checks"""
    from .ml.frames import decode
    W = min(ANALYSIS_WIDTH, out_w) // 2 * 2
    H = max(2, int(round(W * out_h / out_w))) // 2 * 2
    n = max(1, end_frame - start_frame)
    picks = sorted({start_frame + int(round((i + 0.5) * n / n_samples)) for i in range(min(n_samples, n))})
    picks = [min(p, end_frame - 1) for p in picks]
    samples = []
    for p in picks:
        for fr in decode(source, W, H, fps, p, 1, edl, flags="area"):
            samples.append((p / fps, fr)); break
    burst: list[np.ndarray] = []
    if n >= 8:
        mid = start_frame + n // 2 - 3
        burst = list(decode(source, W, H, fps, max(start_frame, mid), 6, edl, flags="area"))
    return diagnose_frames(samples, burst, {**params, "mode": "screen"}, fps)
