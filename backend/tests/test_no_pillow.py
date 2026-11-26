"""The app does not install Pillow (PIL): the engine must render every feature without it (a missing module once broke the live preview on Windows)."""
import subprocess, sys, textwrap
from pathlib import Path


def test_engine_renders_floor_and_colour_lights_without_pillow(tmp_path):
    code = textwrap.dedent(f"""
        import sys; sys.modules["PIL"] = None                     # as if Pillow were not installed
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        from pathlib import Path
        from evp.samples import make_greenscreen
        from evp.engine import prepare, render_frame
        from evp.security import PathPolicy
        d = Path({str(tmp_path)!r}); v = str(make_greenscreen(d / "gs.mp4", 2))
        ops = [{{"id": "k", "type": "background_removal", "start": 0, "parameters": {{"mode": "chroma"}}}},
               {{"id": "r", "type": "background_replacement", "start": 0, "depends_on": ["k"], "parameters": {{"color": "#203040", "reflection": 0.4, "contact_shadow": 0.5}}}},
               {{"id": "l", "type": "studio_look", "start": 0, "depends_on": ["r"], "parameters": {{"preset": "none", "colour_light": 0.8, "colour_light_place": "follow", "camera_match": 1.0}}}}]
        p = prepare({{"blueprint_version": "1.0", "operations": ops}}, v, PathPolicy([d]), d, strict=True)
        assert p.plan, [e.message for e in p.report.errors]
        png = render_frame(p.plan, v, 1.0, 320, str(d))
        assert png[:4] == b"\\x89PNG"
        print("OK")
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
