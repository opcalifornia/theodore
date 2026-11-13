"""Phone sharing (iCloud folder) and the multicam preview render."""
import subprocess
from fractions import Fraction

import pytest

from theodore import share
from theodore.edit import panel


@pytest.fixture
def phone(tmp_path, monkeypatch):
    d = tmp_path / "icloud" / "Theodore"
    monkeypatch.setenv("THEODORE_SHARE_DIR", str(d))
    return d


def test_copy_list_and_delete(phone, tmp_path):
    v = tmp_path / "cut.mp4"
    v.write_bytes(b"x" * 1000)
    r = share.copy_for_phone(v)
    assert r["shared"] and (phone / "cut.mp4").is_file() and "Files app" in r["on_phone"]
    assert share.list_shared() == ["cut.mp4"]
    assert share.delete_shared("../cut.mp4")["deleted"] is False        # nothing outside the folder
    assert share.delete_shared("nope.mp4")["deleted"] is False
    assert share.delete_shared("cut.mp4")["deleted"] is True and share.list_shared() == []


def test_big_files_and_missing_icloud_are_declined_with_a_reason(phone, tmp_path, monkeypatch):
    v = tmp_path / "master.mov"
    v.write_bytes(b"x" * 2_000_000)
    assert "over the 1 MB" in share.copy_for_phone(v, max_mb=1)["reason"]
    monkeypatch.delenv("THEODORE_SHARE_DIR")
    assert share.copy_for_phone(v)["shared"] is False and share.share_dir() is None


def _make(path, args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)


def test_render_preview_follows_the_cuts_and_mixes_the_mics(tmp_path):
    cams = []
    for name, src in (("camA", "testsrc"), ("camB", "smptebars")):
        f = tmp_path / f"{name}.mp4"
        _make(f, ["-f", "lavfi", "-i", f"{src}=size=160x90:rate=25:duration=8", "-pix_fmt", "yuv420p"])
        cams.append(panel.Source(name, str(f), "camera", panel._probe(str(f))))
    lav = tmp_path / "lav.wav"
    _make(lav, ["-f", "lavfi", "-i", "sine=frequency=440:duration=8"])
    lavs = [panel.Source("lav", str(lav), "lav", panel._probe(str(lav)), person="A")]
    plan = panel.PanelPlan(cams, lavs, Fraction(25), 8.0, 0.0, cuts=[(0.0, 3.0, "camA", "x"), (3.0, 7.0, "camB", "y")])
    out = panel.render_preview(plan, tmp_path / "out" / "p.mp4", height=90)
    info = panel._probe(str(out))
    assert info["has_video"] and info["has_audio"] and abs(info["duration"] - 7.0) < 0.3
