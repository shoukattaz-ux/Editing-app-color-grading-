"""Regenerates docs/blueprint.schema.json and docs/OPERATIONS.md from the operation registry (single source of truth)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evp.registry import OPERATIONS

DOCS = Path(__file__).resolve().parents[2] / "docs"


def json_schema_doc() -> dict:
    ops = []
    for t, s in OPERATIONS.items():
        if not s.supported: continue
        props = {}
        for k, p in s.params.items():
            d = {"description": p.desc}
            if p.type in ("number", "integer"): d.update(type=p.type, **({"minimum": p.min} if p.min is not None else {}), **({"maximum": p.max} if p.max is not None else {}))
            elif p.type == "boolean": d["type"] = "boolean"
            elif p.type == "enum": d.update(type="string", enum=list(p.enum))
            elif p.type == "color": d.update(type="string", pattern="^(#?[0-9a-fA-F]{6}|green|blue|black|white|red|gray|grey)$")
            elif p.type in ("string", "asset"): d["type"] = "string"
            elif p.type == "points": d["type"] = "array"
            if p.default is not None: d["default"] = p.default
            if p.animatable: d["x-animatable"] = True
            props[k] = d
        ops.append({"if": {"properties": {"type": {"const": t}}},
                    "then": {"properties": {"parameters": {"type": "object", "properties": props, "additionalProperties": False,
                                                           **({"required": [k for k, p in s.params.items() if p.required]} if any(p.required for p in s.params.values()) else {})}}}})
    time = {"oneOf": [{"type": "number", "minimum": 0}, {"type": "string", "pattern": r"^(\d+:)?\d{1,2}:\d{1,2}(\.\d+)?$|^\d+(\.\d+)?$"}]}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://example.invalid/evp/blueprint-1.0.json", "title": "EVP editing blueprint v1.0",
            "type": "object", "required": ["blueprint_version", "operations"],
            "properties": {
                "blueprint_version": {"const": "1.0"},
                "project": {"type": "object", "properties": {"name": {"type": "string"}, "source_video": {"type": "string", "description": "Informational only; the video is chosen in the app."}}},
                "output": {"type": "object", "properties": {
                    "resolution": {"type": "string", "default": "source", "description": "'source' or 'WIDTHxHEIGHT' (even, 64..7680x4320)"},
                    "fps": {"oneOf": [{"const": "source"}, {"type": "number", "minimum": 1, "maximum": 120}]},
                    "format": {"enum": ["mp4", "mov"]}, "codec": {"enum": ["h264", "h265"]}, "crf": {"type": "integer", "minimum": 0, "maximum": 51},
                    "video_bitrate": {"type": "string", "pattern": "^\\d{2,6}[kKmM]$"}, "audio_bitrate": {"type": "string", "pattern": "^\\d{2,4}k$"},
                    "preset": {"enum": ["ultrafast", "veryfast", "fast", "medium", "slow"]}}},
                "assets": {"type": "array", "items": {"type": "object", "required": ["id", "type", "path"], "properties": {
                    "id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"}, "type": {"enum": ["image", "video", "lut"]},
                    "path": {"type": "string", "description": "Relative to the project folder (no '..'); absolute paths must be inside the project."}}}},
                "clips": {"type": "array", "minItems": 1, "maxItems": 500,
                          "description": "Optional cuts: the timeline is these ranges of the source video played back to back (no gaps). Omit to use the whole video. Operation times are on THIS cut timeline.",
                          "items": {"type": "object", "required": ["id"], "properties": {
                              "id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
                              "start": {**time, "default": 0, "description": "Source time of the clip's first frame."},
                              "end": {**time, "description": "Source time after the clip's last frame. Omit for 'until the end of the source'."},
                              "label": {"type": "string"}}, "additionalProperties": False}},
                "operations": {"type": "array", "maxItems": 2000, "items": {
                    "type": "object", "required": ["id", "type"], "properties": {
                        "id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"}, "type": {"enum": [t for t, s in OPERATIONS.items() if s.supported]},
                        "start": {**time, "default": 0}, "end": {**time, "description": "Omit for 'until end of video'."},
                        "parameters": {"type": "object"},
                        "keyframes": {"type": "object", "description": "Animate numeric parameters (those marked animatable in OPERATIONS.md). Keys = parameter names; each value is an array of keyframes.",
                                      "additionalProperties": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "object", "required": ["time", "value"], "properties": {
                                          "time": {"type": "number", "description": "Seconds after the operation's start (may be outside the operation; values are held before the first / after the last keyframe)."},
                                          "value": {"type": "number"},
                                          "easing": {"enum": ["linear", "hold", "ease_in", "ease_out", "ease_in_out"], "default": "linear", "description": "Shape of the segment that STARTS at this keyframe."}},
                                          "additionalProperties": False}}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "enabled": {"type": "boolean", "default": True}, "label": {"type": "string"}},
                    "allOf": ops}}}}


def ops_md() -> str:
    out = ["# Operation reference", "", "_Generated from `backend/evp/registry.py` by `backend/scripts/gen_docs.py` — do not edit by hand._", ""]
    for t, s in OPERATIONS.items():
        out += [f"## `{t}` — {s.title}", "", s.description, ""]
        if not s.supported:
            out += [f"> **Not available in this build.** {s.unsupported_reason}", ""]; continue
        if any(p.animatable for p in s.params.values()): out += ["◆ = can be keyframed (animated over time).", ""]
        out += [f"Timeline track: **{s.track}**" + (" · provides an alpha layer for `background_replacement`" if s.provides_alpha else ""), "",
                "| parameter | type | default | range / values | description |", "|---|---|---|---|---|"]
        for k, p in s.params.items():
            rng = f"{p.min} … {p.max}" if p.min is not None else (", ".join(p.enum) if p.enum else "")
            out.append(f"| `{k}`{' ◆' if p.animatable else ''} | {p.type}{' (required)' if p.required else ''} | {'' if p.default is None else '`' + str(p.default) + '`'} | {rng}{' ' + p.unit if p.unit and rng else ''} | {p.desc} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    (DOCS / "blueprint.schema.json").write_text(json.dumps(json_schema_doc(), indent=2) + "\n")
    (DOCS / "OPERATIONS.md").write_text(ops_md() + "\n")
    print("docs regenerated")
