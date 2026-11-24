import numpy as np, cv2
from conftest import bp
from evp.engine import render_frame, prepare
from evp.pngtools import strip_colour_tags, chunk_names, SIG


def test_frames_are_returned_without_colour_management_tags(prep, media):
    """regression: FFmpeg tags BT.709 frames (cICP/cHRM/gAMA); browsers then colour-convert them and the preview looks ~10% darker"""
    p = prep(bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "parameters": {"saturation": 1.2}}]))
    png = render_frame(p.plan, media["video"], 1.0, None, str(media["dir"]))
    names = chunk_names(png)
    assert names[0] == "IHDR" and names[-1] == "IEND" and "IDAT" in names
    assert not {"cICP", "cHRM", "gAMA", "iCCP", "sRGB", "sBIT"} & set(names), names
    assert cv2.imdecode(np.frombuffer(png, np.uint8), 1).shape == (180, 320, 3)


def test_stripping_changes_no_pixel_and_leaves_other_input_alone():
    ok, enc = cv2.imencode(".png", (np.arange(48 * 64 * 3) % 251).astype(np.uint8).reshape(48, 64, 3))
    raw = enc.tobytes()
    import struct, zlib
    def chunk(kind, data): return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    tagged = raw[:33] + chunk(b"gAMA", struct.pack(">I", 45455)) + chunk(b"cHRM", b"\0" * 32) + raw[33:]        # insert after IHDR
    assert "gAMA" in chunk_names(tagged)
    clean = strip_colour_tags(tagged)
    assert "gAMA" not in chunk_names(clean) and "cHRM" not in chunk_names(clean)
    assert np.array_equal(cv2.imdecode(np.frombuffer(clean, np.uint8), 1), cv2.imdecode(np.frombuffer(raw, np.uint8), 1))
    assert strip_colour_tags(b"not a png") == b"not a png"
