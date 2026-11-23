"""Compiles an execution Plan into an FFmpeg argument list (never a shell string)."""
from __future__ import annotations
import os, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .blueprint import Operation
from .planner import Plan, _dominant
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
    inputs: list[list[str]] = []
    work_files: list[str] = []
    asset_input: dict[str, int] = {}
    wd = work_dir or tempfile.mkdtemp(prefix="evp_")
    seek = opts.t_start or 0.0
    seek_f = round(seek * fps)          # all streams use frame-index timestamps (time base 1/fps) so they align exactly

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
        return g.add([keep, proc], f"overlay=enable='{en}':shortest=1,format=yuv420p")

    def apply_ranged(cur: str, filters: list[str], op: Operation) -> str:
        if not filters: return cur
        en = enable(op)
        if en is None: return g.add([cur], ",".join(filters))
        if all(supports_timeline(f.split("=", 1)[0]) for f in filters):
            return g.add([cur], ",".join(f"{f}:enable='{en}'" for f in filters))
        return ranged_block(cur, op, lambda s: g.add([s], ",".join(filters)))

    # ---- base: source -> constant fps, output size
    cur = g.add(["0:v"], f"fps={fps:g},setpts=N+{seek_f},"
                f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p", "v0")

    def make_keyed(src: str, k: Operation) -> str:
        p = k.params
        if k.type == "background_removal" and p["mode"] == "ai":
            ref = plan.mattes.get(k.id)
            if ref is None:
                raise RuntimeError(f"AI matte for '{k.id}' was not generated")
            inputs.append(["-i", ref.path])
            # matte file covers [start_frame, start_frame+frames): pad before with opaque, clone after, align to (seeked) timeline
            m = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:start_mode=add:color=white:stop=-1:stop_mode=clone,"
                      f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H},format=gray")
            colour = src
            m, m_edge = g.split(m) if ref.fgr_path else (m, None)
            if ref.fgr_path:
                inputs.append(["-i", ref.fgr_path])
                fg = g.add([f"{len(inputs)}:v"], f"fps={fps:g},setpts=N,tpad=start={ref.start_frame}:start_mode=add:stop=-1:stop_mode=clone,"
                           f"trim=start_frame={seek_f},setpts=N+{seek_f},scale={W}:{H}")
                # take the model's foreground colour only in semi-transparent edges (weight = (1-alpha)*4), keep the
                # (possibly already processed) frame elsewhere
                w = g.add([m_edge], "lut=y='clip((255-val)*4\\,0\\,255)',format=gray")
                a_rgb, b_rgb = g.add([src], "format=gbrp"), g.add([fg], "format=gbrp")
                colour = g.add([a_rgb, b_rgb, g.add([w], "format=gbrp")], "maskedmerge")
            return g.add([g.add([colour], "format=rgba"), m], "alphamerge,format=rgba")
        erode, feather = p.get("edge_erode", 0), p.get("edge_feather", 0.0)
        if k.type == "background_removal":
            q = p["quality"]
            if p["edge_refinement"] and q != "draft":
                erode, feather = (1, 0.8) if q == "standard" else (1, 1.5)
            else:
                erode, feather = 0, 0.0
        chain = [f"chromakey=color={hex0x(p['key_color'])}:similarity={p['similarity']:.4f}:blend={p['blend']:.4f}"]
        dom = _dominant(p["key_color"])
        if p["spill_suppression"] and dom:
            chain.append(f"despill=type={dom}:mix={p['spill_strength']:.3f}:expand=0")
        chain.append("format=rgba")
        keyed = g.add([src], ",".join(chain))
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
        z = p["scale"]
        if p["fit"] in ("cover", "contain"):
            fac = f"{'max' if p['fit'] == 'cover' else 'min'}({W}/iw,{H}/ih)"
            sc = f"scale=w='trunc(iw*{fac}*{z}/2)*2':h='trunc(ih*{fac}*{z}/2)*2'"
        else:
            sc = f"scale={int(W * z) // 2 * 2}:{int(H * z) // 2 * 2}"
        chain = [f"fps={fps:g}", sc, "setsar=1", "format=rgba"]
        if p["blur"] > 0: chain.append(f"gblur=sigma={p['blur']:.3f}")
        if p["brightness"] != 0 or p["saturation"] != 1:
            chain.append(f"eq=brightness={p['brightness']:.4f}:saturation={p['saturation']:.4f}")
        a = g.add([f"{idx}:v"], ",".join(chain))
        canvas = g.add([], f"color=c={hex0x(p['color'])}:s={W}x{H}:r={fps:g},format=rgba")
        return g.add([canvas, a], f"overlay=x='(W-w)/2+{p['offset_x']}*W':y='(H-h)/2+{p['offset_y']}*H':shortest=1,format=rgba{shift}")

    def composite(cur: str, k: Operation, r: Operation | None) -> str:
        rng = r or k
        def build(src: str) -> str:
            return g.add([bg_stream(r, k), make_keyed(src, k)], "overlay=shortest=1:format=auto,format=yuv420p")
        return ranged_block(cur, rng, build)

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
        if p["exposure"] != 0: f.append(f"exposure=exposure={p['exposure']:.4f}")
        b, s, hi, w = p["blacks"], p["shadows"], p["highlights"], p["whites"]
        if any((b, s, hi, w)):
            pts = {0.0: max(0.0, 0.1 * b), 0.25: min(1, max(0, 0.25 + 0.12 * s)), 0.5: 0.5,
                   0.75: min(1, max(0, 0.75 + 0.12 * hi)), 1.0: min(1.0, 1.0 + 0.1 * min(0, w))}
            if b < 0: pts[round(-0.1 * b, 4)] = 0.0
            if w > 0: pts[round(1 - 0.1 * w, 4)] = 1.0
            f.append(f"curves=master='{curve_str(sorted(pts.items()))}'")
        t, ti = p["temperature"] / 100.0, p["tint"] / 100.0
        if t or ti:
            f.append(f"colorchannelmixer=rr={1 + 0.2 * t + 0.05 * ti:.4f}:gg={1 - 0.2 * ti:.4f}:bb={1 - 0.2 * t + 0.05 * ti:.4f}")
        if (p["contrast"], p["brightness"], p["saturation"], p["gamma"]) != (1, 0, 1, 1):
            f.append(f"eq=contrast={p['contrast']:.4f}:brightness={p['brightness']:.4f}:saturation={p['saturation']:.4f}:gamma={p['gamma']:.4f}")
        if p["vibrance"] != 0: f.append(f"vibrance=intensity={p['vibrance']:.4f}")
        if p["lift"] or p["gamma_rgb"] or p["gain"]:
            vals = [*(p["lift"] or [0, 0, 0]), *(p["gamma_rgb"] or [0, 0, 0]), *(p["gain"] or [0, 0, 0])]
            f.append("colorbalance=" + ":".join(f"{k}={v:.4f}" for k, v in
                     zip(("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh"), vals)))
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
            cur = apply_ranged(cur, main, op)
            if lut_id:   # partial-strength LUT: blend graded vs LUT'd
                lut_path, k = esc_path(bp.assets[lut_id].resolved), p["lut_intensity"]
                def build(src: str) -> str:
                    a, b = g.split(src)
                    return g.add([g.add([b], f"lut3d=file='{lut_path}'"), a], f"blend=all_mode=normal:all_opacity={k:.3f}")  # first input is the "top" layer
                cur = ranged_block(cur, op, build)
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
            dt = [f"textfile='{esc_path(tf)}'", "expansion=none", f"fontsize={p['font_size']}",
                  f"fontcolor={hex0x(p['color'])}", f"x=w*{p['x']}-text_w/2", f"y=h*{p['y']}-text_h/2"]
            font = find_font()
            if font: dt.append(f"fontfile='{esc_path(font)}'")
            if p["box"]: dt += ["box=1", f"boxcolor=black@{p['box_opacity']}", "boxborderw=12"]
            cur = apply_ranged(cur, ["drawtext=" + ":".join(dt)], op)
        elif t == "audio_gain":
            en = enable(op)
            if p["gain_db"] != 0:
                audio_filters.append(f"volume={p['gain_db']:.2f}dB" + (f":enable='{en}'" if en else ""))
            if p["fade_in"] > 0: audio_filters.append(f"afade=t=in:st={op.start:.4f}:d={p['fade_in']:.3f}")
            if p["fade_out"] > 0:
                audio_filters.append(f"afade=t=out:st={max(op.start, op.end - p['fade_out']):.4f}:d={p['fade_out']:.3f}")
        else:
            raise RuntimeError(f"compiler has no handler for '{t}' (registry/compiler mismatch)")

    g.add([cur], "format=yuv420p", "vout")
    graph = g.text()
    args = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if not opts.frame_only: args += ["-progress", "pipe:1", "-nostats"]
    if opts.t_start: args += ["-ss", f"{opts.t_start:.4f}"]
    args += ["-i", source]
    for grp in inputs: args += grp
    args += ["-filter_complex", graph, "-map", "[vout]"]
    if opts.frame_only:
        args += ["-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"]
        return Compiled(args, graph, work_files, 0.0)

    out_dur = vid.duration
    if opts.t_start is not None or opts.t_end is not None:
        out_dur = max(0.0, min(opts.t_end if opts.t_end is not None else vid.duration, vid.duration) - (opts.t_start or 0.0))
    if vid.has_audio and opts.audio:
        args += ["-map", "0:a:0"]
        if audio_filters and not opts.t_start: args += ["-af", ",".join(audio_filters)]
        args += ["-c:a", "aac", "-b:a", out.audio_bitrate]
    else:
        args += ["-an"]
    args += ["-t", f"{out_dur:.4f}"]
    enc = pick_encoder(out.codec, opts.encoder)
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
