"""Photo mode: a photo is edited with the same engine as a video and saved as a picture."""
import io
import numpy as np
import cv2
import pytest
from fastapi.testclient import TestClient

from conftest import bp
from evp import photo
from evp.server import create_app
from evp.media import probe

H = {"x-evp-token": "tok"}


def jpeg_with_orientation(path, img, orientation):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 98]); data = buf.tobytes()
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big") + (1).to_bytes(2, "big") + b"\x01\x12\x00\x03" + (1).to_bytes(4, "big") + orientation.to_bytes(2, "big") + b"\x00\x00" + (0).to_bytes(4, "big")
    app1 = b"Exif\x00\x00" + tiff
    path.write_bytes(data[:2] + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + data[2:])


@pytest.fixture
def img():
    a = np.zeros((200, 300, 3), np.uint8); a[:, :] = (40, 120, 200); a[:100, :150] = (0, 0, 255)       # BGR: red block top-left on an orange-ish field
    return a


def test_a_photo_becomes_a_lossless_one_second_clip_with_the_same_picture(tmp_path, img):
    cv2.imwrite(str(tmp_path / "p.png"), img)
    clip, info = photo.ingest(tmp_path / "p.png", tmp_path / "proj")
    v = probe(clip)
    assert (v.width, v.height) == (300, 200) and abs(v.duration - 3 / 25) < 0.05 and info["name"] == "p.png"
    assert photo.ingest(tmp_path / "p.png", tmp_path / "proj")[0] == clip                            # converted once, reused


def test_phone_photos_keep_their_orientation(tmp_path, img):
    jpeg_with_orientation(tmp_path / "rot.jpg", img, 6)                                                 # EXIF: rotate 90 degrees clockwise
    out = photo.read_photo(tmp_path / "rot.jpg")
    assert out.shape[:2] == (300, 200)                                                                  # portrait now: width 200, height 300
    assert out[0, 199, 2] > 200 and out[0, 199, 0] < 60                                               # the red block moved from top-left to top-right


def test_transparency_odd_sizes_and_huge_pictures(tmp_path):
    rgba = np.zeros((101, 201, 4), np.uint8); rgba[:, :, :3] = (0, 0, 0); rgba[:, :, 3] = 0           # fully transparent
    cv2.imwrite(str(tmp_path / "t.png"), rgba)
    out = photo.read_photo(tmp_path / "t.png")
    assert out.shape[0] % 2 == 0 and out.shape[1] % 2 == 0 and out.min() >= 250                         # even size; transparent = white
    big = np.zeros((40, 20000, 3), np.uint8); cv2.imwrite(str(tmp_path / "big.png"), big)
    assert max(photo.read_photo(tmp_path / "big.png").shape[:2]) <= photo.MAX_SIDE


def test_a_broken_picture_says_so(tmp_path):
    (tmp_path / "bad.jpg").write_bytes(b"not a picture")
    from evp.media import MediaError
    with pytest.raises(MediaError, match="could not be read"):
        photo.read_photo(tmp_path / "bad.jpg")


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x"); (tmp_path / "a (2).png").write_bytes(b"x")
    assert photo.unique_path(tmp_path, "a", ".png").name == "a (3).png"
    assert photo.unique_path(tmp_path, "b", ".png", {str(tmp_path / "b.png")}).name == "b (2).png"      # (the original photo's own name is avoided too)


# ---------------------------------------------------------------------------------------------- through the app
@pytest.fixture
def client(tmp_path):
    return TestClient(create_app("tok", str(tmp_path / "data")))


@pytest.fixture
def proj(client, tmp_path, img):
    cv2.imwrite(str(tmp_path / "me.jpg"), img)
    r = client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "Ph", "source": str(tmp_path / "me.jpg")})
    assert r.status_code == 200, r.text
    return r.json()


def test_opening_a_photo_creates_a_photo_project(proj, tmp_path):
    assert proj["photo"]["name"] == "me.jpg" and (proj["video"]["width"], proj["video"]["height"]) == (300, 200)
    assert proj["source"]["path"].endswith(".mkv") and "photo" in proj["source"]["path"]
    assert (tmp_path / "me.jpg").is_file()                                                              # the original is where it was


def test_switching_to_a_video_leaves_photo_mode(client, proj, media):
    r = client.post("/api/project/source", headers=H, json={"path": media["video"]}).json()
    assert r["photo"] is None


def test_a_photo_is_edited_live_and_saved_as_png_jpg_webp(client, proj, tmp_path):
    ops = [{"id": "g", "type": "color_grading", "start": 0, "end": 1, "parameters": {"exposure": 1.0}}]
    b = bp(ops=ops)
    live = client.post("/api/preview/frame", headers=H, json={"blueprint": b, "t": 0, "width": 960})
    assert live.status_code == 200
    outs = {}
    for fmt in ("png", "jpg", "webp"):
        r = client.post("/api/photo/export", headers=H, json={"blueprint": b, "filename": "my edit", "format": fmt, "quality": 92})
        assert r.status_code == 200, r.text
        f = r.json()["output"]; outs[fmt] = cv2.imread(f)
        assert f.endswith("." + fmt) and "exports" in f
    base = cv2.imread(str(tmp_path / "me.jpg")).astype(float)
    for fmt, im in outs.items():
        assert im.shape == (200, 300, 3)                                                                  # the photo's own size
        assert im.astype(float).mean() > base.mean() + 15                                               # the exposure edit is in the saved picture
    assert abs(outs["png"].astype(float) - outs["jpg"].astype(float)).mean() < 3                         # jpg at quality 92 stays close to the lossless one


def test_saving_twice_never_overwrites_and_can_go_to_your_folder(client, proj, tmp_path):
    b = bp(ops=[])
    dest = tmp_path / "out"; dest.mkdir()
    a = client.post("/api/photo/export", headers=H, json={"blueprint": b, "filename": "x", "format": "png", "out_dir": str(dest)}).json()["output"]
    c = client.post("/api/photo/export", headers=H, json={"blueprint": b, "filename": "x", "format": "png", "out_dir": str(dest)}).json()["output"]
    assert a != c and a.endswith("x.png") and c.endswith("x (2).png")
    assert client.post("/api/photo/export", headers=H, json={"blueprint": b, "format": "gif"}).status_code == 422
    assert client.post("/api/photo/export", headers=H, json={"blueprint": b, "out_dir": str(tmp_path / "nope")}).status_code == 422


def test_the_save_endpoint_refuses_a_video_project(client, tmp_path, media):
    client.post("/api/project/new", headers=H, json={"parent": str(tmp_path), "name": "V", "source": media["video"]})
    assert client.post("/api/photo/export", headers=H, json={"blueprint": bp(ops=[])}).status_code == 409


def test_one_look_is_applied_to_every_photo_in_a_folder(client, proj, tmp_path, img):
    import time
    src = tmp_path / "shoot"; src.mkdir()
    cv2.imwrite(str(src / "a.png"), img); cv2.imwrite(str(src / "b.jpg"), img[:100, :150].copy()); (src / "bad.jpg").write_bytes(b"nope"); (src / "notes.txt").write_text("x")
    b = bp(ops=[{"id": "g", "type": "color_grading", "start": 0, "end": 1, "parameters": {"exposure": 1.0}}])
    r = client.post("/api/photo/batch", headers=H, json={"blueprint": b, "folder": str(src), "format": "png"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]; assert r.json()["total"] == 3                                                  # the .txt is not a photo
    for _ in range(120):
        st = client.get(f"/api/photo/batch/{bid}", headers=H).json()
        if st["state"] != "running": break
        time.sleep(0.5)
    assert st["state"] == "done" and st["done"] == 2 and [f["file"] for f in st["failed"]] == ["bad.jpg"]   # one broken photo does not stop the rest
    out = src / "edited"
    assert sorted(p.name for p in out.iterdir()) == ["a.png", "b.png"]
    a = cv2.imread(str(out / "a.png")); assert a.shape == (200, 300, 3) and a.astype(float).mean() > img.astype(float).mean() + 15
    assert cv2.imread(str(out / "b.png")).shape == (100, 150, 3)                                          # each keeps its own size
    assert cv2.imread(str(src / "a.png")).shape == (200, 300, 3)                                          # originals untouched


def test_batch_rejects_an_empty_or_missing_folder(client, proj, tmp_path):
    b = bp(ops=[])
    (tmp_path / "empty").mkdir()
    assert client.post("/api/photo/batch", headers=H, json={"blueprint": b, "folder": str(tmp_path / "empty")}).status_code == 422
    assert client.post("/api/photo/batch", headers=H, json={"blueprint": b, "folder": str(tmp_path / "nope")}).status_code == 422


def test_effects_written_for_a_long_video_still_work_on_a_photo(client, proj, tmp_path):
    ops = [{"id": "g", "type": "color_grading", "start": 2, "end": 9, "parameters": {"exposure": 1.0}},
           {"id": "v", "type": "video_enhancement", "start": 0, "end": 5, "parameters": {"sharpen": 0.3}}]
    b = bp(ops=ops)
    assert client.post("/api/blueprint/validate", headers=H, json={"blueprint": b}).json()["ok"]
    assert client.post("/api/preview/frame", headers=H, json={"blueprint": b, "t": 0, "width": 640}).status_code == 200
    r = client.post("/api/photo/export", headers=H, json={"blueprint": b, "filename": "long", "format": "png"})
    assert r.status_code == 200, r.text
    base = cv2.imread(str(tmp_path / "me.jpg")).astype(float)
    assert cv2.imread(r.json()["output"]).astype(float).mean() > base.mean() + 15


def test_the_ai_steps_of_a_photo_run_on_three_frames_only(prep, tmp_path, img):
    cv2.imwrite(str(tmp_path / "q.png"), img)
    clip, _ = photo.ingest(tmp_path / "q.png", tmp_path / "proj")
    p = prep(photo.whole_clip(bp(ops=[{"id": "u", "type": "ai_upscale", "start": 0, "end": 5, "parameters": {}}])), str(clip))
    up = [o for o in p.plan.steps if o.type == "ai_upscale"]
    assert up and up[0].end_frame - up[0].start_frame == 3                                         # not 25: every AI step is ~8x cheaper
