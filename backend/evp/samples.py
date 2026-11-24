"""Synthetic test media (no binary fixtures committed)."""
from __future__ import annotations
import subprocess
from pathlib import Path
from .media import ffmpeg_bin


def make_greenscreen(path: str | Path, seconds: float = 3, size="320x180", fps=30, audio=True) -> str:
    """A pure-green screen with a moving skin-toned disc + a white box 'subject'."""
    w, h = size.split("x")
    graph = (f"color=c=0x00ff00:s={size}:r={fps}:d={seconds}[bg];"
             f"color=c=0xd2a07a:s={int(h) // 2}x{int(h) // 2}:r={fps}:d={seconds},format=yuva420p,"
             f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lte(hypot(X-W/2,Y-H/2),W/2),255,0)'[disc];"
             f"[bg][disc]overlay=x='(W-w)/2+40*sin(t*2)':y='(H-h)/2'[v]")
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-filter_complex", graph]
    if audio: cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-map", "[v]"] + (["-map", "0:a", "-c:a", "aac"] if audio else []) + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def make_background(path: str | Path, size="320x180") -> str:
    """Colourful gradient image used as a replacement background."""
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"gradients=s={size}:c0=0x203a6f:c1=0xe0307a:x0=0:y0=0:x1=320:y1=180:d=1",
                    "-frames:v", "1", str(path)], check=True)
    return str(path)


def make_cube_lut(path: str | Path) -> str:
    """Identity-with-red-boost 17^3 .cube LUT."""
    n = 17
    lines = ["TITLE \"test\"", f"LUT_3D_SIZE {n}"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append(f"{min(1.0, r / (n - 1) * 1.3):.5f} {g / (n - 1):.5f} {b / (n - 1):.5f}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
