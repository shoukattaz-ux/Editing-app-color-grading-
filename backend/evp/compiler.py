"""Compiles an execution Plan into an FFmpeg argument list (never a shell string)."""
from __future__ import annotations
import re
import os, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .blueprint import Operation
from .studio import resolve as resolve_look
from .planner import Plan, _dominant
from .animate import AnimChain
from .keyframes import to_expr
from .masks import write_mask
from .floorfx import reflection_gradient, band_mask as floor_band, write_gray
from .media import supports_timeline, ffmpeg_bin, ffmpeg_filters, encoders, _try_encoder, hw_decode_usable


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
    audio_only: bool = False             # render ONLY the sound of this range (parallel renders: video in segments, sound once, then joined)
    hwdecode: bool | None = None         # decode the source on the graphics card (None = automatic: Windows renders when d3d11va / dxva2 exist; never for single frames)
    view: str | None = None              # key QC view (single frames): alpha | edge | red | white | grey | checker - shows the matte instead of the finished picture


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


_BLEND_EXPR = re.compile(r"blend=all_expr='((?:[^'\\]|\\.)*)'")


def fast_blends(chain: str) -> str:
    """blend=all_expr='f(A,B)' evaluates the formula for every pixel (~0.3 s per 1080p frame each). When the formula only depends on the two
    pixel values (no position / time) and the planes are 8-bit (every such blend here is), lut2 gives the SAME result from a 256x256 table
    computed once per frame. Formulas that use X, Y, W, H, T, N ... stay a blend."""
    def sub(m: "re.Match[str]") -> str:
        e = m.group(1)
        if re.search(r"\b(X|Y|W|H|T|N|SW|SH|TOP|BOTTOM)\b", e): return m.group(0)
        x = re.sub(r"\bB\b", "y", re.sub(r"\bA\b", "x", e))
        return "lut2=" + ":".join(f"c{i}='{x}'" for i in range(4))
    return _BLEND_EXPR.sub(sub, chain)


class _Graph:
    def __init__(self):
        self.parts: list[str] = []
        self.n = 0

    def label(self, hint="v") -> str:
        self.n += 1
        return f"{hint}{self.n}"

    def add(self, ins: list[str], chain: str, out: str | None = None) -> str:
        out = out or self.label()
        self.parts.append("".join(f"[{i}]" for i in ins) + fast_blends(chain) + f"[{out}]")
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
    # transparent export: the keyer's matte becomes the alpha channel of the output (previews and proxies show a checkerboard instead)
    alpha_out = plan.transparent_key is not None and not opts.frame_only and not opts.scale_width
    alpha_stream: list[str] = []
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

    def cached_stream(path: str, ref, pad: str) -> str:
        """an analysis file (matte, colours, upscale, face control) covering timeline frames [start_frame, start_frame+frames), lined up with
        the (seeked) render: padded before / after with `pad` (tpad options). When the render starts INSIDE the file, the file is SEEKED to that
        frame - padding and throwing away all earlier frames instead made a live frame at minute 1 process ~1800 extra frames."""
        off = seek_f - ref.start_frame
        if 0 < off < ref.frames:
            inputs.append(["-ss", f"{(off - 0.5) / fps:.6f}", "-i", path])
            return g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=stop={pad_after(ref) + 2}:{pad},setpts=N+{seek_f}")
        inputs.append(["-i", path])
        if off <= 0:                                  # the render starts at or before the file: pad only the gap in between (often none)
            return g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={-off}:stop={pad_after(ref)}:{pad},setpts=N+{seek_f}")
        return g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:stop={pad_after(ref)}:{pad},"
                                            f"trim=start_frame={seek_f},setpts=N+{seek_f}")

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

    def mix(graded: str, original: str, amt: float) -> str:
        """original + (graded - original) * amt   (amt 0 = original, 1 = the effect, > 1 = pushed further). Planes are 8-bit 4:4:4, the mix is affine so it is the same in YUV as in RGB."""
        return g.add([graded, original], f"blend=all_expr='clip(B+(A-B)*{amt:.5f}\\,0\\,255)'")      # first input = A = top layer

    def amount_block(cur: str, op: Operation, build: Callable[[str], str]) -> str:
        """the master Amount of an effect: mixes the finished effect with the untouched footage (inside the effect's time range only)"""
        amt = float(op.params.get("amount", 1.0))
        if abs(amt - 1.0) < 1e-6: return ranged_block(cur, op, build)
        if amt <= 0.0005: return cur                              # 0 % = the original, exactly
        def mixed(src: str) -> str:
            keep, work = g.split(src)
            return mix(build(work), keep, amt)
        return ranged_block(cur, op, mixed)

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

    def mask_stream(m: Operation) -> str:
        path = write_mask(m.params, W, H, wd)
        work_files.append(path)
        inputs.append(["-loop", "1", "-framerate", f"{fps:g}", "-i", path])
        return g.add([f"{len(inputs)}:v"], f"fps={fps:g}" + (f",setpts=N+{seek_f}" if seek_f else ",setpts=N") + f",scale={W}:{H},format=gray")

    def make_keyed(src: str, k: Operation) -> str:
        """the keyer's rgba (subject colour + matte), then the hand-drawn garbage / keep masks corrected into the matte"""
        keyed = _make_keyed(src, k)
        ms = [m for m in plan.masks.get(k.id, []) if m.params["shape"] != "polygon" or m.params.get("polygon")]
        if not ms: return keyed
        kc, ka = splitn(keyed, 2)
        a = g.add([ka], "alphaextract")
        for m in ms:
            en = enable(m)
            opt = f":enable='{en}'" if en else ""
            expr = "A*(255-B)/255" if m.params["mode"] == "garbage" else "max(A\\,B)"
            a = g.add([a, mask_stream(m)], f"blend=all_expr='{expr}':shortest=1{opt}")
        return g.add([kc, a], "alphamerge,format=rgba")

    def _make_keyed(src: str, k: Operation) -> str:
        p = k.params
        if k.type == "background_removal" and p["mode"] in ("ai", "screen"):
            ref = plan.mattes.get(k.id)
            if ref is None:
                raise RuntimeError(f"AI matte for '{k.id}' was not generated")
            # matte file covers [start_frame, start_frame+frames): pad before with opaque, clone after, align to (seeked) timeline
            m = g.add([cached_stream(ref.path, ref, "start_mode=add:color=white:stop_mode=clone")], f"scale={W}:{H},format=gray")
            own_colour = p["mode"] == "screen" and not any(o.type in ("video_enhancement", "ai_upscale") for o in plan.steps)   # nothing changed the frame before the key
            if p["mode"] == "screen" and p["spill_suppression"] and ref.screen and not own_colour:
                # core pixels keep the (already processed) frame; take the screen's spill out of them. Edge pixels use the keyer's solved, despilled colour.
                src = g.add([src], f"{cs.yuv_to_rgb('gbrp')},despill=type={ref.screen}:mix={min(1.0, p['spill_strength'] * 1.6):.3f}:expand=0,{cs.rgb_to_yuv()}")
            colour = src
            m, m_edge = g.split(m) if ref.fgr_path else (m, None)
            if ref.fgr_path:
                fg = g.add([cached_stream(ref.fgr_path, ref, "start_mode=add:stop_mode=clone")], f"scale={W}:{H}")
                # take the model's / keyer's foreground colour only in semi-transparent edges (weight = (1-alpha)*4), keep the
                # (possibly already processed) frame elsewhere
                w = g.add([m_edge], "lut=y=255,format=gray" if own_colour else f"lut=y='clip((255-val)*{10 if p['mode'] == 'screen' else 4}\\,0\\,255)',format=gray")
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
        if r is None and plan.transparent_key == k.id:                       # preview of a transparent export: checkerboard, like an editor
            return g.add([], f"nullsrc=s={W}x{H}:r={fps:g},geq=lum='if(lt(mod(floor(X/16)+floor(Y/16)\\,2)\\,1)\\,92\\,138)':cb=128:cr=128,format=rgba{shift}")
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
        amb, wrap, ev = L["ambient_match"], L["light_wrap"], L.get("subject_exposure", 0.0)
        if amb <= 0 and wrap <= 0 and ev == 0:
            return bgs, fg
        b_main, b_amb, b_wrap = splitn(bgs, 3)
        f_col, f_a = splitn(fg, 2)
        a_keep, a_edge, a_blur = splitn(g.add([f_a], "alphaextract"), 3)
        col = g.add([f_col], "format=gbrp")
        if ev:                                                    # subject only: sit naturally in the scene's exposure
            col = g.add([col], "lutrgb=" + ":".join(f"{c}='clip(val*{2 ** ev:.4f}\\,0\\,maxval)'" for c in "rgb"))
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
        if L.get("fringe", 0) > 0: src = fringe_block(src, L["fringe"])
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
            tail.append(f"vignette=angle={0.1 + 0.9 * L['vignette']:.3f}:mode=forward:eval=init")
        if L["clarity"] > 0:
            tail.append(f"unsharp=lx=13:ly=13:la={0.7 * L['clarity']:.3f}:cx=13:cy=13:ca=0")
        if L["saturation"] != 1:
            tail.append(f"eq=saturation={L['saturation']:.3f}")
        if ref_curves:                                   # correction measured against the reference picture
            tail += cs.wrap_rgb(["curves=" + ":".join(f"{n}='{curve_str(pts)}'" for n, pts in zip(("red", "green", "blue"), ref_curves))])
        if L["grain"] > 0:
            tail.append(f"noise=alls={L['grain'] * 14:.2f}:allf=t+u")
        return g.add([rgb], ",".join(tail))

    def subject_camera(fg: str, L: dict) -> str:
        """camera match on the SUBJECT only: black point, softness (edge too) or sharpening, grain. The alpha is handled apart from the colour."""
        s, gr, bl = float(L.get("match_softness", 0.0)), float(L.get("subject_grain", 0.0)), float(L.get("match_blacks", 0.0))
        if abs(s) < 1e-3 and gr <= 0 and abs(bl) < 1e-3: return fg
        col, alpha = splitn(fg, 2)
        a = g.add([alpha], "alphaextract")
        if s > 0: a = g.add([a], f"gblur=sigma={max(0.3, 0.8 * s * scale_px):.3f}")          # the edge softens with the picture
        rgb_f = []
        if abs(bl) >= 1e-3:
            e = (f"clip(val+{bl:.4f}*(maxval-val)\\,0\\,maxval)" if bl > 0 else f"clip((val-{-bl:.4f}*maxval)/{1 + bl:.4f}\\,0\\,maxval)")
            rgb_f.append(f"lutrgb=r='{e}':g='{e}':b='{e}'")
        yuv_f: list[str] = []
        if s > 0: yuv_f.append(f"gblur=sigma={max(0.3, s * scale_px):.3f}")
        elif s < 0: yuv_f.append(f"unsharp=lx=5:ly=5:la={min(2.0, -s * 0.5):.3f}:cx=5:cy=5:ca=0")
        if gr > 0: yuv_f.append(f"noise=alls={gr * 14:.2f}:allf=t+u")
        chain = ["format=gbrp", *rgb_f]
        if yuv_f: chain += [cs.rgb_to_yuv(), *yuv_f, cs.yuv_to_rgb("gbrp")]
        colour = g.add([col], ",".join(chain))
        return g.add([g.add([colour], "format=rgba"), a], "alphamerge,format=rgba")

    PATTERN_MEAN = {"ripples": 0.266, "solid": 0.253, "particles": 0.02, "asset": 0.27}      # average brightness of each generated pattern: the light must redistribute, not darken

    def light_pattern_stream(L: dict) -> tuple[str, float]:
        """a gray W x H stream (bright = lit) and its average brightness. Generated at a quarter of the size (smooth, and 16x cheaper), or the user's own picture / loop."""
        aid = L.get("light_pattern_asset_id")
        spread = float(L.get("light_pattern_spread", 0.15))
        tail = f",gblur=sigma={max(0.3, spread * 14 * scale_px):.3f}" if spread > 0.02 else ""
        if aid:
            return g.add([f"{asset_idx(aid)}:v"], f"fps={fps:g}" + (f",setpts=N+{seek_f}" if seek_f else ",setpts=N") + f",scale={W}:{H}:flags=bicubic,format=gray{tail}"), PATTERN_MEAN["asset"]
        w4, h4 = max(2, W // 4), max(2, H // 4)
        style = L.get("light_pattern_style", "ripples")
        sp, scl, warp = float(L["light_pattern_speed"]), max(0.3, float(L["light_pattern_scale"])), float(L.get("light_pattern_warp", 1.0))
        if style == "particles":
            cell = 6.0 / scl                                                         # quarter-size pixels per cell: bigger scale = bigger, fewer glints
            yy = f"(Y+T*{sp * 14:.4f})"                                              # they drift upwards
            cx, cy = f"floor(X/{cell:.4f})", f"floor({yy}/{cell:.4f})"
            h = f"mod(abs(sin({cx}*12.9898+{cy}*78.233)*43758.5453),1)"
            fx, fy = f"(X/{cell:.4f}-{cx}-0.5)", f"({yy}/{cell:.4f}-{cy}-0.5)"
            expr = f"if(gt({h},0.8),255*pow(max(0,1-hypot({fx},{fy})*2.0),2)*(0.55+{h}*0.45),0)"
        else:
            f = 2 * 3.14159265 * 5.0 / (w4 * scl)                                    # ~5 cells across at scale 1
            u, v = f"(X*{f:.6f})", f"(Y*{f:.6f})"
            w1 = f"abs(sin({u}*0.9+{v}*0.5+{1.8 * warp:.3f}*sin({v}*0.7+T*{sp * 1.3:.4f})))"
            w2 = f"abs(sin({u}*0.5-{v}*0.9+{1.8 * warp:.3f}*sin({u}*0.6+T*{sp * 1.1:.4f})))"
            w3 = f"abs(sin({u}*1.1-{v}*0.2+{1.4 * warp:.3f}*sin({v}*0.9-T*{sp * 0.9:.4f})))"
            expr = (f"if(gt({w1}+{w2}+{w3},2.3),255,0)" if style == "solid"
                    else f"min(255,255*1.4*(pow(1-{w1},8)+pow(1-{w2},8)+pow(1-{w3},8)))")
        expr = expr.replace(",", "\\,")
        return g.add([], f"nullsrc=s={w4}x{h4}:r={fps:g},format=gray" + (f",setpts=N+{seek_f}" if seek_f else ",setpts=N") + f",geq=lum='{expr}',scale={W}:{H}:flags=bicubic,format=gray{tail}"), PATTERN_MEAN[style]

    def _rgb01(hexc: str | None) -> tuple[float, float, float] | None:
        if not hexc: return None
        h = hexc.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def light_blend(colour: str, L: dict) -> str:
        """colour: a gbrp stream. Lit where the pattern is bright, a little darker between, weaker towards the bottom (the light comes from above), tinted by the light colour(s)"""
        k = float(L.get("light_pattern", 0.0))
        pat, mean = light_pattern_stream(L)
        pat = g.add([pat], "format=gbrp")
        fall = float(L.get("light_pattern_falloff", 0.6))
        F = f"(1-{fall:.4f}*Y/H)"
        M = f"(1+{0.34 * min(1.5, k):.4f}*{F}*(2.2*B/255-{2.2 * mean:.4f}))"
        c1, c2 = _rgb01(L.get("light_pattern_color")), _rgb01(L.get("light_pattern_color2"))
        t1 = max(1 - x for x in c1) if c1 else 0.0
        t2 = max(1 - x for x in c2) if c2 else 0.0
        exprs = []
        for plane in (1, 2, 0):                                                        # gbrp planes are G, B, R: take the colour's g, b, r
            x = f"(A*{M})"
            if c2: x = f"({x}*(1-S2)+{c2[plane] * 255:.1f}*S2)".replace("S2", f"({0.3 * min(1.5, k) * t2:.4f}*{F}*(1-B/255))")
            if c1 and t1 > 0.01: x = f"({x}*(1-S1)+{c1[plane] * 255:.1f}*S1)".replace("S1", f"({0.85 * min(1.0, k) * t1:.4f}*B/255*{F})")
            exprs.append(f"clip({x},0,255)".replace(",", "\\,"))
        return g.add([colour, pat], f"blend=c0_expr='{exprs[0]}':c1_expr='{exprs[1]}':c2_expr='{exprs[2]}':shortest=1")

    def light_on_subject(fg: str, L: dict) -> str:
        """project the light onto the SUBJECT'S colour (its matte is untouched, so nothing can ever land outside the subject)"""
        if float(L.get("light_pattern", 0.0)) <= 0 or L.get("light_pattern_on", "subject") == "background": return fg
        col, alpha = splitn(fg, 2)
        a = g.add([alpha], "alphaextract")
        lit = light_blend(g.add([col], "format=gbrp"), L)
        return g.add([g.add([lit], "format=rgba"), a], "alphamerge,format=rgba")

    def colour_lights(fg: str, L: dict, box: dict | None = None) -> str:
        """stage-style COLOUR LIGHTS on the SUBJECT only: up to two coloured lights, each from a side, that fade across the subject, plus a glow that hugs the edge facing each light.
        Done on the subject layer before it is placed over the background and its matte is untouched, so no light can land outside the subject."""
        k = float(L.get("colour_light", 0.0))
        if k <= 0: return fg
        lights = [(L.get("colour_light_side_a", "left"), L.get("colour_light_color_a")), (L.get("colour_light_side_b", "right"), L.get("colour_light_color_b"))]
        lights = [(sd_, _rgb01(c)) for sd_, c in lights if sd_ and sd_ != "off" and c and _rgb01(c)]
        if not lights: return fg
        col, alpha = splitn(fg, 2)
        a1, a2, a3, a4 = splitn(g.add([alpha], "alphaextract"), 4)
        gate0 = g.add([a4], "lut=y='clip((val-40)*2.4\\,0\\,255)'")                  # near-transparent edge specks of the key get no light at all
        reach = 0.3 + 0.6 * float(L.get("colour_light_reach", 0.5))
        edge = float(L.get("colour_light_edge", 0.6))
        blurred = g.add([a2], f"gblur=sigma={max(1.0, (0.006 + 0.02 * float(L.get('colour_light_reach', 0.5))) * W):.2f}")
        ring = g.add([a1, blurred], "blend=all_expr='clip((A-B)*3\\,0\\,255)':shortest=1")        # inner band along the outline
        w4, h4 = max(2, W // 4), max(2, H // 4)
        place = L.get("colour_light_place", "fixed")
        inside = ""
        def num_(v): return f"{float(v):.4f}"
        if place == "zone":                                                            # the user's rectangle; the light is confined to it
            zx0, zy0 = float(L.get("colour_light_zone_x0", 0.25)), float(L.get("colour_light_zone_y0", 0.1))
            zx1, zy1 = max(zx0 + 0.02, float(L.get("colour_light_zone_x1", 0.75))), max(zy0 + 0.02, float(L.get("colour_light_zone_y1", 0.9)))
            bx = {"x0": num_(zx0), "x1": num_(zx1), "y0": num_(zy0), "y1": num_(zy1)}
            fx, fy = max(0.002, float(L.get("colour_light_zone_soft", 0.3)) * 0.5 * (zx1 - zx0)), max(0.002, float(L.get("colour_light_zone_soft", 0.3)) * 0.5 * (zy1 - zy0))
            inside = f"*clip((X/W-{zx0:.4f})/{fx:.4f},0,1)*clip(({zx1:.4f}-X/W)/{fx:.4f},0,1)*clip((Y/H-{zy0:.4f})/{fy:.4f},0,1)*clip(({zy1:.4f}-Y/H)/{fy:.4f},0,1)"
        elif box and box.get("track"):                                                 # follow: piecewise-linear in time through the measured moments
            tr = box["track"]
            def pw(i: int) -> str:
                e = num_(tr[-1][i])
                for j in range(len(tr) - 2, -1, -1):
                    t0, t1 = tr[j][0], tr[j + 1][0]
                    e = f"if(lt(T,{t1:.4f}),{tr[j][i]:.4f}+({tr[j + 1][i] - tr[j][i]:.4f})*clip((T-{t0:.4f})/{max(1e-3, t1 - t0):.4f},0,1),{e})"
                return f"({e})"
            bx = {"x0": pw(1), "x1": pw(2), "y0": pw(3), "y1": pw(4)}
        else:
            b = box or {"x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0}                    # the subject's bounds (measured on the middle frame), else the whole frame
            bx = {k_: num_(v) for k_, v in b.items() if k_ in ("x0", "x1", "y0", "y1")}
        layers = []
        for side, c in lights:
            bw, bh = f"max(0.05,{bx['x1']}-{bx['x0']})", f"max(0.05,{bx['y1']}-{bx['y0']})"
            d = {"left": f"((X/W-{bx['x0']})/{bw})", "right": f"(({bx['x1']}-X/W)/{bw})", "top": f"((Y/H-{bx['y0']})/{bh})", "bottom": f"(({bx['y1']}-Y/H)/{bh})"}[side]
            expr = f"255*pow(clip(1-max(0,{d})/{reach:.4f},0,1),1.6){inside}".replace(",", "\\,")
            w = g.add([], f"nullsrc=s={w4}x{h4}:r={fps:g},format=gray" + (f",setpts=N+{seek_f}" if seek_f else ",setpts=N") + f",geq=lum='{expr}',scale={W}:{H}:flags=bicubic,format=gray")
            w_a, w_b = splitn(w, 2)
            layers.append((w_a, w_b, c))
        # a ring copy per light
        rings = splitn(ring, len(layers))
        gates = splitn(gate0, len(layers)) if len(layers) > 1 else [gate0]
        mixed = []
        for (w_a, w_b, c), rg, gt in zip(layers, rings, gates):
            e = g.add([rg, w_b], "blend=all_expr='A*B/255':shortest=1")
            tot = g.add([w_a, e], f"blend=all_expr='clip(A*{0.8 * min(1.5, k):.4f}+B*{1.6 * min(1.5, k) * edge:.4f}\\,0\\,255)':shortest=1")
            tot = g.add([tot, gt], "blend=all_expr='A*B/255':shortest=1")
            t1, t2, t3 = splitn(tot, 3)
            chans = {pl: g.add([t], f"lut=y='clip(val*{c[pl_i]:.4f},0,255)'") for pl, t, pl_i in ((0, t1, 1), (1, t2, 2), (2, t3, 0))}   # planes G, B, R
            mixed.append(g.add([chans[0], chans[1], chans[2]], "mergeplanes=0x001020:gbrp"))
        light = mixed[0] if len(mixed) == 1 else g.add([mixed[0], mixed[1]], "blend=all_mode=addition:shortest=1")
        lit = g.add([g.add([col], "format=gbrp"), light], "blend=all_mode=screen:shortest=1")
        return g.add([g.add([lit], "format=rgba"), a3], "alphamerge,format=rgba")

    def light_on_background(bgs: str, L: dict) -> str:
        if float(L.get("light_pattern", 0.0)) <= 0 or L.get("light_pattern_on", "subject") == "subject": return bgs
        return g.add([light_blend(g.add([bgs], "format=gbrp"), L)], "format=rgba")

    def fringe_block(src: str, px: float) -> str:
        """lens colour fringing: green slightly magnified, red more, blue not at all, so they separate towards the frame edge by `px` pixels"""
        k = px * scale_px / (W / 2.0)
        rgb = g.add([src], cs.yuv_to_rgb("gbrp"))
        ra, rb, rc = splitn(rgb, 3)
        up = lambda f: f"scale=w='trunc(iw*{f:.6f}/2)*2':h='trunc(ih*{f:.6f}/2)*2':flags=bicubic,crop={W}:{H},setsar=1"
        gch = g.add([ra], f"extractplanes=g,{up(1 + k)}")
        bch = g.add([rb], "extractplanes=b,setsar=1")
        rch = g.add([rc], f"extractplanes=r,{up(1 + 2 * k)}")
        return g.add([g.add([gch, bch, rch], "mergeplanes=0x001020:gbrp")], cs.rgb_to_yuv())

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

    def floor_for(r: Operation | None) -> dict | None:
        """the floor line of a background replacement (final-frame fraction) unless the subject is cut by the frame bottom"""
        if r is None: return None
        p = r.params
        if p.get("reflection", 0) <= 0 and p.get("contact_shadow", 0) <= 0: return None
        if p.get("floor_y") is not None: return {"y": float(p["floor_y"]), "cut": False}
        f = plan.floor.get(r.id)
        return f if f and not f["cut"] else None

    def mul_mask(a: str, path: str) -> str:
        """a gray stream x a still gray mask (both 0..255): a * mask / 255"""
        work_files.append(path)
        inputs.append(["-loop", "1", "-framerate", f"{fps:g}", "-i", path])
        m = g.add([f"{len(inputs)}:v"], f"fps={fps:g}" + (f",setpts=N+{seek_f}" if seek_f else ",setpts=N") + f",scale={W}:{H},format=gray")
        return g.add([a, m], "blend=all_expr='A*B/255':shortest=1")

    def shift_down(src: str, d: int) -> str:
        """move a full-frame stream down (d > 0) or up (d < 0) by d pixels; the vacated edge is transparent / black"""
        if d == 0: return src
        if d > 0: return g.add([src], f"pad=iw:ih+{d}:0:{d}:black@0,crop=iw:{H}:0:0")
        return g.add([src], f"pad=iw:ih+{-d}:0:0:black@0,crop=iw:{H}:0:{-d}")

    def reflection_layer(tap: str, p: dict, F: float) -> str:
        """the subject flipped about the floor line, faded with distance and blurred, as an rgba layer for under the subject"""
        fp = int(round(F * H))
        flipped = shift_down(g.add([tap], "vflip"), 2 * fp - (H - 1))
        col, a = splitn(flipped, 2)
        a = mul_mask(g.add([a], "alphaextract"), write_gray(reflection_gradient(H, W, fp, p["reflection_length"] * H, p["reflection"]), wd, "refl"))
        bl = p["reflection_blur"] * scale_px
        if bl > 0.3: a = g.add([a], f"gblur=sigma={bl:.3f}")
        col = g.add([col], "format=gbrp" + (f",gblur=sigma={bl:.3f}" if bl > 0.3 else "") + ",format=rgba")
        return g.add([col, a], "alphamerge,format=rgba")

    def contact_layer(tap: str, p: dict, F: float) -> str:
        """a tight dark blob at the floor line: the lowest slice of the subject, squashed and blurred, so it follows the feet"""
        fp = int(round(F * H))
        band = max(2, int(round(p["contact_height"] * H)))
        d = band // 2                                                   # shift the subject (rgba: the vacated rows are transparent, a gray plane's "black" would be 16) so the slice is centred on the floor line
        a = mul_mask(g.add([shift_down(tap, d)], "alphaextract"), write_gray(floor_band(H, W, fp + d, band), wd, "contact"))
        cb = max(0.5, p["contact_blur"] * scale_px)
        a = g.add([a], f"gblur=sigma={cb:.3f}:sigmaV={max(0.4, cb * 0.35):.3f}")
        a = g.add([a], f"lut=y='clip(val*{min(1.0, p['contact_shadow'] * 1.1):.4f}\\,0\\,255)'")
        black = g.add([], f"color=c=black:s={W}x{H}:r={fps:g},format=rgba{shift}")
        return g.add([black, a], "alphamerge,format=rgba")

    def qc_view(src: str, k: Operation) -> str:
        v = opts.view
        fg = make_keyed(src, k)
        if v in ("alpha", "edge"):
            a = g.add([fg], "alphaextract")
            if v == "edge": a = g.add([a], "lut=y='if(between(val\\,6\\,248)\\,255\\,0)'")        # only the partly transparent band
            white = g.add([], f"color=c=white:s={W}x{H}:r={fps:g},format=rgba{shift}")
            black = g.add([], f"color=c=black:s={W}x{H}:r={fps:g},format=rgba{shift}")
            fg = g.add([white, a], "alphamerge,format=rgba")
            bg = black
        elif v == "checker":
            bg = g.add([], f"nullsrc=s={W}x{H}:r={fps:g},geq=lum='if(lt(mod(floor(X/16)+floor(Y/16)\\,2)\\,1)\\,92\\,138)':cb=128:cr=128,format=rgba{shift}")
        else:
            col = {"red": "0xe0102a", "white": "0xffffff", "grey": "0x808080"}.get(v, "0x808080")
            bg = g.add([], f"color=c={col}:s={W}x{H}:r={fps:g},format=rgba{shift}")
        return g.add([bg, fg], f"overlay=shortest=1:format=auto,{cs.rgb_to_yuv()}")

    def composite(cur: str, k: Operation, r: Operation | None) -> str:
        rng = r or k
        L = plan.look_for.get(r.id) if r and not plan.measuring else None
        look = resolve_look(L.params, plan.look_auto.get(L.id)) if L else None
        if look is not None:                                       # the look's master Amount also scales how strongly the subject is integrated into the scene
            la = max(0.0, float(L.params.get("amount", 1.0)))
            if la != 1.0:
                look = {**look, **{key: look.get(key, 0.0) * la for key in ("ambient_match", "light_wrap", "depth", "subject_exposure", "shadow", "match_softness", "subject_grain", "background_grain", "match_blacks", "light_pattern", "colour_light")}}
        rp = r.params if r else {}
        moved = bool(r) and (rp["subject_scale"] != 1 or rp["subject_x"] != 0 or rp["subject_y"] != 0)
        sp = rp if (r and rp["shadow"] > 0) else (look if look and look.get("shadow", 0) > 0 else None)      # a look's measured shadow applies only if the background has none of its own
        shadow = bool(r) and sp is not None
        if opts.view:                                          # key QC view: the matte (or the subject on a solid colour) instead of the finished picture
            return ranged_block(cur, rng, lambda src: qc_view(src, k))
        def build(src: str) -> str:
            bgs, fg = bg_stream(r, k), make_keyed(src, k)
            if moved: fg = place_subject(fg, rp)
            for gop in (plan.grade_for.get(r.id, []) if r else []):     # Color Gradings set to 'subject only' / 'background only'
                if gop.params.get("apply_to") == "subject": fg = grade_part(fg, gop)
                else: bgs = grade_part(bgs, gop)
            sh = rfl = ctl = None
            fl = floor_for(r)
            want = [n for n, on in (("shadow", shadow), ("reflect", bool(fl) and rp["reflection"] > 0), ("contact", bool(fl) and rp["contact_shadow"] > 0)) if on]
            if want:
                outs = splitn(fg, 1 + len(want)); fg = outs[0]; taps = dict(zip(want, outs[1:]))
                if "shadow" in taps: sh = cast_shadow(taps["shadow"], sp)
                if "reflect" in taps: rfl = reflection_layer(taps["reflect"], rp, fl["y"])
                if "contact" in taps: ctl = contact_layer(taps["contact"], rp, fl["y"])
            if look:
                fg = subject_camera(fg, look)
                fg = light_on_subject(fg, look)
                fg = colour_lights(fg, look, plan.light_box.get(r.id) if r else None)
                bgs, fg = integrate(bgs, fg, look)
                bgs = light_on_background(bgs, look)
                if look.get("background_grain", 0) > 0:
                    bgs = g.add([bgs], f"{cs.rgb_to_yuv()},noise=alls={look['background_grain'] * 14:.2f}:allf=t+u,{cs.yuv_to_rgb('rgba')}")
            for layer in (rfl, sh, ctl):                                 # reflection under the cast shadow under the contact shadow, all under the subject
                if layer: bgs = g.add([bgs, layer], "overlay=shortest=1:format=auto,format=rgba")
            return g.add([bgs, fg], f"overlay=shortest=1:format=auto,{cs.rgb_to_yuv()}")
        amt = min(1.0, float(k.params.get("amount", 1.0)))
        if amt >= 0.9995: return ranged_block(cur, rng, build)
        if amt <= 0.0005: return cur                                 # master Amount 0 %: the removal is mixed out completely, the original footage stays
        def mixed(src: str) -> str:
            keep, work = g.split(src)
            return mix(build(work), keep, amt)
        return ranged_block(cur, rng, mixed)

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
        d = p["dehaze"]
        if d:   # + : move the black point up the curve and stretch (removes the veil), - : lift blacks (adds haze); colour follows
            b = 0.14 * abs(d)
            f.append(f"curves=master='{curve_str([(b, 0), (1, 1)] if d > 0 else [(0, b), (1, 1)])}'")
            f.append(f"eq=saturation={1 + 0.25 * d:.4f}:contrast={1 + 0.12 * d:.4f}")
        if p["texture"]:   # luma-only unsharp at a finer radius than studio_look's clarity (13); negative = mid-frequency smoothing
            f.append(f"unsharp=lx=7:ly=7:la={1.5 * p['texture']:.3f}:cx=7:cy=7:ca=0")
        if p["clarity"]:   # luma-only wide-radius unsharp (same radius studio_look uses for its clarity)
            f.append(f"unsharp=lx=13:ly=13:la={0.9 * p['clarity']:.3f}:cx=13:cy=13:ca=0")
        if p["lift"] or p["gamma_rgb"] or p["gain"]:
            f.append(lgg_filter(p["lift"] or [0, 0, 0], p["gamma_rgb"] or [0, 0, 0], p["gain"] or [0, 0, 0]))
        cv = [f"{n}='{curve_str(p[k])}'" for n, k in (("master", "curve_master"), ("red", "curve_red"),
              ("green", "curve_green"), ("blue", "curve_blue")) if p[k]]
        if cv: f.append("curves=" + ":".join(cv))
        if float(p.get("cinematic", 0) or 0) > 0:                       # the film look, on top of everything above
            from .cinema import filters as cine_filters
            f += cine_filters(float(p["cinematic"]), p.get("cinematic_style") or "teal_orange", "huesaturation" in ffmpeg_filters())
        return f

    def grade_block(src: str, op: Operation) -> str:
        """one Color Grading applied to a stream in the main picture format (the whole frame, or only the subject / the background inside a composite)"""
        p = op.params
        if float(p.get("auto_enhance", 0) or 0) > 0 and plan.enhance.get(op.id):
            import copy as _copy
            from .enhance import apply as enhance_apply
            op_e = _copy.copy(op); op_e.params = enhance_apply(p, plan.enhance[op.id])        # the measured corrections x the master slider + your own sliders
            main = grade_filters(op_e)
        else:
            main = grade_filters(op)
        lut_id = p["lut_asset_id"]
        if lut_id and p["lut_intensity"] >= 1:
            main.append(f"lut3d=file='{esc_path(bp.assets[lut_id].resolved)}'")
            lut_id = None
        pre = pre_cmd[0]
        lut_k = p["lut_intensity"]
        lut_path = esc_path(bp.assets[lut_id].resolved) if lut_id else None
        def lut_partial(src: str) -> str:     # partial-strength LUT: blend graded vs LUT'd
            a, b = g.split(src)
            return g.add([g.add([b], ",".join(cs.wrap_rgb([f"lut3d=file='{lut_path}'"]))), a], f"blend=all_mode=normal:all_opacity={lut_k:.3f}")  # first input is the "top" layer
        if abs(float(p.get("amount", 1.0)) - 1.0) > 1e-6:
            def whole(x: str) -> str:
                s_ = g.add([x], ",".join([*pre, *cs.wrap_rgb(main)])) if main else x
                return lut_partial(s_) if lut_id else s_
            return grade_finish(amount_block(src, op, whole), op)
        out = apply_ranged(src, main, op, pre)
        out = ranged_block(out, op, lut_partial) if lut_id else out
        return grade_finish(out, op)

    def grade_finish(src: str, op: Operation) -> str:
        """the cinematic look's vignette + grain and the letterbox bars: after the colour work, over the grade's range"""
        from .cinema import finish as cine_finish, letterbox as cine_bars
        p = op.params
        fin = cine_finish(float(p.get("cinematic", 0) or 0), p.get("cinematic_style") or "teal_orange", scale_px)
        bars = cine_bars(p.get("letterbox"), W, H)
        chain = fin + ([bars] if bars else [])
        if not chain: return src
        return ranged_block(src, op, lambda x: g.add([x], ",".join(chain)))

    def grade_part(x: str, op: Operation) -> str:
        """a Color Grading on an RGBA layer (the subject or the background): only the colour is graded, the alpha (the cut-out) is untouched"""
        col, alpha = splitn(x, 2)
        a = g.add([alpha], "alphaextract")
        graded = grade_block(g.add([col], cs.rgb_to_yuv()), op)
        return g.add([g.add([graded], cs.yuv_to_rgb("rgba")), a], "alphamerge,format=rgba")

    def gray3(x: str) -> str:
        """a gray stream as gbrp with the SAME values in all three planes (no range conversion)"""
        a, b, c = splitn(x, 3)
        return g.add([a, b, c], "mergeplanes=0x001020:gbrp")

    def portrait_block(src: str, op: Operation, ref) -> str:
        """sharp person, blurred real background. The background is blurred by normalised convolution: blur(picture x background-mask) /
        blur(background-mask), so the person's colours never smear into the blur (no halo around the head)."""
        p = op.params
        m = g.add([cached_stream(ref.path, ref, "start_mode=add:color=black:stop_mode=clone")], f"scale={W}:{H},format=gray")
        ew = max(0.5, (0.5 + 4.0 * float(p["edge"])) * scale_px)
        m_soft = g.add([m], f"gblur=sigma={ew:.3f}")                      # the sharp / blurred transition
        m1, m2, m3 = splitn(m_soft, 3)
        inv = g.add([m1], "negate")                                       # where the background is
        rgb = g.add([src], cs.yuv_to_rgb("gbrp"))
        r_a, r_b = splitn(rgb, 2)
        sig = (1.5 + 26.0 * float(p["blur"])) * scale_px

        def normalised(pic: str, w: str, sigma: str) -> str:
            w1, wa = splitn(w, 2)
            pw = g.add([pic, w1], "lut2=c0='x*y/255':c1='x*y/255':c2='x*y/255'")
            num = g.add([pw], f"gblur=sigma={sigma}")
            den = g.add([wa], f"gblur=sigma={sigma}")
            return g.add([num, den], "lut2=c0='if(gt(y\\,0)\\,min(255\\,x*255/y)\\,0)':c1='if(gt(y\\,0)\\,min(255\\,x*255/y)\\,0)':c2='if(gt(y\\,0)\\,min(255\\,x*255/y)\\,0)'")

        fo = float(p["falloff"])
        inv3 = gray3(inv)
        if fo > 0.01:
            i_a, i_b = splitn(inv3, 2)
            pa, pb = splitn(r_a, 2)
            far = normalised(pa, i_a, f"{sig:.3f}")
            near = normalised(pb, i_b, f"{sig * 0.35:.3f}")
            prox = gray3(g.add([m2], f"gblur=sigma={max(4.0, 0.06 * W):.2f},lut=y='clip(val*{2.5 * fo:.3f}\\,0\\,255)'"))   # close to the person
            bg = g.add([far, near, prox], "maskedmerge")
        else:
            g.parts.append(f"[{m2}]nullsink")
            bg = normalised(r_a, inv3, f"{sig:.3f}")
        person = r_b
        if ref.fgr_path:                                                  # see-through edges (hair strands): the estimated person colour, not the old sharp background
            fg = g.add([cached_stream(ref.fgr_path, ref, "start_mode=add:stop_mode=clone")], f"scale={W}:{H},format=gbrp")
            m3, m4 = splitn(m3, 2)
            w = gray3(g.add([m4], "lut=y='clip((255-val)*4\\,0\\,255)'"))   # 0 inside the person, 1 where it is mostly see-through
            person = g.add([r_b, fg, w], "maskedmerge")
        out = g.add([bg, person, gray3(m3)], "maskedmerge")               # the person (mask) over the blurred background
        return g.add([out], cs.rgb_to_yuv())

    in_composite = {o.id for ops in plan.grade_for.values() for o in ops}
    audio_filters: list[str] = []
    for op in plan.steps:
        t, p = op.type, op.params
        if opts.view and t not in ("chroma_key", "background_removal", "background_replacement"): continue     # key QC view: nothing but the key
        if t in ("chroma_key", "background_removal"):
            if op.id in plan.consumed_keyers: continue
            if alpha_out and op.id == plan.transparent_key:
                keyed = make_keyed(cur, op)                                   # rgba: subject colour + matte
                amt = min(1.0, float(p.get("amount", 1.0)))
                if amt < 0.9995:                                              # master Amount of a transparent export: 0 % = nothing removed (fully opaque)
                    keyed = g.add([keyed], f"lutrgb=a='clip(val*{amt:.5f}+maxval*{1 - amt:.5f}\\,0\\,maxval)',format=rgba")
                kc, ka = splitn(keyed, 2)
                cur = g.add([kc], cs.rgb_to_yuv())                            # carry on with the subject's colour (grading etc. still apply)
                alpha_stream.append(g.add([ka], "alphaextract"))
            else:
                cur = composite(cur, op, None)
        elif t == "matte_mask":
            continue                                                      # applied inside the keyer's matte (make_keyed), not a stage of its own
        elif t == "background_replacement":
            cur = composite(cur, next(o for o in plan.steps if o.id == plan.alpha_source[op.id]), op)
        elif t == "portrait_blur":
            ref = plan.mattes.get(op.id)
            if ref is None: raise RuntimeError(f"person detection for '{op.id}' was not generated")
            cur = ranged_block(cur, op, lambda src, op=op, ref=ref: portrait_block(src, op, ref))
        elif t == "color_grading":
            if op.id in in_composite: continue                  # applied to the subject / the background inside the composite
            cur = grade_block(cur, op)
        elif t == "studio_look":
            if plan.measuring: continue
            L = resolve_look(p, plan.look_auto.get(op.id))
            cur = amount_block(cur, op, lambda s_: look_block(s_, L, plan.look_curves.get(op.id)))
        elif t in ("face_tone_correction", "face_relighting"):
            ref = plan.face_controls.get(op.id)
            if ref is None:
                raise RuntimeError(f"face analysis for '{op.id}' was not generated")
            ctrl = g.add([cached_stream(ref.path, ref, "start_mode=add:stop_mode=add:color=black")], f"scale={W}:{H}:flags=bilinear,format=gbrp")
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
            u = g.add([cached_stream(ref.path, ref, "start_mode=add:stop_mode=add:color=black")], f"scale={W}:{H}:flags=lanczos:out_color_matrix={cs.matrix}:out_range={cs.range},setsar=1,format=yuv444p")
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
            import uuid as _uuid                            # a unique name: two renders at once (live frame + preview video) must not delete each other's file
            tf = os.path.join(wd, f"{op.id}_{_uuid.uuid4().hex[:8]}.txt")           # textfile + expansion=none: no filter-injection via text
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

    if alpha_stream:
        cur = g.add([cur, alpha_stream[0]], "alphamerge")
        g.add([cur], cs.finish("yuva444p10le" if out.codec == "prores4444" else "yuva420p"), "vout")
    else:
        # a render that starts later in the timeline (a parallel segment) keeps the timeline time for every effect above (animation,
        # light patterns ...), but the FILE must start at 0 - otherwise the output fills the gap by repeating the first frame
        restart = "setpts=PTS-STARTPTS," if (seek_f > 0 and not opts.frame_only) else ""
        g.add([cur], restart + cs.finish("yuv422p10le" if (out.codec == "prores" and not opts.scale_width) else "yuv420p"), "vout")
    audio_from = len(g.parts)                       # the audio part of the graph starts here (used by audio_only renders)
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
    hw = opts.hwdecode if opts.hwdecode is not None else (not opts.frame_only and hw_decode_usable())
    if hw and not opts.frame_only: args += ["-hwaccel", "auto"]                     # decoded frames come back to memory: the filters are unchanged
    args += ["-i", source]
    for grp in inputs: args += grp
    if opts.audio_only:
        # the same inputs and the same audio processing as the full render, without any picture work
        if not (vid.has_audio and opts.audio): raise RuntimeError("audio_only render of a video without sound")
        tl_dur = plan.total_frames / fps
        a_dur = max(0.0, min(opts.t_end if opts.t_end is not None else tl_dur, tl_dur) - (opts.t_start or 0.0))
        if pieces is None:
            args += ["-map", "0:a:0"] + (["-af", ",".join(audio_filters)] if audio_filters and not opts.t_start else [])
        else:
            args += ["-filter_complex", ";".join(g.parts[audio_from:]), "-map", "[aout]"]
        # uncompressed: the final join encodes it once, exactly as a one-piece render does (an AAC file copied in would lose its start-up delay: +23 ms)
        args += ["-vn", "-c:a", "pcm_s16le", "-t", f"{a_dur:.4f}", out_path]
        return Compiled(args, graph, work_files, a_dur)
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
        if prores or (alpha_out and out.codec == "prores4444"): args += ["-c:a", "pcm_s16le"]
        elif alpha_out and out.codec == "vp9_alpha": args += ["-c:a", "libopus", "-b:a", "128k"]
        else: args += ["-c:a", "aac", "-b:a", out.audio_bitrate]
    else:
        args += ["-an"]
    args += ["-t", f"{out_dur:.4f}"]
    if alpha_out and out.codec == "prores4444":
        # ProRes 4444 with a 16-bit alpha channel: opens with transparency in Premiere, DaVinci Resolve, Final Cut, After Effects
        args += ["-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-r", f"{fps:g}", out_path]
        return Compiled(args, graph, work_files, out_dur)
    if alpha_out and out.codec == "vp9_alpha":
        # WebM / VP9 with alpha: plays with transparency in Chrome, Edge and Firefox (and OBS, CapCut, Resolve)
        args += ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(opts.crf if opts.crf is not None else max(18, min(40, out.crf + 4))),
                 "-row-mt", "1", "-auto-alt-ref", "0", "-deadline", "good", "-cpu-used", "2", "-r", f"{fps:g}", out_path]
        return Compiled(args, graph, work_files, out_dur)
    if prores:
        # ProRes 422 10-bit intermediate for finishing in other software (processing itself is 8-bit)
        prof = {"proxy": 0, "lt": 1, "standard": 2, "hq": 3}[out.prores_profile]
        args += ["-c:v", "prores_ks", "-profile:v", str(prof), "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-r", f"{fps:g}", out_path]
        return Compiled(args, graph, work_files, out_dur)
    enc = pick_encoder(out.codec if out.codec in ("h264", "h265") else "h264", opts.encoder)
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
        names = ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox") if codec == "h264" else ("hevc_nvenc", "hevc_amf", "hevc_qsv", "hevc_videotoolbox")
        return next((n for n in names if n in encoders() and _try_encoder(n)), soft)
    return choice if choice in encoders() else soft
