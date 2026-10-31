import pytest

from theodore.resolve import deliver


class _Tl:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class _Project:
    """Render queue that writes the file Resolve would, like the real thing."""

    def __init__(self, names, fail=()):
        self.tls = [_Tl(n) for n in names]
        self.current = self.tls[0]
        self.settings = {}
        self.jobs = {}
        self.deleted = []
        self.fail = set(fail)
        self.codec = None

    def GetName(self):
        return "Acme Spot"

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, tl):
        self.current = tl
        return True

    def GetTimelineCount(self):
        return len(self.tls)

    def GetTimelineByIndex(self, i):
        return self.tls[i - 1]

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.codec = (fmt, codec)
        return True

    def SetRenderSettings(self, s):
        self.settings = s
        return True

    def AddRenderJob(self):
        jid = f"job{len(self.jobs)}"
        self.jobs[jid] = (self.current.name, dict(self.settings))
        return jid

    def StartRendering(self, jobs, isInteractiveMode=False):
        from pathlib import Path
        for j in jobs:
            name, s = self.jobs[j]
            if name not in self.fail:
                (Path(s["TargetDir"]) / f"{s['CustomName']}.mp4").write_bytes(b"x" * 2000)
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, j):
        name, _ = self.jobs[j]
        return {"JobStatus": "Failed" if name in self.fail else "Complete", "CompletionPercentage": 100}

    def DeleteRenderJob(self, j):
        self.deleted.append(j)


class _Resolve:
    def __init__(self):
        self.page = "edit"
        self.opened = []

    def GetCurrentPage(self):
        return self.page

    def OpenPage(self, p):
        self.opened.append(p)


class _H:
    def __init__(self, project):
        self.project = project
        self.resolve = _Resolve()


def test_next_version_skips_past_what_is_already_sent(tmp_path):
    (tmp_path / "Acme_Spot_Master_v2.mp4").write_bytes(b"x")
    (tmp_path / "Other_Master_v9.mp4").write_bytes(b"x")
    assert deliver.next_version(tmp_path, "Acme_Spot") == 3
    assert deliver.next_version(tmp_path / "nope", "Acme_Spot") == 1


def test_delivers_master_and_vertical_with_versioned_names_and_note(tmp_path):
    p = _Project(["Master 16x9", "Master 9x16"])
    h = _H(p)
    out = deliver.deliver(h, ["Master 16x9", "Master 9x16"], str(tmp_path), note="First pass.")
    assert [d["timeline"] for d in out["delivered"]] == ["Master 16x9", "Master 9x16"]
    assert (tmp_path / "Acme_Spot_Master_16x9_v1.mp4").exists()
    assert (tmp_path / "Acme_Spot_Master_9x16_v1.mp4").exists()
    assert p.codec == ("mp4", "H264")
    note = (tmp_path / "Acme_Spot_v1_delivery.md").read_text()
    assert "First pass." in note and "Master_16x9_v1.mp4" in note
    # nothing left in the editor's queue, editor put back where they were
    assert len(p.deleted) == 2
    assert p.current is p.tls[0] and h.resolve.opened == ["edit"]


def test_second_delivery_is_v2_and_never_overwrites(tmp_path):
    h = _H(_Project(["Master"]))
    deliver.deliver(h, None, str(tmp_path))
    out = deliver.deliver(h, None, str(tmp_path))
    assert out["version"] == 2
    assert (tmp_path / "Acme_Spot_Master_v1.mp4").exists() and (tmp_path / "Acme_Spot_Master_v2.mp4").exists()


def test_failed_render_is_reported_not_claimed(tmp_path):
    h = _H(_Project(["Master", "Cutdown"], fail={"Cutdown"}))
    out = deliver.deliver(h, ["Master", "Cutdown"], str(tmp_path))
    assert [d["timeline"] for d in out["delivered"]] == ["Master"]
    assert out["failed"][0]["timeline"] == "Cutdown" and "warning" in out
    assert "Did not render: Cutdown" in (tmp_path / "Acme_Spot_v1_delivery.md").read_text()


def test_unknown_timeline_and_bad_quality(tmp_path):
    h = _H(_Project(["Master"]))
    with pytest.raises(deliver.DeliverError):
        deliver.deliver(h, ["Nope"], str(tmp_path))
    with pytest.raises(deliver.DeliverError):
        deliver.deliver(h, None, str(tmp_path), quality="ultra")


def test_safe_name():
    assert deliver.safe_name("Acme / Spot: v2!") == "Acme_Spot_v2"
    assert deliver.safe_name("") == "Untitled"
