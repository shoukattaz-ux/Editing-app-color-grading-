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


def _dom(f: np.ndarray, c: int) -> np.ndarray:
    others = [i for i in range(3) if i != c]
    return f[..., c] - np.maximum(f[..., others[0]], f[..., others[1]])


def _hex_channel(hexc: str) -> int:
    v = [int(hexc.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return int(np.argmax(v))


class ScreenKeyer:
    def __init__(self, p: dict):
        self.p = p
        self.prev_a: np.ndarray | None = None
        self.K: np.ndarray | None = None          # tracked screen colour (0..1)
        self.c: int | None = None                 # dominant channel of the screen: 1 = green, 2 = blue
        self.fixed_c = None if p.get("auto_screen", True) else _hex_channel(p["key_color"])

    # -- screen colour ---------------------------------------------------------------------------------------------
    def _find_screen(self, f: np.ndarray) -> None:
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
        K = np.median(f[sel], 0) if sel.sum() > 200 else (self.K if self.K is not None else np.array([0.2, 0.7, 0.2]))
        self.K = K if self.K is None or c != self.c else 0.7 * self.K + 0.3 * K
        self.c = c

    def __call__(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        p = self.p
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
        D0 = max(float(_dom(K[None, None, :], c)[0, 0]), 0.1)
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
        Dloc = np.maximum(_dom(B, c), 0.1)
        d = dom / Dloc
        n0, n1 = float(p["similarity"]), float(p["blend"]) * 0.5
        a = np.clip((1.0 - d - n0) / max(1e-3, 1.0 - n0 - n1), 0.0, 1.0).astype(np.float32)
        f_clean = self._despill(f, c, p) if p.get("spill_suppression", True) else f      # borrowed colours must not carry spill
        core = cv2.erode((a > 0.995).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(np.float32)      # safely inside the subject: no mixed pixels
        near, got = self._nearest_solid(f_clean, core, h, w)
        a = self._clean(a, p)
        # temporal smoothing where nothing moved (large changes pass straight through: no ghosting)
        ts = float(p.get("temporal_smooth", 0.5))
        if ts > 0 and self.prev_a is not None and self.prev_a.shape == a.shape:
            wgt = np.where(np.abs(a - self.prev_a) > 0.3, 1.0, 1.0 - 0.6 * ts).astype(np.float32)
            a = self.prev_a + (a - self.prev_a) * wgt
        self.prev_a = a
        if not p.get("edge_color_fix", True):
            return a, None
        # solve the subject colour back out of the mix and despill it
        F = np.clip((f - (1.0 - a)[..., None] * B) / np.maximum(a, 0.05)[..., None], 0, 1)
        # where almost everything is screen the solve divides noise by a tiny alpha: lean on the nearest solid subject colour instead
        conf = np.clip(a / 0.6, 0.0, 1.0)[..., None]
        F = np.where(got, F * conf + np.clip(near, 0, 1) * (1 - conf), F)
        t = np.clip((1.0 - a) * 8.0, 0.0, 1.0)[..., None]
        fg = np.clip(f * (1 - t) + F * t, 0, 1)
        if p.get("spill_suppression", True):
            fg = self._despill(fg, c, p)
            # thin edge band only: motion-blurred fingers and fabric fringes keep an olive-yellow rim (red about equal to green) that the rule
            # above does not see. Cap the screen channel at 85% of the larger other one there; solid areas (alpha >= 0.97) are never touched.
            o1, o2 = [i for i in range(3) if i != c]
            cap = 0.85 * np.maximum(fg[..., o1], fg[..., o2])
            edge = (a < 0.97) & (a > 0.02)
            fg[..., c] = np.where(edge, np.minimum(fg[..., c], cap), fg[..., c])
        return a, np.clip(fg, 0, 1).astype(np.float32)

    @staticmethod
    def _nearest_solid(f_clean: np.ndarray, solid: np.ndarray, h: int, w: int):
        """colour of the nearest solid subject pixels (blurred, widening until every pixel has one) and where such a colour exists"""
        sig = max(3.0, min(h, w) / 120)
        near = np.zeros_like(f_clean); got = np.zeros((h, w, 1), bool)

        def blur(img: np.ndarray, sg: float) -> np.ndarray:
            # a wide Gaussian costs time proportional to its width AND the picture size (90 % of the keyer's time at 720p). The colour field it makes
            # is smooth anyway, so blur a shrunken copy (sigma ~3 px there) and scale back up: same result to within rounding, far faster.
            d = int(min(32, max(1, sg // 3)))
            if d == 1: return cv2.GaussianBlur(img, (0, 0), sg)
            small = cv2.resize(img, (max(1, w // d), max(1, h // d)), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), sg / d)
            return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)

        for sg in (sig, sig * 4, sig * 16):
            den = blur(solid, sg)[..., None]
            cand = blur(f_clean * solid[..., None], sg) / np.maximum(den, 1e-4)
            use = (den > 0.02) & ~got
            near = np.where(use, cand, near); got |= den > 0.02
        return np.clip(near, 0, 1), got

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
