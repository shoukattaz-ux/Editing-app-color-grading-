"""Smart HDR: dark regions up, bright regions down, mid-grey left alone, detail kept, and no halo at the edge between them."""
import subprocess
import numpy as np
import cv2
import pytest
from evp.engine import prepare, render_frame
from evp.security import PathPolicy
from evp.media import ffmpeg_bin
from evp.hdr import gain

W, H = 640, 360


@pytest.fixture
def scene(tmp_path):
    # left half: a DARK textured region (a face in shadow), right half: a BRIGHT textured region (sky / sunlit wall), a mid-grey patch at the bottom
    src = tmp_path / "hdr.mp4"
    g = (f"nullsrc=s={W}x{H}:d=1:r=25,geq=lum='if(lt(X\\,{W // 2})\\,30+12*sin(X/3)*sin(Y/3)\\,225+12*sin(X/3)*sin(Y/3))':cb=128:cr=128[a];"
         f"color=c=0x808080:s=160x60:d=1:r=25[m];[a][m]overlay=240:290,format=yuv420p")
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", g, "-t", "1", str(src)], check=True)
    return tmp_path, src


def render(scene, params=None):
    d, src = scene
    ops = [{"id": "h", "type": "smart_hdr", "start": 0, "parameters": params}] if params is not None else []
    p = prepare({"blueprint_version": "1.0", "operations": ops}, str(src), PathPolicy([d]), d, strict=True)
    assert p.plan, [e.message for e in p.report.errors]
    return cv2.imdecode(np.frombuffer(render_frame(p.plan, str(src), 0.5, None, str(d)), np.uint8), 1).astype(float).mean(2)


dark = (slice(40, 240), slice(40, 260))
bright = (slice(40, 240), slice(380, 600))
mid = (slice(300, 340), slice(260, 380))


def test_darks_up_brights_down_mid_grey_alone(scene):
    a, b = render(scene), render(scene, {"amount": 1.0, "shadows": 0.8, "highlights": 0.8})
    assert b[dark].mean() > a[dark].mean() * 1.4                     # the shadowed region comes up a lot
    assert b[bright].mean() < a[bright].mean() - 15                  # the bright region is held back
    assert abs(b[mid].mean() - a[mid].mean()) < 6                    # mid-grey is left alone


def test_detail_inside_a_region_is_kept(scene):
    a, b = render(scene), render(scene, {"amount": 1.0, "shadows": 0.8, "highlights": 0.8})
    rel = lambda x: x[dark].std() / x[dark].mean()
    assert rel(b) > rel(a) * 0.8                                     # the texture in the shadow is lifted with it, not flattened


def test_no_halo_at_the_edge_between_dark_and_bright(scene):
    b = render(scene, {"amount": 1.0, "shadows": 0.8, "highlights": 0.8, "size": 1.0})
    near_bright, far_bright = b[40:240, W // 2 + 4:W // 2 + 24].mean(), b[bright].mean()
    near_dark, far_dark = b[40:240, W // 2 - 24:W // 2 - 4].mean(), b[dark].mean()
    assert abs(near_bright - far_bright) < 10 and abs(near_dark - far_dark) < 10       # an edge-unaware version glows / darkens here


def test_off_and_validation(scene, prep):
    assert np.abs(render(scene, {"amount": 0.0}) - render(scene)).max() == 0
    assert gain(0.5, 1, 1, 1) == pytest.approx(1.0)
    from conftest import bp
    ok = lambda **kw: prep(bp(ops=[{"id": "h", "type": "smart_hdr", "start": 0, "parameters": kw}])).report.ok
    assert ok(amount=0.5, shadows=1, highlights=0, size=0.2) and not ok(amount=2) and not ok(shadows=-0.1)
