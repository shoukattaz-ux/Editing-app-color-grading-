"""A queued render says what it is waiting for, and cancelling it while it waits takes effect at once."""
import threading, time
from conftest import bp
from evp.compiler import RenderOptions
from evp.engine import Job, JobManager


class Held(Job):
    """a render that holds the slot until released (stands in for a long AI step)"""
    release = threading.Event()
    def run(self):
        self.state = "running"; self.current = "AI matting 'k': frame 10/100"; self.progress = 0.1
        self.release.wait(10)
        self.state = "done"


def mk(cls, prep, media, tmp_path, kind="proxy"):
    p = prep(bp(ops=[]))
    return cls(p.plan, media["video"], str(tmp_path / "o.mp4"), RenderOptions(encoder="cpu"), str(tmp_path / "w"), kind)


def test_a_queued_render_names_the_one_it_waits_for(prep, media, tmp_path):
    Held.release = threading.Event()
    m = JobManager()
    a = m.submit(mk(Held, prep, media, tmp_path, "render")); time.sleep(0.3)
    b = m.submit(mk(Held, prep, media, tmp_path)); time.sleep(0.3)
    jb = b.to_json()
    assert jb["state"] == "queued" and jb["waiting_for"]["id"] == a.id and jb["waiting_for"]["kind"] == "render"
    assert "AI matting" in jb["waiting_for"]["current"] and jb["waiting_for"]["progress"] == 0.1
    assert a.to_json()["waiting_for"] is None                                      # the running one waits for nobody
    Held.release.set(); time.sleep(0.5)
    assert b.to_json()["waiting_for"] is None and m.blocker_of(b) is None


def test_cancelling_a_waiting_render_takes_effect_at_once(prep, media, tmp_path):
    Held.release = threading.Event()
    m = JobManager()
    a = m.submit(mk(Held, prep, media, tmp_path)); time.sleep(0.3)
    b = m.submit(mk(Held, prep, media, tmp_path)); time.sleep(0.2)
    b.cancel()
    assert b.state == "cancelled"                                                  # not still "queued" until the first one ends
    Held.release.set(); time.sleep(0.5)
    assert a.state == "done" and b.state == "cancelled"


def test_stopping_the_earlier_render_lets_the_queued_one_start(prep, media, tmp_path):
    Held.release = threading.Event()
    m = JobManager()
    a = m.submit(mk(Held, prep, media, tmp_path)); time.sleep(0.3)
    b = m.submit(mk(Job, prep, media, tmp_path)); time.sleep(0.2)
    assert b.to_json()["waiting_for"]["id"] == a.id
    a._cancel.set(); Held.release.set(); time.sleep(1.5)
    assert b.state != "queued"                                                      # it got its turn
