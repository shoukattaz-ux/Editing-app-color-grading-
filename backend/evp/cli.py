"""Headless usage:  python -m evp.cli validate|run|schema|sample ..."""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path

from .compiler import RenderOptions
from .engine import prepare, Job
from .registry import json_schema
from .security import PathPolicy, safe_output_path


def _print_report(d: dict) -> None:
    for e in d["errors"]: print(f"ERROR   {e['code']}: {e['message']}" + (f"  [{e['op_id']}]" if e.get("op_id") else ""))
    for w in d["warnings"]: print(f"WARNING {w['code']}: {w['message']}" + (f"  [{w['op_id']}]" if w.get("op_id") else ""))
    print("VALID" if d["ok"] else "INVALID")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "run"):
        s = sub.add_parser(name)
        s.add_argument("--video", required=True); s.add_argument("--blueprint", required=True)
        s.add_argument("--assets-dir", default=None, help="directory that relative asset paths resolve against (default: blueprint's folder)")
        if name == "run":
            s.add_argument("--out", required=True); s.add_argument("--encoder", default="cpu", choices=["cpu", "auto"])
    sub.add_parser("schema")
    sa = sub.add_parser("sample"); sa.add_argument("dir")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        print(json.dumps(json_schema(), indent=2)); return 0
    if a.cmd == "sample":
        from .samples import make_greenscreen, make_background
        d = Path(a.dir); (d / "assets").mkdir(parents=True, exist_ok=True)
        make_greenscreen(d / "greenscreen.mp4", 6, "640x360"); make_background(d / "assets" / "space_background.png", "640x360")
        print(f"wrote {d}/greenscreen.mp4 and {d}/assets/space_background.png"); return 0

    bpf = Path(a.blueprint).resolve()
    base = Path(a.assets_dir).resolve() if a.assets_dir else bpf.parent
    pol = PathPolicy([base])
    prep = prepare(bpf.read_text(encoding="utf-8"), a.video, pol, base)
    d = prep.to_json(); _print_report(d)
    if a.cmd == "validate" or not d["ok"]:
        return 0 if d["ok"] else 2
    out = Path(a.out).resolve()
    safe_output_path(out, a.video, out.parent)     # refuses to overwrite the source
    job = Job(prep.plan, str(Path(a.video).resolve()), str(out), RenderOptions(encoder=a.encoder), tempfile.mkdtemp(prefix="evp_"))
    print("Executing:", " -> ".join(o.id for o in prep.plan.steps))
    job.run()
    print(job.state, f"-> {out}" if job.state == "done" else job.error)
    return 0 if job.state == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
