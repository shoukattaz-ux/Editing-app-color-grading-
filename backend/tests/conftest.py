import sys, copy
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evp.samples import make_greenscreen, make_background, make_cube_lut
from evp.security import PathPolicy
from evp.engine import prepare


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return {"dir": d, "video": make_greenscreen(d / "gs.mp4", 3), "bg": make_background(d / "bg.png"),
            "lut": make_cube_lut(d / "warm.cube"), "silent": make_greenscreen(d / "silent.mp4", 2, audio=False)}


@pytest.fixture
def policy(media):
    return PathPolicy([media["dir"]])


def bp(ops=None, assets=None, **kw):
    d = {"blueprint_version": "1.0", "project": {"name": "t"}, "output": {"resolution": "source", "fps": "source"},
         "assets": assets if assets is not None else [], "operations": ops or []}
    d.update(kw)
    return d


@pytest.fixture
def prep(media, policy):
    def _p(blueprint, video=None):
        return prepare(blueprint, video or media["video"], policy, media["dir"])
    return _p


def pixel(png: bytes, x: int, y: int):
    import cv2, numpy as np
    im = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    b, g, r = (int(v) for v in im[y, x])
    return r, g, b
