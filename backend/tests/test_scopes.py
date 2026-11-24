"""Scope maths reference: the vectors the TypeScript scopes must reproduce, and the colour-space definition cross-checked against FFmpeg."""
import json, subprocess, sys
from pathlib import Path
import numpy as np, pytest
from evp.media import ffmpeg_bin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_scope_vectors as g

VEC = Path(__file__).resolve().parents[2] / "shared" / "scope_vectors.json"


def test_shared_vectors_are_current():
    assert json.loads(VEC.read_text()) == json.loads(json.dumps(g.build())), "run backend/scripts/gen_scope_vectors.py"


def test_vector_invariants():
    d = json.loads(VEC.read_text())
    n = d["image"]["width"] * d["image"]["height"]
    for k in ("r", "g", "b", "y"): assert sum(d["histogram"][k]) == n
    for k in "yrgb": assert sum(d["waveform"][k]) == n
    assert sum(d["vectorscope"]) == n
    c = {x["name"]: x for x in d["colors"]}
    assert max(c[k]["radius"] for k in ("grey", "white", "black")) < 1e-9                                  # neutrals sit at the centre
    ang = lambda k: c[k]["angle"]
    # BT.709 vectorscope with Cb on x and Cr up (both scaled to +-0.5): R ~103, Mg ~50, B ~355, Cy ~283, G ~230, Yl ~175 degrees
    assert 100 < ang("red") < 106 and 47 < ang("magenta") < 53 and 350 < ang("blue") < 359
    assert 280 < ang("cyan") < 286 and 226 < ang("green") < 233 and 172 < ang("yellow") < 178
    assert 120 < ang("skin") < 135                                                                       # skin sits on the ~123 degree line
    assert c["red75"]["radius"] == pytest.approx(0.75 * c["red"]["radius"], rel=0.02)                     # linear in saturation


@pytest.mark.parametrize("rgb", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (224, 172, 140), (30, 60, 200), (128, 128, 128), (250, 250, 250), (10, 5, 12)])
def test_colour_definition_matches_ffmpegs_bt709_conversion(rgb):
    """our Y/Cb/Cr (BT.709, full range) must agree with FFmpeg's converter, fed with true RGB bytes"""
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "16x16", "-i", "-",
                        "-vf", "scale=in_range=pc:out_range=pc:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,format=yuv444p",
                        "-frames:v", "1", "-f", "rawvideo", "-"], input=bytes(rgb) * 256, capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    raw = np.frombuffer(r.stdout, np.uint8).reshape(3, 256)
    y8, u8, v8 = (int(raw[i].mean()) for i in range(3))
    y, cb, cr = (float(v) for v in g.ycbcr(*rgb))
    assert abs(y8 - y * 255) <= 1.5 and abs(u8 - (128 + 255 * cb)) <= 1.5 and abs(v8 - (128 + 255 * cr)) <= 1.5, (rgb, (y8, u8, v8), (y * 255, 128 + 255 * cb, 128 + 255 * cr))
