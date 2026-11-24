"""Self-test of the FROZEN engine (the one inside the installer): start it, import a video whose tags contain emoji/Cyrillic/CJK through the
one-click endpoint, apply a small blueprint and render one preview frame. Exits non-zero on any failure.

    python scripts/selftest_engine.py [build/engine/evp-engine[.exe]] [build/ffmpeg]
"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
exe = "evp-engine.exe" if sys.platform == "win32" else "evp-engine"
engine = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "engine" / exe
ffdir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "build" / "ffmpeg"
ffmpeg = ffdir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
ffprobe = ffdir / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")


WORK = None


def fail(msg: str):
    print("SELFTEST FAILED:", msg)
    if WORK:                                    # the engine keeps the full traceback of any unexpected error
        log = Path(WORK) / "data" / "engine-errors.log"
        if log.is_file(): print("---- engine-errors.log ----\n" + log.read_text(encoding="utf-8", errors="replace")[-3000:])
    sys.exit(1)


def main():
    global WORK
    work = WORK = Path(tempfile.mkdtemp(prefix="evp-selftest-"))
    video = work / "clip.mp4"
    subprocess.run([str(ffmpeg), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x3c8c3c:s=320x180:r=25:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-metadata", "title=Привет 😀 café — 测试", "-metadata", "comment=☃ snowman", str(video)], check=True)
    env = {**os.environ, "EVP_FFMPEG": str(ffmpeg), "EVP_FFPROBE": str(ffprobe), "EVP_PROJECTS_DIR": str(work / "projects")}
    p = subprocess.Popen([str(engine), "--data-dir", str(work / "data")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        line = p.stdout.readline()
        if not line: fail("the engine printed nothing: " + p.stderr.read()[-800:])
        info = json.loads(line)
        base, H = f"http://127.0.0.1:{info['port']}", {"x-evp-token": info["token"], "content-type": "application/json"}
        def call(method, path, body=None):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=H, method=method)
            try:
                with urllib.request.urlopen(req, timeout=120) as r: return r.read()
            except urllib.error.HTTPError as e: fail(f"{method} {path} -> HTTP {e.code}: {e.read()[:400]!r}")
        for _ in range(50):
            try: call("GET", "/api/health"); break
            except SystemExit: raise
            except Exception: time.sleep(0.2)
        proj = json.loads(call("POST", "/api/project/quick", {"source": str(video)}))
        if not proj.get("video") or proj["video"]["width"] != 320: fail(f"project has no video: {proj}")
        print("one-click project OK:", proj["name"])
        bp = {"blueprint_version": "1.0", "output": {"resolution": "source", "fps": "source"},
              "operations": [{"id": "k", "type": "background_removal", "start": 0, "parameters": {"mode": "screen"}},
                             {"id": "r", "type": "background_replacement", "start": 0, "parameters": {"color": "#204080"}, "depends_on": ["k"]},
                             {"id": "l", "type": "studio_look", "start": 0, "parameters": {"preset": "clean_bright"}}]}
        png = call("POST", "/api/preview/frame", {"blueprint": bp, "t": 0.4, "original": False, "width": None})
        if not png.startswith(b"\x89PNG"): fail("preview frame is not a PNG")
        print("preview frame OK:", len(png), "bytes")
        job = json.loads(call("POST", "/api/render", {"blueprint": bp, "preset": "export", "filename": "selftest"}))["job"]
        for _ in range(240):                                           # a real export through the frozen engine and the bundled FFmpeg
            time.sleep(0.5)
            j = json.loads(call("GET", f"/api/jobs/{job['id']}"))
            if j["state"] in ("done", "failed", "cancelled"): break
        if j["state"] != "done": fail(f"export {j['state']}: {j.get('error')} | {' '.join(j.get('log', [])[-4:])[:600]}")
        out = Path(proj["path"]) / j["output"]
        if not out.is_file() or out.stat().st_size < 1000: fail(f"export file missing or empty: {out}")
        print("export OK:", out.name, out.stat().st_size, "bytes")
    finally:
        p.kill()
    print("SELFTEST PASSED")


if __name__ == "__main__":
    main()
