"""Compiles an execution Plan into an FFmpeg argument list (never a shell string)."""
from __future__ import annotations
import os, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .blueprint import Operation
from .studio import resolve as resolve_look
from .planner import Plan, _dominant
from .animate import AnimChain
from .keyframes import to_expr
from .media import supports_timeline, ffmpeg_bin, encoders, _try_encoder


@dataclass
class RenderOptions:
    scale_width: int | None = None       # proxy/preview: downscale to this width (aspect kept)
    crf: int | None = None
    preset: str | None = None
    t_start: float | None = None         # partial render / single frame
    t_end: float | None = None
    audio: bool = True
    encoder: str = "cpu"                 # cpu | auto | explicit encoder name
    frame_only: bool = False             # emit one PNG to stdout


@dataclass
class Compiled:
    args: list[str]
    filtergraph: str
    work_files: list[str] = field(default_factory=list)
    out_duration: float = 0.0


def esc_path(p: str) -> str:
    """Escape a filesystem path for use inside a single-quoted filter option value."""
    return (p.replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
             .replace(",", r"\,").replace("[", r"\[").replace("]", r"\]").replace(";", r"\;"))


def hex0x(c: str) -> str:
    return "0x" + c.lstrip("#")


def find_font() -> str | None:
    cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/TTF/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
             "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
             "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]
    if os.environ.get("EVP_FONT"): cands.insert(0, os.environ["EVP_FONT"])
    return next((c for c in cands if os.path.isfile(c)), None)


def lgg_filter(lift, gamma, gain) -> str:
    """Classic per-channel lift / gamma / gain (what colour wheels mean), as one exact 1-D LUT per channel:
         x = in;  y = (1 + gain) * (x + lift * (1 - x));  out = max(y, 0) ** 2**(-gamma)
       lift moves the black point (white fixed), gain scales (black fixed), gamma bends the midtones (black and white fixed)."""
    ex = []
    for c in range(3):
        l, g, m = float(lift[c]), float(gamma[c]), float(gain[c])
        ex.append(f"'clip(pow(max(0,{1 + m:.6f}*(val/maxval+({l:.6f})*(1-val/maxval))),{2 ** -g:.6f})*maxval,0,maxval)'")
    return "lutrgb=r=" + ex[0] + ":g=" + ex[1] + ":b=" + ex[2]


RGB_FILTERS = {"exposure", "curves", "colorchannelmixer", "vibrance", "lutrgb", "lut3d", "colorbalance"}


def fname(f: str) -> str:
    return f.split("=", 1)[0].split("@", 1)[0]


class ColorSpec:
    """The clip's YUV<->RGB matrix and range, made explicit.

    FFmpeg's automatic conversions decode with the frame's tag but encode RGB with BT.601 whatever the tag says, so every
    RGB-domain step on HD footage would shift colours. All conversions in the graph therefore name the matrix. The graph
    is 4:4:4 between the (lossless) chroma upsample at the start and the single chroma downsample at the end.
    """
    def __init__(self, matrix: str, rng: str):
        self.matrix, self.range = matrix, rng

    @staticmethod
    def of(vid) -> "ColorSpec":
        tag = (getattr(vid, "color_space", "") or "").lower()
        if tag == "bt709": m = "bt709"
        elif tag in ("smpte170m", "bt470bg", "bt470m"): m = "bt601"
        elif tag.startswith("bt2020"): m = "bt2020"
        else: m = "bt709" if vid.height >= 720 else "bt601"       # untagged: players assume 709 for HD, 601 for SD
        # the working range is always limited ("tv"): full-range sources (phones: yuvj420p) are squeezed once on entry, as always
        return ColorSpec(m, "tv")

    def tag_filter(self) -> str:
        cs, pr = {"bt709": ("bt709", "bt709"), "bt601": ("smpte170m", "smpte170m"), "bt2020": ("bt2020nc", "bt2020")}[self.matrix]
        return f"setparams=colorspace={cs}:color_primaries={pr}:range={self.range}"

    def to_444(self) -> str:                       # 4:2:0 -> 4:4:4 by duplication: lossless, undone exactly by finish()
        return "scale=flags=neighbor+accurate_rnd,format=yuv444p"

    def yuv_to_rgb(self, fmt: str = "gbrp16le") -> str:
        return f"scale=in_color_matrix={self.matrix}:in_range={self.range},format={fmt}"

    def rgb_to_yuv(self, fmt: str = "yuv444p") -> str:
        return f"scale=out_color_matrix={self.matrix}:out_range={self.range},format={fmt}"

    def wrap_rgb(self, filters: list[str]) -> list[str]:
        """put each run of RGB-domain filters between a YUV->RGB and an RGB->YUV conversion (16-bit RGB inside)"""
        out: list[str] = []; run: list[str] = []
        def flush():
            if run: out.extend([self.yuv_to_rgb(), *run, self.rgb_to_yuv()]); run.clear()
        for f in filters:
            if fname(f) in RGB_FILTERS: run.append(f)
            else: flush(); out.append(f)
        flush()
        return out

    def finish(self, fmt: str) -> str:             # the one chroma downsample (box average) + tags for players and the encoder
        return f"scale=flags=area+accurate_rnd+full_chroma_int,format={fmt},{self.tag_filter()}"


def curve_str(pts) -> str:
    return " ".join(f"{x:g}/{y:g}" for x, y in pts)


class _Graph:
    def __init__(self):
        self.parts: list[str] = []
        self.n = 0

    def label(self, hint="v") -> str:
        self.n += 1
        return f"{hint}{self.n}"

    def add(self, ins: list[str], chain: str, out: str | None = None) -> str:
        out = out or self.label()
        self.parts.append("".join(f"[{i}]" for i in ins) + chain + f"[{out}]")
        return out

    def split(self, src: str) -> tuple[str, str]:
        a, b = self.label("s"), self.label("s")
        self.parts.append(f"[{src}]split[{a}][{b}]")
        return a, b

    def text(self) -> str:
        return ";".join(self.parts)


def compile_plan(plan: Plan, source: str, out_path: str, opts: RenderOptions | None = None,
                 work_dir: str | None = None) -> Compiled:
    opts = opts or RenderOptions()
    bp, vid, out = plan.blueprint, plan.video, plan.blueprint.output
    fps = out.fps
    W, H = out.width, out.height
    if opts.scale_width:
        W = max(2, int(opts.scale_width) // 2 * 2)
        H = max(2, int(round(out.height * W / out.width)) // 2 * 2)
    half = 0.5 / fps
    g = _Graph()
    cs = ColorSpec.of(vid)
    inputs: list[list[str]] = []
    work_files: list[str] = []
    asset_input: dict[str, int] = {}
    wd = work_dir or tempfile.mkdtemp(prefix="evp_")
    seek = opts.t_start or 0.0
    seek_f = round(seek * fps)          # all streams use frame-index timestamps (time base 1/fps) so they align exactly
    # ---- cuts: the pieces of the source needed for the requested timeline range (None = uncut source)
    pieces = None
    if plan.edl:
        from . import edl as _edl
        count = 1 if opts.frame_only else (None if opts.t_end is None else max(1, round(opts.t_end * fps) - seek_f))
        pieces = _edl.pieces_for_range(plan.edl, seek_f, count)
        if not pieces:
            raise RuntimeError(f"timeline range starting at frame {seek_f} is empty")
        multi = not _edl.is_in_order(pieces)          # reordered / duplicated / overlapping pieces: one decoder each
        for a, _b in (pieces[1:] if multi else []):
            inputs.append((["-ss", f"{(a - 0.5) / fps:.6f}"] if a > 0 else []) + ["-i", source])
    else:
        multi = False

    # last timeline frame (exclusive) this render touches: keyframe command files stop there
    range_end_f = seek_f + 1 if opts.frame_only else (round(opts.t_end * fps) if opts.t_end is not None else plan.total_frames)

    def chain_for(op: Operation, tag: str = "") -> AnimChain:
        """animation context for one linear chain of `op` (first frame = where this render starts inside the op)"""
        return AnimChain(op, fps, seek_f, range_end_f, wd, work_files, tag)

    def pad_after(ref) -> int:
        """frames needed after an analysis file ends so the stream covers the whole timeline (+2 spare)"""
        return max(0, plan.total_frames - ref.start_frame - ref.frames) + 2

    def enable(op: Operation) -> str | None:
        if op.start_frame <= 0 and op.end_frame >= plan.total_frames:
            return None
        return f"gte(t\\,{op.start_frame / fps - half:.6f})*lt(t\\,{op.end_frame / fps - half:.6f})"

    def asset_idx(aid: str) -> int:
        if aid in asset_input: return asset_input[aid]
        a = bp.assets[aid]
        if a.resolved is None: raise RuntimeError(f"asset {aid} unresolved")
        if a.type == "image": grp = ["-loop", "1", "-framerate", f"{fps:g}", "-i", a.resolved]
        elif a.type == "video": grp = ["-stream_loop", "-1", "-i", a.resolved]
        else: raise RuntimeError("LUT assets are not stream inputs")
        inputs.append(grp)
        asset_input[aid] = len(inputs)
        return asset_input[aid]

    def ranged_block(cur: str, op: Operation, build: Callable[[str], str]) -> str:
        """Run build(stream)->stream over op's time range only (split + overlay)."""
        en = enable(op)
        if en is None: return build(cur)
        keep, work = g.split(cur)
        proc = build(work)
        return g.add([keep, proc], f"overlay=enable='{en}':shortest=1:format=yuv444,format=yuv444p")

    def apply_ranged(cur: str, filters: list[str], op: Operation, pre: list[str] | None = None) -> str:
        """pre = filters (sendcmd) that must run in the same chain, in front, without a time range of their own"""
        pre = pre or []
        if not filters: return cur
        en = enable(op)
        chain = lambda: ",".join([*pre, *cs.wrap_rgb(filters)])
        if en is None: return g.add([cur], chain())
        # RGB-domain filters sit inside a conversion pair; `enable` can't skip that pair, so frames outside the
        # range would still be round-tripped. A split + overlay copies them untouched instead.
        if not any(fname(f) in RGB_FILTERS for f in filters) and all(supports_timeline(fname(f)) for f in filters):
            return g.add([cur], ",".join([*pre, *(f"{f}:enable='{en}'" for f in filters)]))
        return ranged_block(cur, op, lambda s: g.add([s], chain()))

    # ---- base: source -> constant fps, output size
    fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,{cs.to_444()},{cs.tag_filter()}")
    if pieces is None:
        cur = g.add(["0:v"], f"fps={fps:g},setpts=N+{seek_f}," + fit, "v0")
    else:
        s0 = pieces[0][0]
        if not multi:                                  # one decoder, split + trim per piece (no buffering when in order)
            src = g.add(["0:v"], f"fps={fps:g},setpts=N")
            if len(pieces) > 1:
                outs = [g.label("p") for _ in pieces]
                g.parts.append(f"[{src}]split={len(pieces)}" + "".join(f"[{o}]" for o in outs))
            else:
                outs = [src]
            segs = [g.add([o], f"trim=start_frame={a - s0}:end_frame={b - s0},setpts=N") for o, (a, b) in zip(outs, pieces)]
        else:                                          # one input per piece
            segs = [g.add([f"{k}:v"], f"fps={fps:g},setpts=N,trim=end_frame={b - a},setpts=N") for k, (a, b) in enumerate(pieces)]
        joined = segs[0] if len(segs) == 1 else g.add(segs, f"concat=n={len(segs)}:v=1:a=0")
        # concat changes the time base, so express timestamps in seconds (independent of TB): frame N -> (N+seek)/fps
        # ... then fps= puts the stream back on the exact 1/fps time base every other stream uses (a mismatched time base made
        # overlays drop their last frame at EOF)
        cur = g.add([joined], f"setpts=(N+{seek_f})/({fps:g}*TB),fps={fps:g}," + fit, "v0")

    def make_keyed(src: str, k: Operation) -> str:
        p = k.params
        if k.type == "background_removal" and p["mode"] == "ai":
            ref = plan.mattes.get(k.id)
            if ref is None:
                raise RuntimeError(f"AI matte for '{k.id}' was not generated")
            inputs.append(["-i", ref.path])
            # matte file covers [start_frame, start_frame+frames): pad before with opaque, clone after, align to (seeked) timeline
            m = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:start_mode=add:color=white:stop={pad_after(ref)}:stop_mode=clone,"
                      f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H},format=gray")
            colour = src
            m, m_edge = g.split(m) if ref.fgr_path else (m, None)
            if ref.fgr_path:
                inputs.append(["-i", ref.fgr_path])
                fg = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:start_mode=add:stop={pad_after(ref)}:stop_mode=clone,"
                           f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H}")
                # take the model's foreground colour only in semi-transparent edges (weight = (1-alpha)*4), keep the
                # (possibly already processed) frame elsewhere
                w = g.add([m_edge], "lut=y='clip((255-val)*4\\,0\\,255)',format=gray")
                a_rgb, b_rgb = g.add([src], cs.yuv_to_rgb("gbrp")), g.add([fg], "format=gbrp")
                colour = g.add([a_rgb, b_rgb, g.add([w], "format=gbrp")], "maskedmerge")
            return g.add([g.add([colour], "format=rgba"), m], "alphamerge,format=rgba")
        erode, feather = p.get("edge_erode", 0), p.get("edge_feather", 0.0)
        if k.type == "background_removal":
            q = p["quality"]
            if p["edge_refinement"] and q != "draft":
                erode, feather = (1, 0.8) if q == "standard" else (1, 1.5)
            else:
                erode, feather = 0, 0.0
        an = chain_for(k)
        chain = [an.add("chromakey", ("similarity", "blend"), lambda q: {"color": hex0x(q["key_color"]), "similarity": f"{q['similarity']:.4f}", "blend": f"{q['blend']:.4f}"})]
        dom = _dominant(p["key_color"])
        if p["spill_suppression"] and dom:
            chain.append(an.add("despill", ("spill_strength",), lambda q: {"type": dom, "mix": f"{q['spill_strength']:.3f}", "expand": "0"}))
        chain.append(cs.yuv_to_rgb("rgba"))
        keyed = g.add([src], ",".join([*an.sendcmd(), *chain]))
        if erode or feather > 0:
            a, b = g.split(keyed)
            m = "alphaextract" + ",erosion=coordinates=255" * erode + (f",gblur=sigma={feather:.3f}" if feather > 0 else "")
            keyed = g.add([a, g.add([b], m)], "alphamerge,format=rgba")
        return keyed

    shift = f",setpts=N+{seek_f}" if seek_f else ""   # keep generated streams aligned with a seeked source

    def bg_stream(r: Operation | None, k: Operation) -> str:
        if r is None or not r.params.get("asset_id"):
            color = r.params["color"] if r else k.params["background_color"]
            return g.add([], f"color=c={hex0x(color)}:s={W}x{H}:r={fps:g},format=rgba{shift}")
        p = r.params
        idx = asset_idx(p["asset_id"])
        zoom_anim = "scale" in r.keyframes
        z = 1.0 if zoom_anim else p["scale"]            # animated zoom is applied LAST (any filter after a per-frame size change makes FFmpeg undo it)
        if p["fit"] in ("cover", "contain"):
            fac = f"{'max' if p['fit'] == 'cover' else 'min'}({W}/iw,{H}/ih)"
            sc = f"scale=w='trunc(iw*{fac}*{z}/2)*2':h='trunc(ih*{fac}*{z}/2)*2'"
        else:
            sc = f"scale={int(W * z) // 2 * 2}:{int(H * z) // 2 * 2}"
        an = chain_for(r, "bg")
        eqf = an.add("eq", ("brightness", "saturation"), lambda q: {"brightness": f"{q['brightness']:.4f}", "saturation": f"{q['saturation']:.4f}"},
                     lambda q: q["brightness"] != 0 or q["saturation"] != 1)
        # asset stream is shifted onto the timeline FIRST so sendcmd times / expressions see timeline timestamps
        chain = [f"fps={fps:g}"] + ([f"setpts=N+{seek_f}"] if seek_f else []) + an.sendcmd() + [sc, "setsar=1", "format=rgba"]
        if p["blur"] > 0: chain.append(f"gblur=sigma={p['blur']:.3f}")
        if eqf: chain.append(eqf)
        if zoom_anim:
            zx = to_expr(r.keyframes["scale"], r.start)
            chain.append(f"scale=w='trunc(iw*({zx})/2)*2':h='trunc(ih*({zx})/2)*2':eval=frame")
        a = g.add([f"{idx}:v"], ",".join(chain))
        canvas = g.add([], f"color=c={hex0x(p['color'])}:s={W}x{H}:r={fps:g},format=rgba{shift}")
        offs = []
        for nm in ("offset_x", "offset_y"):
            offs.append(f"({to_expr(r.keyframes[nm], r.start)})" if nm in r.keyframes else f"{p[nm]}")
        per_frame = zoom_anim or "offset_x" in r.keyframes or "offset_y" in r.keyframes
        return g.add([canvas, a], f"overlay={'eval=frame:' if per_frame else ''}x='(W-w)/2+{offs[0]}*W':y='(H-h)/2+{offs[1]}*H':shortest=1,format=rgba")

    def splitn(src: str, n: int) -> list[str]:
        outs = [g.label("s") for _ in range(n)]
        g.parts.append(f"[{src}]split={n}" + "".join(f"[{o}]" for o in outs))
        return outs

    scale_px = H / 1080.0

    def integrate(bgs: str, fg: str, L: dict) -> tuple[str, str]:
        """make the keyed subject belong to the new scene: depth of field, the background's colour on the subject, its light wrapping the edges"""
        if L["depth"] > 0:
            bgs = g.add([bgs], f"gblur=sigma={L['depth'] * 8 * scale_px:.3f}")
        amb, wrap = L["ambient_match"], L["light_wrap"]
        if amb <= 0 and wrap <= 0:
            return bgs, fg
        b_main, b_amb, b_wrap = splitn(bgs, 3)
        f_col, f_a = splitn(fg, 2)
        a_keep, a_edge, a_blur = splitn(g.add([f_a], "alphaextract"), 3)
        col = g.add([f_col], "format=gbrp")
        for unused in ([b_amb] if amb <= 0 else []) + ([b_wrap, a_edge, a_blur] if wrap <= 0 else []):
            g.parts.append(f"[{unused}]nullsink")
        if amb > 0:
            # light falling on the subject = the background heavily blurred, reduced to its colour (luma fixed at mid-grey so the
            # subject's brightness is untouched), applied as a multiplicative cast: out = in * (1 + k * (tint/128 - 1))
            tint = g.add([b_amb], f"scale=iw/8:ih/8:flags=area,gblur=sigma=3,scale={W}:{H}:flags=bicubic,{cs.rgb_to_yuv()},lutyuv=y=128,{cs.yuv_to_rgb('gbrp')}")
            col = g.add([col, tint], f"blend=all_expr='clip(A*(1+{amb * 1.2:.3f}*(B/128-1))\\,0\\,255)'")
        if wrap > 0:
            # light wrap: just inside the subject's edge (alpha * (1 - blurred alpha)) the blurred, brightened background shows through
            ww = max(1.0, L["wrap_width"] * scale_px)
            edge = g.add([a_edge, g.add([a_blur], f"gblur=sigma={ww:.3f}")], f"blend=all_expr='clip(A*(255-B)/128*{wrap * 0.8:.3f}\\,0\\,255)',format=gbrp")
            light = g.add([b_wrap], f"gblur=sigma={ww * 0.5:.3f},format=gbrp,lutrgb=r='clip(val*1.3\\,0\\,maxval)':g='clip(val*1.3\\,0\\,maxval)':b='clip(val*1.3\\,0\\,maxval)'")
            col = g.add([col, light, edge], "maskedmerge")
        return b_main, g.add([g.add([col], "format=rgba"), a_keep], "alphamerge,format=rgba")

    def look_block(src: str, L: dict, ref_curves=None) -> str:
        """the global finish, in RGB (curve, split-tone, bloom) then YUV (vignette, local contrast, saturation, grain)"""
        from .studio import tint_vector
        rgbf: list[str] = []
        c = L["contrast"]
        if c > 0:
            rgbf.append("curves=master='" + curve_str([(0, 0), (0.25, 0.25 - 0.07 * c), (0.5, 0.5), (0.75, 0.75 + 0.07 * c), (1, 1)]) + "'")
        if L["tint_amount"] > 0:
            sr, sg, sb = tint_vector(L["shadow_tint"], L["tint_amount"]); hr, hg, hb = tint_vector(L["highlight_tint"], L["tint_amount"])
            rgbf.append(f"colorbalance=rs={sr}:gs={sg}:bs={sb}:rh={hr}:gh={hg}:bh={hb}")
        rgb = g.add([src], ",".join([cs.yuv_to_rgb(), *rgbf]))
        if L["glow"] > 0:
            base, hi = g.split(rgb)
            thr, gain_ = L["glow_threshold"], 1.0 / max(1e-3, 1.0 - L["glow_threshold"])
            hi = g.add([hi], "lutrgb=" + ":".join(f"{ch}='clip((val/maxval-{thr:.3f})*{gain_:.3f}\\,0\\,1)*maxval'" for ch in "rgb")
                       + f",gblur=sigma={max(1.0, L['glow_radius'] * scale_px):.3f}")
            rgb = g.add([g.add([base], "format=gbrp"), g.add([hi], "format=gbrp")], f"blend=all_mode=screen:all_opacity={L['glow']:.3f}")
        tail = [cs.rgb_to_yuv()]
        if L["vignette"] > 0:
            tail.append(f"vignette=angle={1.1 - 0.6 * L['vignette']:.3f}:mode=forward:eval=init")
        if L["clarity"] > 0:
            tail.append(f"unsharp=lx=13:ly=13:la={0.7 * L['clarity']:.3f}:cx=13:cy=13:ca=0")
        if L["saturation"] != 1:
            tail.append(f"eq=saturation={L['saturation']:.3f}")
        if ref_curves:                                   # correction measured against the reference picture
            tail += cs.wrap_rgb(["curves=" + ":".join(f"{n}='{curve_str(pts)}'" for n, pts in zip(("red", "green", "blue"), ref_curves))])
        if L["grain"] > 0:
            tail.append(f"noise=alls={L['grain'] * 14:.2f}:allf=t+u")
        return g.add([rgb], ",".join(tail))

    def place_subject(fg: str, p: dict) -> str:
        """resize / move the keyed subject on a transparent canvas (so everything after it - integration, shadow - lines up with the result)"""
        sw, sh_ = max(2, int(W * p["subject_scale"]) // 2 * 2), max(2, int(H * p["subject_scale"]) // 2 * 2)
        x = round((W - sw) / 2 + p["subject_x"] * W)
        y = round((H - sh_ if p["subject_anchor"] == "bottom" else (H - sh_) / 2) + p["subject_y"] * H)
        big = g.add([fg], f"scale={sw}:{sh_}:flags=lanczos,format=rgba")
        canvas = g.add([], f"color=c=black@0.0:s={W}x{H}:r={fps:g},format=rgba{shift}")
        return g.add([canvas, big], f"overlay=x={x}:y={y}:shortest=1:format=auto,format=rgba")

    def cast_shadow(fg: str, p: dict) -> str:
        """the subject's silhouette, blurred, offset and darkened, as a transparent black layer to lay on the background"""
        dx, dy = round(p["shadow_dx"] * W), round(p["shadow_dy"] * H)
        m = max(abs(dx), abs(dy), 1)
        # shift in RGBA with a transparent fill (a gray plane's "black" is 16, not 0, and would leave a veil at the vacated edges)
        a = g.add([fg], f"pad={W + 2 * m}:{H + 2 * m}:{m + dx}:{m + dy}:black@0,crop={W}:{H}:{m}:{m},"
                        f"alphaextract,gblur=sigma={max(0.5, p['shadow_blur'] * scale_px):.3f},lut=y='val*{p['shadow']:.4f}'")
        black = g.add([], f"color=c=black:s={W}x{H}:r={fps:g},format=rgba{shift}")
        return g.add([black, a], "alphamerge,format=rgba")

    def composite(cur: str, k: Operation, r: Operation | None) -> str:
        rng = r or k
        L = plan.look_for.get(r.id) if r else None
        look = resolve_look(L.params) if L else None
        rp = r.params if r else {}
        moved = bool(r) and (rp["subject_scale"] != 1 or rp["subject_x"] != 0 or rp["subject_y"] != 0)
        shadow = bool(r) and rp["shadow"] > 0
        def build(src: str) -> str:
            bgs, fg = bg_stream(r, k), make_keyed(src, k)
            if moved: fg = place_subject(fg, rp)
            sh = None
            if shadow:
                fg, f_sh = splitn(fg, 2)
                sh = cast_shadow(f_sh, rp)
            if look: bgs, fg = integrate(bgs, fg, look)
            if sh: bgs = g.add([bgs, sh], "overlay=shortest=1:format=auto,format=rgba")
            return g.add([bgs, fg], f"overlay=shortest=1:format=auto,{cs.rgb_to_yuv()}")
        return ranged_block(cur, rng, build)

    pre_cmd: list[list[str]] = [[]]          # sendcmd filter(s) produced by the last grade_filters() call

    def grade_filters(op: Operation) -> list[str]:
        p = op.params
        f: list[str] = []
        presets = {
            "cinematic_teal_orange": ["colorbalance=rs=-0.08:bs=0.12:rh=0.12:gh=0.02:bh=-0.10",
                                      "curves=master='0/0 0.25/0.21 0.75/0.79 1/1'", "eq=saturation=1.08"],
            "warm_film": ["colorchannelmixer=rr=1.06:bb=0.92", "curves=master='0/0.02 0.5/0.52 1/0.98'", "eq=saturation=1.05"],
            "cool_blue": ["colorchannelmixer=rr=0.93:bb=1.08", "eq=saturation=0.95"],
            "bleach_bypass": ["eq=saturation=0.6:contrast=1.25"],
            "faded_matte": ["curves=master='0/0.08 0.5/0.5 1/0.92'", "eq=saturation=0.9"],
            "high_contrast_bw": ["eq=saturation=0:contrast=1.35"],
        }
        f += presets.get(p["preset"], [])
        an = chain_for(op)
        def tonal(q: dict) -> dict[str, str]:
            b, s, hi, w = q["blacks"], q["shadows"], q["highlights"], q["whites"]
            pts = {0.0: max(0.0, 0.1 * b), 0.25: min(1, max(0, 0.25 + 0.12 * s)), 0.5: 0.5,
                   0.75: min(1, max(0, 0.75 + 0.12 * hi)), 1.0: min(1.0, 1.0 + 0.1 * min(0, w))}
            if b < 0: pts[round(-0.1 * b, 4)] = 0.0
            if w > 0: pts[round(1 - 0.1 * w, 4)] = 1.0
            return {"master": f"'{curve_str(sorted(pts.items()))}'"}
        def mixer(q: dict) -> dict[str, str]:
            tt, ti = q["temperature"] / 100.0, q["tint"] / 100.0
            return {"rr": f"{1 + 0.2 * tt + 0.05 * ti:.4f}", "gg": f"{1 - 0.2 * ti:.4f}", "bb": f"{1 - 0.2 * tt + 0.05 * ti:.4f}"}
        f += [x for x in (
            an.add("exposure", ("exposure",), lambda q: {"exposure": f"{q['exposure']:.4f}"}, lambda q: q["exposure"] != 0),
            an.add("curves", ("blacks", "shadows", "highlights", "whites"), tonal, lambda q: any((q["blacks"], q["shadows"], q["highlights"], q["whites"]))),
            an.add("colorchannelmixer", ("temperature", "tint"), mixer, lambda q: bool(q["temperature"] or q["tint"])),
            an.add("eq", ("contrast", "brightness", "saturation", "gamma"),
                   lambda q: {"contrast": f"{q['contrast']:.4f}", "brightness": f"{q['brightness']:.4f}", "saturation": f"{q['saturation']:.4f}", "gamma": f"{q['gamma']:.4f}"},
                   lambda q: (q["contrast"], q["brightness"], q["saturation"], q["gamma"]) != (1, 0, 1, 1)),
            an.add("vibrance", ("vibrance",), lambda q: {"intensity": f"{q['vibrance']:.4f}"}, lambda q: q["vibrance"] != 0)) if x]
        pre_cmd[0] = an.sendcmd()
        if p["lift"] or p["gamma_rgb"] or p["gain"]:
            f.append(lgg_filter(p["lift"] or [0, 0, 0], p["gamma_rgb"] or [0, 0, 0], p["gain"] or [0, 0, 0]))
        cv = [f"{n}='{curve_str(p[k])}'" for n, k in (("master", "curve_master"), ("red", "curve_red"),
              ("green", "curve_green"), ("blue", "curve_blue")) if p[k]]
        if cv: f.append("curves=" + ":".join(cv))
        return f

    audio_filters: list[str] = []
    for op in plan.steps:
        t, p = op.type, op.params
        if t in ("chroma_key", "background_removal"):
            if op.id in plan.consumed_keyers: continue
            cur = composite(cur, op, None)
        elif t == "background_replacement":
            cur = composite(cur, next(o for o in plan.steps if o.id == plan.alpha_source[op.id]), op)
        elif t == "color_grading":
            main = grade_filters(op)
            lut_id = p["lut_asset_id"]
            if lut_id and p["lut_intensity"] >= 1:
                main.append(f"lut3d=file='{esc_path(bp.assets[lut_id].resolved)}'")
                lut_id = None
            cur = apply_ranged(cur, main, op, pre_cmd[0])
            if lut_id:   # partial-strength LUT: blend graded vs LUT'd
                lut_path, k = esc_path(bp.assets[lut_id].resolved), p["lut_intensity"]
                def build(src: str) -> str:
                    a, b = g.split(src)
                    return g.add([g.add([b], ",".join(cs.wrap_rgb([f"lut3d=file='{lut_path}'"]))), a], f"blend=all_mode=normal:all_opacity={k:.3f}")  # first input is the "top" layer
                cur = ranged_block(cur, op, build)
        elif t == "studio_look":
            L = resolve_look(p)
            cur = ranged_block(cur, op, lambda s_: look_block(s_, L, plan.look_curves.get(op.id)))
        elif t in ("face_tone_correction", "face_relighting"):
            ref = plan.face_controls.get(op.id)
            if ref is None:
                raise RuntimeError(f"face analysis for '{op.id}' was not generated")
            inputs.append(["-i", ref.path])
            ctrl = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:stop={pad_after(ref)}:start_mode=add:stop_mode=add:color=black,"
                         f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H}:flags=bilinear,format=gbrp")
            tone, tone_pre = [], []
            if t == "face_tone_correction":
                an = chain_for(op, "ft")
                def face_mixer(q: dict) -> dict[str, str]:
                    w_, ti = q["warmth"] / 100.0, q["tint"] / 100.0
                    return {"rr": f"{1 + 0.15 * w_ + 0.04 * ti:.4f}", "gg": f"{1 - 0.12 * ti:.4f}", "bb": f"{1 - 0.15 * w_ + 0.04 * ti:.4f}"}
                mx = an.add("colorchannelmixer", ("warmth", "tint"), face_mixer, lambda q: bool(q["warmth"] or q["tint"]))
                sat = an.add("eq", ("saturation",), lambda q: {"saturation": f"{q['saturation']:.4f}"}, lambda q: q["saturation"] != 1)
                if mx: tone.append(mx)
                if sat: tone.append(sat)
                tone_pre = an.sendcmd()
            mr, mg, mb = g.label("m"), g.label("m"), g.label("m")
            if tone: g.parts.append(f"[{ctrl}]extractplanes=r+g+b[{mr}][{mg}][{mb}]")
            else: g.parts.append(f"[{ctrl}]extractplanes=g+b[{mg}][{mb}]")
            # Work in yuv444p so pixels with zero weight pass through bit-exactly (no RGB round trip);
            # only the corrected branch goes through RGB. Masks are replicated into Y, U and V.
            # nearest-neighbour 4:2:0 <-> 4:4:4 is an exact round trip (duplicate chroma, then pick it back)

            def masked(cur: str, rgb_chain: str, mask: str) -> str:
                a, b = g.split(cur)
                fx = g.add([b], f"{cs.yuv_to_rgb()},{rgb_chain},{cs.rgb_to_yuv()}")
                m1, m2, m3 = g.label("q"), g.label("q"), g.label("q")
                g.parts.append(f"[{mask}]split=3[{m1}][{m2}][{m3}]")
                mk = g.add([m1, m2, m3], "mergeplanes=0x001020:yuv444p")
                return g.add([a, fx, mk], "maskedmerge")

            if tone: cur = masked(cur, ",".join([*tone_pre, *tone]), mr)
            gain = lambda k: "lutrgb=" + ":".join(f"{c}='clip(val*{k}\\,0\\,maxval)'" for c in "rgb")
            cur = masked(cur, gain(4), mg)      # = face.UP_GAIN
            cur = masked(cur, gain(0.25), mb)   # = face.DOWN_GAIN
        elif t == "ai_upscale":
            ref = plan.upscales.get(op.id)
            if ref is None:
                raise RuntimeError(f"AI upscale for '{op.id}' was not generated")
            inputs.append(["-i", ref.path])
            u = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:stop={pad_after(ref)}:start_mode=add:stop_mode=add:color=black,"
                      f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H}:flags=lanczos:out_color_matrix={cs.matrix}:out_range={cs.range},setsar=1,format=yuv444p")
            en = enable(op)
            cur = g.add([cur, u], (f"overlay=enable='{en}':shortest=1:format=yuv444" if en else "overlay=shortest=1:format=yuv444") + ",format=yuv444p")
        elif t == "video_enhancement":
            f = []
            if p["stabilize"]: f.append(f"deshake=rx={p['stabilize_strength']}:ry={p['stabilize_strength']}")
            if p["denoise"] > 0:
                d = p["denoise"]
                f.append(f"hqdn3d={d * 8:.2f}:{d * 6:.2f}:{d * 12:.2f}:{d * 9:.2f}")
            if p["sharpen"] > 0: f.append(f"unsharp=lx=5:ly=5:la={p['sharpen']:.3f}:cx=5:cy=5:ca=0")
            cur = apply_ranged(cur, f, op)
        elif t == "text_overlay":
            tf = os.path.join(wd, f"{op.id}.txt")           # textfile + expansion=none: no filter-injection via text
            Path(tf).write_text(p["text"], encoding="utf-8")
            work_files.append(tf)
            font = find_font()
            an = chain_for(op)
            def text_opts(q: dict) -> dict[str, str]:
                o = {"textfile": f"'{esc_path(tf)}'", "expansion": "none", "fontsize": str(int(round(q["font_size"]))),
                     "fontcolor": hex0x(q["color"]), "x": f"w*{q['x']:.6f}-text_w/2", "y": f"h*{q['y']:.6f}-text_h/2"}
                if font: o["fontfile"] = f"'{esc_path(font)}'"
                if q["opacity"] != 1 or an.has("opacity"): o["alpha"] = f"{q['opacity']:.4f}"
                if q["box"]: o.update({"box": "1", "boxcolor": f"black@{q['box_opacity'] * q['opacity']:.4f}", "boxborderw": "12"})   # opacity fades the box too
                return o
            dtf = an.add("drawtext", ("x", "y", "font_size", "opacity", "box_opacity"), text_opts)
            cur = apply_ranged(cur, [dtf], op, an.sendcmd())
        elif t == "audio_gain":
            en = enable(op)
            if "gain_db" in op.keyframes:        # automation: dB expression of the timeline time, updated per audio frame
                audio_filters.append(f"volume=volume='pow(10,({to_expr(op.keyframes['gain_db'], op.start)})/20)':eval=frame" + (f":enable='{en}'" if en else ""))
            elif p["gain_db"] != 0:
                audio_filters.append(f"volume={p['gain_db']:.2f}dB" + (f":enable='{en}'" if en else ""))
            if p["fade_in"] > 0: audio_filters.append(f"afade=t=in:st={op.start:.4f}:d={p['fade_in']:.3f}")
            if p["fade_out"] > 0:
                audio_filters.append(f"afade=t=out:st={max(op.start, op.end - p['fade_out']):.4f}:d={p['fade_out']:.3f}")
        else:
            raise RuntimeError(f"compiler has no handler for '{t}' (registry/compiler mismatch)")

    g.add([cur], cs.finish("yuv422p10le" if (out.codec == "prores" and not opts.scale_width) else "yuv420p"), "vout")
    if pieces is not None and vid.has_audio and opts.audio and not opts.frame_only:
        # audio follows the same cuts, sample-accurately; a 4 ms fade at every real joint avoids clicks
        FADE = 0.004
        s0 = pieces[0][0]
        segs = []
        if not multi and len(pieces) > 1:
            ins = [g.label("q") for _ in pieces]
            g.parts.append(f"[0:a]asplit={len(pieces)}" + "".join(f"[{o}]" for o in ins)); ins_iter = iter(ins)
        for k, (a, b) in enumerate(pieces):
            if multi:
                src_label, off = f"{k}:a", (0.5 / fps if a > 0 else 0.0)
                st = off
            else:
                src_label = next(ins_iter) if len(pieces) > 1 else "0:a"
                off = 0.5 / fps if s0 > 0 else 0.0
                st = (a - s0) / fps + off
            ln = (b - a) / fps
            # apad: if the source's audio ends before its video does, pad this piece with silence to its exact length, otherwise
            # concat would pull every later piece earlier and break sync
            chain = [f"atrim=start={st:.6f}:end={st + ln:.6f}", "asetpts=PTS-STARTPTS", f"apad=whole_dur={ln:.6f}"]
            if k > 0 and pieces[k - 1][1] != a: chain.append(f"afade=t=in:st=0:d={FADE}")
            if k < len(pieces) - 1 and pieces[k + 1][0] != b: chain.append(f"afade=t=out:st={ln - FADE:.6f}:d={FADE}")
            segs.append(g.add([src_label], ",".join(chain)))
        joined = segs[0] if len(segs) == 1 else g.add(segs, f"concat=n={len(segs)}:v=0:a=1")
        g.add([joined], ",".join(audio_filters) if audio_filters and not seek_f else "anull", "aout")
    # graph-wide scaler flags: unbiased colour conversions (AI intermediates re-enter the graph as RGB)
    graph = "sws_flags=bicubic+accurate_rnd+full_chroma_int+full_chroma_inp;" + g.text()
    args = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if not opts.frame_only: args += ["-progress", "pipe:1", "-nostats"]
    # seek half a frame before the first needed frame so decoding starts exactly on it (seeking to the raw time could
    # land on the next frame, misaligning AI layers by one frame in previews)
    first_src = pieces[0][0] if pieces else seek_f
    if first_src > 0 and (pieces or opts.t_start):
        args += ["-ss", f"{max(0.0, (first_src - 0.5) / fps):.6f}"]
    args += ["-i", source]
    for grp in inputs: args += grp
    args += ["-filter_complex", graph, "-map", "[vout]"]
    if opts.frame_only:
        args += ["-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"]
        return Compiled(args, graph, work_files, 0.0)

    tl_dur = plan.total_frames / fps
    out_dur = tl_dur
    if opts.t_start is not None or opts.t_end is not None:
        out_dur = max(0.0, min(opts.t_end if opts.t_end is not None else tl_dur, tl_dur) - (opts.t_start or 0.0))
    prores = out.codec == "prores" and not opts.scale_width          # proxies stay H.264 for fast preview playback
    if vid.has_audio and opts.audio:
        if pieces is None:
            args += ["-map", "0:a:0"]
            if audio_filters and not opts.t_start: args += ["-af", ",".join(audio_filters)]
        else:
            args += ["-map", "[aout]"]
        args += ["-c:a", "pcm_s16le"] if prores else ["-c:a", "aac", "-b:a", out.audio_bitrate]
    else:
        args += ["-an"]
    args += ["-t", f"{out_dur:.4f}"]
    if prores:
        # ProRes 422 10-bit intermediate for finishing in other software (processing itself is 8-bit)
        prof = {"proxy": 0, "lt": 1, "standard": 2, "hq": 3}[out.prores_profile]
        args += ["-c:v", "prores_ks", "-profile:v", str(prof), "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-r", f"{fps:g}", out_path]
        return Compiled(args, graph, work_files, out_dur)
    enc = pick_encoder("h264" if out.codec == "prores" else out.codec, opts.encoder)
    args += ["-c:v", enc]
    if enc in ("libx264", "libx265"):
        args += ["-preset", opts.preset or out.preset]
        if out.video_bitrate and opts.crf is None: args += ["-b:v", out.video_bitrate]
        else: args += ["-crf", str(opts.crf if opts.crf is not None else out.crf)]
    else:
        args += ["-b:v", out.video_bitrate or "8M"]
    args += ["-pix_fmt", "yuv420p"]
    if out.codec == "h265": args += ["-tag:v", "hvc1"]
    if out.format == "mp4": args += ["-movflags", "+faststart"]
    args += ["-r", f"{fps:g}", out_path]
    return Compiled(args, graph, work_files, out_dur)


def pick_encoder(codec: str, choice: str) -> str:
    soft = "libx264" if codec == "h264" else "libx265"
    if choice == "cpu": return soft
    if choice == "auto":
        names = ("h264_nvenc", "h264_qsv", "h264_videotoolbox") if codec == "h264" else ("hevc_nvenc", "hevc_qsv", "hevc_videotoolbox")
        return next((n for n in names if n in encoders() and _try_encoder(n)), soft)
    return choice if choice in encoders() else soft
