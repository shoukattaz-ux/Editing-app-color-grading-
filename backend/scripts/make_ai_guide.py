"""Builds docs/EVP-Blueprint-Guide-for-AI.pdf from the operation registry (so it can never drift from the validator).
Every example blueprint in the PDF is run through the real validator first; the script fails if one is rejected.
Usage: python backend/scripts/make_ai_guide.py [output.pdf]"""
import json, subprocess, sys, tempfile
from pathlib import Path
from xml.sax.saxutils import escape
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evp.registry import OPERATIONS
from evp.engine import prepare
from evp.security import PathPolicy
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "docs" / "EVP-Blueprint-Guide-for-AI.pdf"

# ------------------------------------------------------------------ examples (validated below)
EX = {}
EX["Natural colour grade (no background change)"] = ("Use when the user only wants better colour. color_grading values are in the software's own units "
    "(contrast 1.0 = unchanged, saturation 1.0 = unchanged, highlights/shadows/whites/blacks -1..1, temperature/tint -100..100).", {
    "blueprint_version": "1.0",
    "project": {"name": "Natural grade", "notes": "Consistent natural look."},
    "output": {"resolution": "source", "fps": "source", "format": "mp4", "codec": "h264", "crf": 17, "preset": "medium"},
    "assets": [],
    "operations": [
        {"id": "grade", "type": "color_grading", "start": 0,
         "parameters": {"exposure": 0.3, "contrast": 1.15, "highlights": -0.25, "shadows": 0.22, "whites": 0.15, "blacks": -0.18,
                        "temperature": 4, "tint": 2, "saturation": 1.08, "vibrance": 0.15, "texture": 0.12, "clarity": 0.10, "dehaze": 0.05}},
        {"id": "skin", "type": "face_tone_correction", "start": 0,
         "parameters": {"strength": 0.6, "target_brightness": 0.55, "auto_strength": 0.6, "max_auto_ev": 0.6}},
        {"id": "sharp", "type": "video_enhancement", "start": 0, "parameters": {"sharpen": 0.3}}]})
EX["Green screen -> new studio background, matched colour"] = ("Chain: key -> replacement -> studio_look. Each later step names the earlier one in depends_on. "
    "auto_match lets the software measure the background and set light, shadow colour, contrast and edge light itself.", {
    "blueprint_version": "1.0",
    "project": {"name": "Green screen to studio"},
    "output": {"resolution": "source", "fps": "source", "format": "mp4", "codec": "h264", "crf": 18},
    "assets": [{"id": "studio_bg", "type": "image", "path": "assets/studio_bg.png"}],
    "operations": [
        {"id": "key", "type": "background_removal", "start": 0, "parameters": {"mode": "screen", "temporal_smooth": 0.5}},
        {"id": "bg", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "studio_bg", "fit": "cover"}, "depends_on": ["key"]},
        {"id": "look", "type": "studio_look", "start": 0, "parameters": {"preset": "clean_bright", "auto_match": 1.0}, "depends_on": ["bg"]}]})
EX["Normal video (no green screen) -> new background"] = ("mode 'ai' cuts out a person without any screen. Needs the AI model downloaded in the app (the validator warns if missing).", {
    "blueprint_version": "1.0",
    "project": {"name": "AI background swap"},
    "output": {"resolution": "source", "fps": "source", "format": "mp4", "codec": "h264", "crf": 18},
    "assets": [{"id": "studio_bg", "type": "image", "path": "assets/studio_bg.png"}],
    "operations": [
        {"id": "cut", "type": "background_removal", "start": 0, "parameters": {"mode": "ai", "solidity": 0.5}},
        {"id": "bg", "type": "background_replacement", "start": 0, "parameters": {"asset_id": "studio_bg", "fit": "cover"}, "depends_on": ["cut"]},
        {"id": "look", "type": "studio_look", "start": 0, "parameters": {"preset": "studio_warm", "auto_match": 1.0}, "depends_on": ["bg"]}]})
EX["Remove green screen only, export with transparency"] = ("No background_replacement. Codec prores4444 (+ format mov) or vp9_alpha (+ format webm) keeps the alpha channel.", {
    "blueprint_version": "1.0",
    "project": {"name": "Transparent export"},
    "output": {"resolution": "source", "fps": "source", "format": "mov", "codec": "prores4444"},
    "assets": [],
    "operations": [{"id": "key", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}}]})

WRONG = {"blueprint_version": "1.0", "operations": [{"id": "g", "type": "studio_look", "start": 0, "parameters": {
    "white_balance": {"temperature": 4}, "tone": {"contrast": 18}, "look_name": "cinematic"}}]}

def validate_all():
    tmp = Path(tempfile.mkdtemp(prefix="evpguide_")); (tmp / "assets").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3", "-f", "lavfi", "-i", "sine=d=3",
                    "-pix_fmt", "yuv420p", "-shortest", str(tmp / "in.mp4")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x303a4a:s=640x360", "-frames:v", "1", str(tmp / "assets/studio_bg.png")], check=True)
    for name, (_, bp) in EX.items():
        p = prepare(bp, str(tmp / "in.mp4"), PathPolicy([tmp]), tmp)
        errs = [f"{e.code}: {e.message}" for e in p.report.errors]
        if errs or not p.plan: raise SystemExit(f"example '{name}' REJECTED by the validator: {errs}")
        print("validated:", name)
    p = prepare(WRONG, str(tmp / "in.mp4"), PathPolicy([tmp]), tmp)
    assert p.report.errors, "the 'wrong' example must be rejected"

# ------------------------------------------------------------------ PDF
ss = getSampleStyleSheet()
B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9.5, leading=13.5, spaceAfter=4)
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=19, spaceBefore=6, spaceAfter=8, textColor=colors.HexColor("#14325c"))
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#14325c"))
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=10.5, spaceBefore=6, spaceAfter=3)
CODE = ParagraphStyle("CODE", fontName="Courier", fontSize=7.6, leading=9.4, backColor=colors.HexColor("#f2f4f7"), borderPadding=5, spaceBefore=3, spaceAfter=8)
CELL = ParagraphStyle("CELL", parent=B, fontSize=7.6, leading=9.2, spaceAfter=0)
BOX = ParagraphStyle("BOX", parent=B, backColor=colors.HexColor("#fff6dc"), borderColor=colors.HexColor("#e0b84a"), borderWidth=0.8, borderPadding=6, spaceBefore=6, spaceAfter=10)
OKB = ParagraphStyle("OKB", parent=BOX, backColor=colors.HexColor("#e6f6ea"), borderColor=colors.HexColor("#5ab26f"))
P = lambda t, s=B: Paragraph(t, s)
C = lambda t: Paragraph(escape(str(t)), CELL)

def code(obj):
    txt = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    return Preformatted(txt, CODE)

def table(rows, widths, head=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b7c0cc")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if head: st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde6f2"))]
    t.setStyle(TableStyle(st)); return t

def rng(p):
    if p.enum: return ", ".join(map(str, p.enum))
    if p.min is not None or p.max is not None: return f"{p.min} … {p.max}" + (f" {p.unit}" if p.unit else "")
    return {"color": "#rrggbb / green / blue / ...", "points": "list of numbers or [x,y] points", "asset": "asset id (" + ", ".join(p.asset_types) + ")", "boolean": "true / false"}.get(p.type, "")

def story():
    s = []
    s += [P("EVP Editor — Blueprint Guide for AI models", H1),
          P("<b>What this document is.</b> The EVP Editor is a video-editing program. You (an AI model) write one JSON file — a <b>blueprint</b> — that says what to do to a video "
            "(remove a background, put a new one behind the person, colour-grade, cut, add text, …). The program checks the blueprint very strictly and then runs it. "
            "This guide lists exactly what the program accepts, so the blueprint you write passes validation the first time.", B),
          P("<b>The single most important rule:</b> the program accepts <b>only</b> the operations and parameter names listed in this guide. "
            "<b>Any unknown name, wrong type or out-of-range number is an error and nothing runs.</b> Do not invent parameters, do not nest extra objects inside parameters, "
            "and do not use 0–100 scales unless the table says so. If a wanted feature is not listed, leave it out and say so in your answer — never fake it.", BOX),
          P("1. How to answer the user", H2),
          P("1) Read the user's request. 2) Pick operations from section 4. 3) Output <b>only</b> the JSON object (no comments, no trailing commas, no markdown prose inside it). "
            "4) Use only the example shapes in section 5 as templates. 5) After the JSON, list any wish you could not map to a real control.", B),
          P("2. File layout", H2),
          code('''{
  "blueprint_version": "1.0",            // required, exactly "1.0"
  "project":   { "name": "...", "notes": "..." },        // optional, informational only
  "output":    { ... },                                  // optional, see 2.1
  "assets":    [ { "id": "...", "type": "image|video|lut", "path": "assets/file.png" } ],
  "clips":     [ { "id": "...", "start": 0, "end": 12.5 } ],      // optional cuts, see 2.3
  "operations":[ { "id": "...", "type": "...", "start": 0, "end": 6,
                   "parameters": { ... }, "keyframes": { ... }, "depends_on": ["other_id"], "enabled": true } ]
}'''),
          P("(The <font face='Courier'>//</font> notes above are explanation only — real JSON must not contain comments.)", B),
          P("Do not add other top-level keys (they only produce a warning, but they do nothing). "
            "The source video itself is NOT named in the blueprint: the user picks it in the app (<font face='Courier'>project.source_video</font> is only a label).", B),
          P("2.1 output", H3)]
    s.append(table([[C(a), C(b), C(c)] for a, b, c in [
        ("field", "values", "notes"),
        ("resolution", '"source" or "WIDTHxHEIGHT"', "even numbers, 64..7680x4320. Default source."),
        ("fps", '"source" or number 1..120', "Default source."),
        ("format", "mp4 | mov | webm", "mp4 default."),
        ("codec", "h264 | h265 | prores | prores4444 | vp9_alpha", "prores/prores4444 need format mov. vp9_alpha needs format webm (and webm needs vp9_alpha). prores4444 / vp9_alpha keep transparency."),
        ("crf", "integer 0..51", "Quality, lower = better. 17-20 is high quality. h264/h265 only."),
        ("preset", "ultrafast | veryfast | fast | medium | slow", "Speed vs file size."),
        ("prores_profile", "proxy | lt | standard | hq", "Only for codec prores."),
        ("video_bitrate / audio_bitrate", '"8M" / "192k"', "Optional; usually omit.")]], [32 * mm, 62 * mm, 80 * mm]))
    s += [P("2.2 assets", H3),
          P("Each asset has a unique <font face='Courier'>id</font>, a <font face='Courier'>type</font> (image: png/jpg/webp/bmp; video: mp4/mov/mkv/webm/avi; lut: .cube/.3dl) and a relative "
            "<font face='Courier'>path</font> such as <font face='Courier'>assets/studio_bg.png</font>. Absolute paths, '..', URLs and network paths are rejected. "
            "Operations refer to an asset by its <b>id</b>, never by path.", B),
          P("2.3 clips (cutting)", H3),
          P("Optional. The timeline becomes the listed source ranges played back to back (audio follows). Example: "
            "<font face='Courier'>\"clips\": [{\"id\":\"a\",\"start\":0,\"end\":12.5},{\"id\":\"b\",\"start\":\"0:31.0\",\"end\":\"1:02.5\"}]</font>. "
            "Operation start/end then refer to the <b>cut</b> timeline. Omit clips to use the whole video.", B),
          P("2.4 operation fields", H3)]
    s.append(table([[C(a), C(b)] for a, b in [
        ("field", "meaning"),
        ("id", "unique, only letters digits _ . - (max 64 chars)."),
        ("type", "one of the operation types in section 4."),
        ("start / end", "seconds (1.5), \"1:02.5\" or \"00:01:02.500\". Omit end = until the end of the video. end after the video's length is an error. Use start 0 and omit end to cover the whole video (safest)."),
        ("parameters", "object. Only names listed for that type. Missing ones use the default shown in section 4."),
        ("depends_on", "list of ids this operation builds on. REQUIRED link: background_replacement -> its key (chroma_key / background_removal); studio_look -> the background_replacement it finishes. No cycles."),
        ("keyframes", "optional animation: { \"param\": [ {\"time\": seconds_after_operation_start, \"value\": n, \"easing\": \"linear|hold|ease_in|ease_out|ease_in_out\"} ] }. Only parameters marked [anim] in section 4."),
        ("enabled", "false = skip this operation.")]], [30 * mm, 144 * mm]))
    s += [P("3. Golden rules (the validator enforces all of these)", H2)]
    for r in [
        "Parameters are <b>flat</b>: a single number, string, true/false, colour or list. <b>No nested objects</b> inside <font face='Courier'>parameters</font> (except the point lists named in section 4).",
        "Numbers must be inside the shown range. Scales differ per control — always read the range column (e.g. contrast 1.0 = unchanged, <u>not</u> 0..100).",
        "Enum values must be spelled exactly as listed.",
        "Every operation needs a unique id. Reference assets by id.",
        "<font face='Courier'>background_replacement</font> must be connected to a key that covers its whole time range (<font face='Courier'>depends_on</font>).",
        "<font face='Courier'>studio_look</font> is a one-step 'real studio' finish for a composite. For a plain natural grade without a new background use <font face='Courier'>color_grading</font> instead.",
        "Two overlapping <font face='Courier'>background_replacement</font>s are an error. Overlapping grades simply stack.",
        "Face tools (face_tone_correction, face_relighting) and AI tools need the person's face / models; the app reports a warning if something is missing.",
        "Never claim a feature the guide does not list. If the user asks for something unsupported, omit it and tell the user."]:
        s.append(P("• " + r, B))
    s += [P("A typical mistake (this exact blueprint style was rejected)", H3),
          code(WRONG),
          P("Rejected: <font face='Courier'>studio_look</font> has no 'white_balance', 'tone' or 'look_name'. Use <font face='Courier'>color_grading</font> with flat parameters "
            "(temperature, tint, exposure, contrast, …) instead.", BOX),
          PageBreak(), P("4. Operation catalogue (generated from the program itself)", H1),
          P("Legend: [anim] after a name = can be animated with keyframes. “default” is used when you omit the parameter. “[ai]”, “[screen]”, “[chroma]”, “[needs background_replacement]” in a description say when a control has an effect.", B)]
    for t, sp in OPERATIONS.items():
        if not sp.supported: continue
        s.append(P(f"<font face='Courier'>{t}</font> — {escape(sp.title)}", H2))
        s.append(P(escape(sp.description), B))
        notes = []
        if sp.provides_alpha: notes.append("Provides a transparent layer that <b>background_replacement</b> can use.")
        if t == "background_replacement": notes.append("Needs a key: put its id in <b>depends_on</b>.")
        if t == "studio_look": notes.append("Put the background_replacement id in <b>depends_on</b> to integrate the subject into the new scene.")
        if notes: s.append(P(" ".join(notes), B))
        rows = [[C("parameter"), C("type"), C("default"), C("range / values"), C("description")]]
        for k, p in sp.params.items():
            rows.append([C(k + (" [anim]" if p.animatable else "")), C(p.type), C("" if p.default is None else json.dumps(p.default) if not isinstance(p.default, str) else p.default),
                         C(rng(p)), C(p.desc + (" REQUIRED." if p.required else ""))])
        s.append(table(rows, [27 * mm, 14 * mm, 17 * mm, 30 * mm, 86 * mm]))
    s += [PageBreak(), P("5. Validated example blueprints", H1),
          P("Each example below was run through the program's real validator while this PDF was generated and was <b>accepted</b>. Copy the shape; change values and ids as needed. "
            "(Asset files must exist in the user's project, e.g. assets/studio_bg.png.)", OKB)]
    for i, (name, (note, bp)) in enumerate(EX.items(), 1):
        s.append(KeepTogether([P(f"5.{i} {escape(name)}", H2), P(escape(note), B), code(bp)]))
    s += [PageBreak(), P("6. Mapping common wishes to real controls", H1)]
    s.append(table([[C(a), C(b)] for a, b in [
        ("User wish", "Use this"),
        ("warmer / cooler picture", "color_grading.temperature (-100..100), tint"),
        ("brighter / darker", "color_grading.exposure (EV, -3..3)"),
        ("more contrast / punch", "color_grading.contrast (1.0=unchanged), clarity (-1..1), blacks / whites"),
        ("protect highlights, open shadows", "color_grading.highlights negative, shadows positive"),
        ("richer colour without orange skin", "color_grading.vibrance (not saturation), plus face_tone_correction"),
        ("sharper / more detail", "color_grading.texture (fine detail), clarity (broad), video_enhancement.sharpen"),
        ("remove haze / fog look", "color_grading.dehaze (+)"),
        ("noise reduction", "video_enhancement.denoise"),
        ("natural skin / even face light", "face_tone_correction (auto target), face_relighting (key_intensity, fill, shadow_depth)"),
        ("cinematic look from a preset", "color_grading.preset (see table) or studio_look.preset"),
        ("copy colours of a reference photo", "studio_look.reference_asset_id + reference_strength"),
        ("remove green/blue screen", "background_removal mode 'screen'"),
        ("remove background from a normal video", "background_removal mode 'ai'"),
        ("new background that looks real", "background_removal -> background_replacement -> studio_look (auto_match 1.0)"),
        ("transparent video file", "background_removal alone + output codec prores4444 (mov) or vp9_alpha (webm)"),
        ("cut / reorder parts", "clips"),
        ("text / titles", "text_overlay"),
        ("louder / quieter audio", "audio_gain"),
        ("higher resolution", "ai_upscale"),
        ("anything with no row here", "NOT supported: leave it out and tell the user.")]], [60 * mm, 114 * mm]))
    s += [P("7. Prompt to paste before your request", H2),
          code("""You are generating an editing blueprint for the EVP Editor. Use the attached guide.
Output ONLY one JSON object (blueprint_version "1.0"), valid JSON, no comments.
Use only operation types and parameter names from section 4, with flat parameters inside the ranges shown.
Never nest objects inside parameters. Give every operation a unique id. Use depends_on to link
background_replacement -> key and studio_look -> background_replacement. Reference assets by id,
files by relative path (assets/name.png). If something I ask for is not supported, leave it out
and list it after the JSON. My video: {DURATION}s, {WIDTH}x{HEIGHT} @ {FPS}fps. My request: ...""")]
    return s

def footer(c, d):
    c.saveState(); c.setFont("Helvetica", 7.5); c.setFillColor(colors.grey)
    c.drawString(18 * mm, 10 * mm, "EVP Editor - Blueprint Guide for AI models (generated from the validator's registry)")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {d.page}"); c.restoreState()

if __name__ == "__main__":
    validate_all()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title="EVP Editor - Blueprint Guide for AI models").build(story(), onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT)
