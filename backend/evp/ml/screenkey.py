"""Professional green/blue-screen keyer (no model, no GPU): physically based unmixing instead of a colour-distance threshold.

A pixel at a hair strand or fuzzy wool edge is a *mix*  I = a*F + (1-a)*B  of the subject F and the screen B. The screen is the only
thing that is strongly green-dominant, so the "screen-ness" d = dom(I)/dom(B) (dom = green - max(red, blue) for a green screen) falls
linearly from 1 to 0 across the mix and  a = 1 - d  recovers the true partial transparency, hair included. What makes it robust:

  * the screen colour is found automatically (frame border + dominant-channel statistics), and tracked smoothly over time;
  * B is a *local* estimate (masked, normalised blur of the screen pixels), so uneven lighting, hot spots and shadows on the screen
    do not leave holes or haze;
  * the foreground colour is solved back out of the mix, F = (I - (1-a)B)/a, so edge pixels carry no green halo;
  * core pixels are despilled by the compiler; holes and isolated blobs are cleaned; the matte is smoothed over time only where
    nothing moved (no flicker, no ghosting).
"""
from __future__ import annotations
import numpy as np
import cv2

CH = {"red": 0, "green": 1, "blue": 2}
PLATE_FLOOR = 0.04      # smallest screen excess (green - max(red, blue)) divided by when a clean plate describes the screen


def _dom(f: np.ndarray, c: int) -> np.ndarray:
    others = [i for i in range(3) if i != c]
    return f[..., c] - np.maximum(f[..., others[0]], f[..., others[1]])


def _hex_channel(hexc: str) -> int:
    v = [int(hexc.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return int(np.argmax(v))


class ScreenKeyer:
    def __init__(self, p: dict, plate: np.ndarray | None = None):
        self.p = p
        self.plate = plate                        # clean plate (RGB 0..1) or None; resized to the frame on first use
        self.plate_frames = 0                     # frames where the plate fitted and was used
        self.plate_rejected = 0                   # frames where it did not fit (camera moved ...) and the keyer's own estimate was used
        self._plate_fit: np.ndarray | None = None
        self.prev_a: np.ndarray | None = None
        self.K: np.ndarray | None = None          # tracked screen colour (0..1)
        self.c: int | None = None                 # dominant channel of the screen: 1 = green, 2 = blue
        self.fixed_c = None if p.get("auto_screen", True) else _hex_channel(p["key_color"])

    # -- screen colour ---------------------------------------------------------------------------------------------
    def _find_screen(self, f: np.ndarray) -> None:
        # statistics only (median colour, 95th percentile): a quarter-size copy gives the same answer for a fraction of the time
        q = 4 if min(f.shape[:2]) >= 400 else 1
        if q > 1: f = cv2.resize(f, (f.shape[1] // q, f.shape[0] // q), interpolation=cv2.INTER_AREA)
        h, w = f.shape[:2]
        m = max(2, int(min(h, w) * 0.08))
        ring = np.concatenate([f[:m].reshape(-1, 3), f[-m:].reshape(-1, 3), f[:, :m].reshape(-1, 3), f[:, -m:].reshape(-1, 3)])
        if self.fixed_c is not None: c = self.fixed_c
        else:
            med = np.median(ring, 0)
            c = int(np.argmax([med[i] - max(med[j] for j in range(3) if j != i) for i in range(3)]))
            c = c if c in (1, 2) else 1                        # screens are green or blue
        d = _dom(f, c)
        thr = max(0.08, 0.6 * float(np.percentile(d, 95)))
        sel = d > thr
        K = np.median(f[sel], 0) if sel.sum() > 200 // (q * q) else (self.K if self.K is not None else np.array([0.2, 0.7, 0.2]))
        self.K = K if self.K is None or c != self.c else 0.7 * self.K + 0.3 * K
        self.c = c

    def __call__(self, rgb: np.ndarray, need_fg: bool = True) -> tuple[np.ndarray, np.ndarray | None]:
        """need_fg=False: a warm-up frame of a live preview - only the matte (it primes the screen tracking and the temporal smoothing)
        is needed, so the subject-colour solve, the costly part, is skipped. The matte itself is identical either way."""
        p = self.p
        need_fg = need_fg and p.get("edge_color_fix", True)
        f = rgb.astype(np.float32) / 255.0
        h, w = f.shape[:2]
        self._find_screen(f)
        c, K = self.c, self.K
        # Which parts of the sheet ARE screen is judged by chromaticity (green excess relative to brightness), so a shadowed or dim corner
        # counts as screen and gets its own local reference. Transparency itself uses the absolute green excess against that LOCAL reference
        # (relative measures amplify noise on dark hair).
        def chroma(x):
            return _dom(x, c) / np.maximum(x[..., c], 0.08)
        dom = _dom(f, c)
        C0 = max(float(chroma(K[None, None, :])[0, 0]), 0.1)
        # local screen model at 1/8 resolution: normalised blur of the pixels that are clearly screen
        s = 8
        small = cv2.resize(f, (max(2, w // s), max(2, h // s)), interpolation=cv2.INTER_AREA)
        S = (chroma(small) > 0.6 * C0).astype(np.float32)
        sig = max(2.0, min(small.shape[:2]) / 6)
        num = cv2.GaussianBlur(small * S[..., None], (0, 0), sig)
        den = cv2.GaussianBlur(S, (0, 0), sig)[..., None]
        ok = den > 0.02
        B_small = np.where(ok, num / np.maximum(den, 1e-4), K[None, None, :]).astype(np.float32)
        B = cv2.resize(B_small, (w, h), interpolation=cv2.INTER_LINEAR)
        n0, n1 = float(p["similarity"]), float(p["blend"]) * 0.5

        def alpha_from(Bm: np.ndarray, floor: float = 0.1) -> np.ndarray:
            d = dom / np.maximum(_dom(Bm, c), floor)
            return np.clip((1.0 - d - n0) / max(1e-3, 1.0 - n0 - n1), 0.0, 1.0).astype(np.float32)
        Bpl = None
        if self.plate is not None:                    # (a partial clean_plate_strength is handled by the caller: it mixes a plate keyer with a plain one)
            Bpl = self._plate_model(f, small, S, B, sig, c, C0, chroma)
            if Bpl is not None: B = Bpl
        # a measured plate can be trusted in dark parts of the screen where a guessed model cannot: a lower floor there keeps the matte clean in shadows
        a = alpha_from(B, PLATE_FLOOR if (self.plate is not None and Bpl is not None) else 0.1)
        if need_fg:
            core = cv2.erode((a > 0.995).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(np.float32)      # safely inside the subject: no mixed pixels
            near, got = self._nearest_solid(f, core, h, w, c if p.get("spill_suppression", True) else None, p)      # (spill removed: borrowed colours must not carry it)
        a = self._clean(a, p)
        # temporal smoothing where nothing moved (large changes pass straight through: no ghosting)
        ts = float(p.get("temporal_smooth", 0.5))
        if ts > 0 and self.prev_a is not None and self.prev_a.shape == a.shape:
            wgt = np.where(np.abs(a - self.prev_a) > 0.3, 1.0, 1.0 - 0.6 * ts).astype(np.float32)
            a = self.prev_a + (a - self.prev_a) * wgt
        self.prev_a = a
        if not need_fg:
            return a, None
        # solve the subject colour back out of the mix and despill it - only in the edge band where it matters:
        #   solid subject (a = 1): the frame itself;   clear screen (a ~ 0, invisible): the nearest subject colour, so nothing green can bleed
        #   into the edge if the subject is blurred later;   in between: the full solve.  (Same result as solving every pixel.)
        # (thresholds: at alpha >= 0.996 the solved colour equals the frame to within 1/1000 of a level; at alpha < 0.01 the pixel is invisible)
        bg_ = (a < 0.01) & got[..., 0]
        fg = np.where(bg_[..., None], near, f)
        idx = np.flatnonzero((a < 0.996) & ~bg_)                       # the mixed edge pixels: usually around 1 % of the frame
        if idx.size:
            ab = a.ravel()[idx][:, None]; fb = f.reshape(-1, 3)[idx]; Bb = B.reshape(-1, 3)[idx]
            F = np.clip((fb - (1.0 - ab) * Bb) / np.maximum(ab, 0.05), 0, 1)
            # where almost everything is screen the solve divides noise by a tiny alpha: lean on the nearest solid subject colour instead
            conf = np.clip(ab / 0.6, 0.0, 1.0)
            F = np.where(got.reshape(-1, 1)[idx], F * conf + near.reshape(-1, 3)[idx] * (1 - conf), F)
            t = np.clip((1.0 - ab) * 8.0, 0.0, 1.0)
            fg.reshape(-1, 3)[idx] = np.clip(fb * (1 - t) + F * t, 0, 1)
        if p.get("spill_suppression", True):
            fg = self._despill(fg, c, p)
            # thin edge band only: motion-blurred fingers and fabric fringes keep an olive-yellow rim (red about equal to green) that the rule
            # above does not see. Cap the screen channel at 85% of the larger other one there; solid areas (alpha >= 0.97) are never touched.
            o1, o2 = [i for i in range(3) if i != c]
            cap = 0.85 * np.maximum(fg[..., o1], fg[..., o2])
            edge = (a < 0.97) & (a > 0.02)
            fg[..., c] = np.where(edge, np.minimum(fg[..., c], cap), fg[..., c])
        return a, np.clip(fg, 0, 1).astype(np.float32)

    def _plate_model(self, f, small, S, B_est, sig, c, C0, chroma):
        """screen model from the clean plate: plate x a smooth gain field (the slow drift between plate and live footage). Used only if it explains
        the screen pixels of THIS frame better than the keyer's own estimate does (else the camera moved or the plate is of another scene)."""
        h, w = f.shape[:2]
        if self._plate_fit is None or self._plate_fit.shape[:2] != (h, w):
            self._plate_fit = cv2.resize(self.plate, (w, h), interpolation=cv2.INTER_AREA if self.plate.shape[1] > w else cv2.INTER_LINEAR).astype(np.float32)
        pl = self._plate_fit
        sh, sw = small.shape[:2]
        pl_small = cv2.resize(pl, (sw, sh), interpolation=cv2.INTER_AREA)
        ratio = small / np.maximum(pl_small, 0.03)
        num = cv2.GaussianBlur(ratio * S[..., None], (0, 0), sig)
        den = cv2.GaussianBlur(S, (0, 0), sig)[..., None]
        glob = np.median(ratio[S > 0.5], 0) if (S > 0.5).sum() > 8 else np.ones(3, np.float32)
        R = np.clip(np.where(den > 0.02, num / np.maximum(den, 1e-4), glob[None, None, :]), 0.25, 4.0).astype(np.float32)
        B_pl = np.clip(pl * cv2.resize(R, (w, h), interpolation=cv2.INTER_LINEAR), 0, 1)
        scr = chroma(f) > 0.6 * C0                                   # pixels that are clearly screen in this frame
        if scr.sum() < 200:
            return None
        e_pl = float(np.abs(f[scr] - B_pl[scr]).mean())
        e_est = float(np.abs(f[scr] - B_est[scr]).mean())
        if e_pl > 1.25 * e_est + 0.01:                              # does not fit this frame: fall back, do not make the key worse
            self.plate_rejected += 1
            return None
        self.plate_frames += 1
        return B_pl.astype(np.float32)

    @staticmethod
    def _nearest_solid(f_src: np.ndarray, solid: np.ndarray, h: int, w: int, c: int | None = None, p: dict | None = None):
        """colour of the nearest solid subject pixels (blurred, widening until every pixel has one) and where such a colour exists.
        The colour field is smooth, so it is built entirely on shrunken copies (each level blurred at its own size, combined at the finest
        small size) and scaled up ONCE - the full-size version spent most of the keyer's time on whole-frame array work.
        f_src is the frame; when c is given the screen colour is removed from it first (borrowed colours must not carry spill)."""
        sig = max(3.0, min(h, w) / 120)
        d0 = int(min(32, max(1, sig // 3)))
        w0, h0 = max(1, w // d0), max(1, h // d0)
        f_small = cv2.resize(f_src, (w0, h0), interpolation=cv2.INTER_AREA)
        if c is not None: f_small = ScreenKeyer._despill(f_small, c, p or {})
        s_small = cv2.resize(solid, (w0, h0), interpolation=cv2.INTER_AREA)
        near = np.zeros_like(f_small); got = np.zeros((h0, w0, 1), bool)
        for sg in (sig, sig * 4, sig * 16):
            d = int(min(32, max(1, sg // 3)))
            k = max(1, d // d0)                                   # this level's extra shrink relative to the finest small size
            if k == 1:
                den = cv2.GaussianBlur(s_small, (0, 0), sg / d0)[..., None]
                num = cv2.GaussianBlur(f_small * s_small[..., None], (0, 0), sg / d0)
            else:
                ws, hs = max(1, w0 // k), max(1, h0 // k)
                ss = cv2.resize(s_small, (ws, hs), interpolation=cv2.INTER_AREA)
                fs = cv2.resize(f_small * s_small[..., None], (ws, hs), interpolation=cv2.INTER_AREA)
                den = cv2.resize(cv2.GaussianBlur(ss, (0, 0), sg / (d0 * k)), (w0, h0), interpolation=cv2.INTER_LINEAR)[..., None]
                num = cv2.resize(cv2.GaussianBlur(fs, (0, 0), sg / (d0 * k)), (w0, h0), interpolation=cv2.INTER_LINEAR)
            cand = num / np.maximum(den, 1e-4)
            use = (den > 0.02) & ~got
            near = np.where(use, cand, near); got |= den > 0.02
        near = cv2.resize(np.clip(near, 0, 1).astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
        got = cv2.resize(got[..., 0].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)[..., None]
        return near, got

    @staticmethod
    def _despill(x: np.ndarray, c: int, p: dict) -> np.ndarray:
        """Remove only what is unmistakably screen colour: the screen channel rising ABOVE both others. Skin (red > green > blue) is never
        touched; a green-tinted beanie or blurred-finger edge is pulled back to neutral. (The old average-based rule also shifted skin tones.)"""
        out = x.copy()
        o1, o2 = [i for i in range(3) if i != c]
        green_dominant = out[..., c] > np.maximum(out[..., o1], out[..., o2])             # skin, grey, blue/red clothing: never true
        excess = np.maximum(0.0, out[..., c] - 0.5 * (out[..., o1] + out[..., o2]))         # down to the average of the other two = neutral
        k = min(1.0, 0.4 + 0.6 * float(p.get("spill_strength", 0.5)))
        out[..., c] = out[..., c] - np.where(green_dominant, k * excess, 0.0)
        return out

    @staticmethod
    def _clean(a: np.ndarray, p: dict) -> np.ndarray:
        from .matting import clean_matte
        a = clean_matte(a, float(p.get("matte_threshold", 0.03)), bool(p.get("despeckle", True)))
        if p.get("fill_holes", True):
            core = (a > 0.5).astype(np.uint8)
            inv = 1 - core
            n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
            hh, ww = a.shape
            for i in range(1, n):
                x, y, bw, bh, area = st[i]
                touches = x == 0 or y == 0 or x + bw >= ww or y + bh >= hh
                # a hole = small, enclosed, and not really see-through (key noise on the subject); gaps between hair strands are mostly clear screen
                if not touches and area < 0.0003 * a.size and float(a[lab == i].mean()) > 0.25:
                    a[lab == i] = 1.0
        shrink, soften = float(p.get("edge_shrink", 0.0)), float(p.get("edge_soften", 0.0))
        if shrink > 0:
            r = max(1, int(round(shrink)))
            a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        if soften > 0:
            a = cv2.GaussianBlur(a, (0, 0), soften)
        return a.astype(np.float32)

    @property
    def screen_name(self) -> str:
        return "blue" if self.c == 2 else "green"
