"""Freeze the Python engine into a self-contained folder (no Python needed on the user's machine).

    python scripts/build_engine.py            # -> build/engine/evp-engine[.exe] + libraries

Run it on the OS you are building for (PyInstaller does not cross-compile). Requires: pip install pyinstaller -r backend/requirements.txt
"""
import os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "engine"


def main() -> int:
    work = ROOT / "build" / "pyi-work"
    for d in (OUT, work): shutil.rmtree(d, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "evp-engine", "--console",
           "--distpath", str(OUT.parent / "pyi-dist"), "--workpath", str(work), "--specpath", str(work),
           "--paths", str(ROOT / "backend"),
           "--collect-submodules", "uvicorn", "--collect-submodules", "evp", "--collect-all", "onnxruntime",
           "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.auto", "--hidden-import", "uvicorn.protocols.http.auto",
           "--hidden-import", "uvicorn.lifespan.on",
           # large packages the engine does not use
           "--exclude-module", "tkinter", "--exclude-module", "matplotlib", "--exclude-module", "pytest", "--exclude-module", "IPython",
           str(ROOT / "backend" / "evp_engine.py")]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode: return r.returncode
    shutil.move(str(OUT.parent / "pyi-dist" / "evp-engine"), str(OUT))
    shutil.rmtree(OUT.parent / "pyi-dist", ignore_errors=True)
    print("engine built:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
