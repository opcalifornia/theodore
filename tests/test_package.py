"""package_shoot: one multicam shoot -> episode, highlight, spot and vertical clips, each its own timeline."""
import pytest

from theodore import brand
from theodore.edit import cleanup, interview, package, panel


class TL:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class Project:
    def __init__(self):
        self.tls = [TL("Acme - Multicam")]
        self.current = self.tls[0]

    def GetTimelineCount(self):
        return len(self.tls)

    def GetTimelineByIndex(self, i):
        return self.tls[i - 1]

    def SetCurrentTimeline(self, tl):
        self.current = tl


class H:
    def __init__(self, project):
        self.project = project

    @property
    def timeline(self):
        return self.project.current


@pytest.fixture
def world(monkeypatch, tmp_path):
    from theodore import config
    from theodore.resolve import organize, timeline_ops, transcribe
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    project = Project()
    log = {"stories": [], "opened_from": []}

    def add(name):
        tl = TL(name)
        project.tls.append(tl)
        project.current = tl

    def fake_story(h, *, use_timeline, brief, target_seconds, name, subjects=None, progress=None, **kw):
        log["opened_from"].append(h.timeline.GetName())
        log["stories"].append({"name": name, "brief": brief, "seconds": target_seconds})
        if "fail me" in name:
            raise RuntimeError("boom")
        add(name)
        return {"seconds": target_seconds, "story": [{"line": f"line for {name}"}], "logline": "x"}

    monkeypatch.setattr(interview, "build_story", fake_story)
    monkeypatch.setattr(cleanup, "clean_up", lambda h, new_name=None, **kw: add(new_name) or {"timeline": new_name})
    monkeypatch.setattr(timeline_ops, "make_aspect_version", lambda h, aspect, name=None, **kw: add(name))
    monkeypatch.setattr(transcribe, "add_captions", lambda h, style: log.setdefault("captioned", []).append(
        h.timeline.GetName()))
    monkeypatch.setattr(organize, "label_tracks", lambda tl: {})
    monkeypatch.setattr(organize, "organize_media", lambda h: {})
    return project, log, (lambda: H(project))


def test_every_product_is_its_own_timeline_built_from_the_multicam(world):
    project, log, connect = world
    brand.update("Acme", tone="warm", calls_to_action=["Book a call"])
    out = package.package_shoot(connect, client="Acme", brand="Acme", brief="Spring promo",
                                source_timeline="Acme - Multicam", vertical_clips=2)
    names = [t.GetName() for t in project.tls]
    assert "Acme - Episode" in names and "Acme - Highlight 120s" in names and "Acme - Spot :30" in names
    assert "Acme - Vertical 2 (9x16)" in names
    assert out["built"] == 5 and not out["failed"]
    assert set(log["opened_from"]) == {"Acme - Multicam"}          # every story starts from the same source
    assert all("warm" in s["brief"] and "Spring promo" in s["brief"] for s in log["stories"])
    assert "Book a call" in log["stories"][1]["brief"]
    assert log["captioned"] == ["Acme - Vertical 1 (9x16)", "Acme - Vertical 2 (9x16)"]


def test_later_verticals_are_told_not_to_reuse_earlier_lines(world):
    _, log, connect = world
    package.package_shoot(connect, client="Acme", source_timeline="Acme - Multicam", episode=False,
                          highlight_seconds=0, spot_seconds=0, vertical_clips=3)
    briefs = [s["brief"] for s in log["stories"]]
    assert "Do NOT use" not in briefs[0]
    assert "line for Acme - Vertical 1" in briefs[1] and "line for Acme - Vertical 2" in briefs[2]


def test_a_failed_product_is_reported_and_the_rest_still_build(world):
    project, log, connect = world
    out = package.package_shoot(connect, client="fail me", source_timeline="Acme - Multicam", vertical_clips=0)
    assert out["failed"] and "boom" in out["failed"][0]["error"]
    assert any(p["ok"] and p["kind"] == "episode" for p in out["products"])


def test_unknown_brand_fails_before_anything_is_built(world):
    _, log, connect = world
    with pytest.raises(ValueError):
        package.package_shoot(connect, client="Acme", brand="nobody", source_timeline="Acme - Multicam")
    assert not log["stories"]


def test_builds_the_multicam_first_when_no_timeline_is_given(world, monkeypatch):
    project, log, connect = world
    seen = {}

    def fake_multicam(h, **kw):
        seen.update(kw)
        project.tls.append(TL(kw["name"]))
        return {"timeline": kw["name"], "cuts": 12, "seconds_per_angle": {"wide": 5}}

    monkeypatch.setattr(panel, "multicam_from_bins", fake_multicam)
    out = package.package_shoot(connect, client="Beta", camera_bin="Cams", lav_bin="Lavs", people=["A", "B"],
                                episode=False, highlight_seconds=60, spot_seconds=0, vertical_clips=0)
    assert seen["camera_bin"] == "Cams" and seen["people"] == ["A", "B"]
    assert out["source_timeline"] == "Beta - Multicam" and out["multicam"]["cuts"] == 12
    assert log["opened_from"] == ["Beta - Multicam"]
