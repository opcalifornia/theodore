"""timeline_ops (move clips, aspect versions), Resolve transcription parsing,
and the docs search -- against a small fake that honours recordFrame,
trackIndex and mediaType the way Resolve 21 does (measured: endFrame is
exclusive, recordFrame is an absolute timeline frame)."""
from __future__ import annotations

import pytest

from theodore.resolve import timeline_ops, transcribe


class Clip:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class Item:
    def __init__(self, tl, mpi, kind, idx, start, src_in, src_out, props=None):
        self.tl, self.mpi, self.kind, self.idx = tl, mpi, kind, idx
        self.start, self.src_in, self.src_out = start, src_in, src_out
        self.props = dict(props or {"ZoomX": 1.0})
        self.grade, self.color, self.enabled = None, "", True

    def GetName(self):
        return self.mpi.GetName() if self.mpi else "Title"

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.GetDuration()

    def GetDuration(self):
        return self.src_out - self.src_in

    def GetSourceStartFrame(self):
        return self.src_in

    def GetSourceEndFrame(self):
        return self.src_out

    def GetMediaPoolItem(self):
        return self.mpi

    def GetProperty(self, key=None):
        return dict(self.props) if key is None else self.props.get(key)

    def SetProperty(self, key, value):
        self.props[key] = value
        return True

    def GetClipEnabled(self):
        return self.enabled

    def SetClipEnabled(self, v):
        self.enabled = v
        return True

    def GetClipColor(self):
        return self.color

    def SetClipColor(self, c):
        self.color = c
        return True

    def CopyGrades(self, targets):
        for t in targets:
            t.grade = self.grade
        return True

    def SmartReframe(self):
        self.props["reframed"] = True
        return True


class Timeline:
    def __init__(self, name, project):
        self.name, self.project = name, project
        self.items = []
        self.markers = {}
        self.settings = {"timelineFrameRate": "24"}
        self.video_tracks, self.audio_tracks = 2, 1
        self.linked = []

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return 86400

    def GetSetting(self, k=None):
        return self.settings.get(k)

    def SetSetting(self, k, v):
        self.settings[k] = v
        return True

    def GetTrackCount(self, kind):
        return {"video": self.video_tracks, "audio": self.audio_tracks}.get(kind, 0)

    def AddTrack(self, kind, *_):
        if kind == "video":
            self.video_tracks += 1
        return True

    def DeleteTrack(self, kind, idx):
        assert not [i for i in self.items if i.kind == kind and i.idx == idx]
        self.video_tracks -= 1
        return True

    def GetItemListInTrack(self, kind, idx):
        return sorted([i for i in self.items if i.kind == kind and i.idx == idx], key=lambda i: i.start)

    def DeleteClips(self, items, ripple=False):
        for it in items:
            self.items.remove(it)
        return True

    def SetClipsLinked(self, items, linked):
        self.linked.append(tuple(items))
        return True

    def GetMarkers(self):
        return dict(self.markers)

    def DeleteMarkerAtFrame(self, f):
        return self.markers.pop(f, None) is not None

    def AddMarker(self, f, color, name, note, duration, custom):
        self.markers[f] = {"color": color, "name": name, "note": note, "duration": duration}
        return True

    def DuplicateTimeline(self, name):
        dup = Timeline(name, self.project)
        dup.video_tracks, dup.audio_tracks = self.video_tracks, self.audio_tracks
        dup.markers = dict(self.markers)
        for i in self.items:
            c = Item(dup, i.mpi, i.kind, i.idx, i.start, i.src_in, i.src_out, i.props)
            c.grade, c.color = i.grade, i.color
            dup.items.append(c)
        self.project.timelines.append(dup)
        return dup


class Pool:
    def __init__(self, project):
        self.project = project

    def AppendToTimeline(self, infos):
        tl = self.project.current
        out = []
        for info in infos:
            kind = "video" if info["mediaType"] == 1 else "audio"
            s, e = info["recordFrame"], info["recordFrame"] + info["endFrame"] - info["startFrame"]
            if any(i.kind == kind and i.idx == info["trackIndex"] and i.start < e and s < i.GetEnd()
                   for i in tl.items):
                return []  # Resolve refuses to place over an existing clip
            it = Item(tl, info["mediaPoolItem"], kind, info["trackIndex"], s,
                      info["startFrame"], info["endFrame"])
            tl.items.append(it)
            out.append(it)
        return out


class Project:
    def __init__(self):
        self.timelines, self.pool = [], Pool(self)
        self.current = None

    def GetMediaPool(self):
        return self.pool

    def SetCurrentTimeline(self, tl):
        self.current = tl
        return True


class H:
    def __init__(self, project):
        self.project = project

    @property
    def timeline(self):
        return self.project.current


def make():
    project = Project()
    tl = Timeline("Edit v1", project)
    project.timelines.append(tl)
    project.current = tl
    a, b, c = Clip("A.mov"), Clip("B.mov"), Clip("C.mov")
    s = 86400
    tl.items += [Item(tl, a, "video", 1, s, 0, 96), Item(tl, a, "audio", 1, s, 0, 96),
                 Item(tl, b, "video", 1, s + 144, 24, 168, {"ZoomX": 1.5}), Item(tl, b, "audio", 1, s + 144, 24, 168),
                 Item(tl, c, "video", 2, s + 48, 0, 48), Item(tl, None, "video", 2, s + 200, 0, 50)]
    tl.items[2].grade, tl.items[2].color = "LUT:CanonLog", "Orange"
    tl.markers = {60: {"color": "Red", "name": "m", "note": "", "duration": 1}}
    return project, tl


def positions(tl, kind, idx):
    return [(i.GetName(), i.start - 86400, i.GetDuration()) for i in tl.GetItemListInTrack(kind, idx)]


def test_move_everything_right_on_a_duplicate():
    project, original = make()
    res = timeline_ops.move_clips(H(project), 1440)
    new = project.current
    assert new is not original and res.original == "Edit v1"
    assert positions(new, "video", 1) == [("A.mov", 1440, 96), ("B.mov", 1584, 144)]
    assert positions(new, "audio", 1) == [("A.mov", 1440, 96), ("B.mov", 1584, 144)]
    moved_b = new.GetItemListInTrack("video", 1)[1]
    assert moved_b.grade == "LUT:CanonLog" and moved_b.color == "Orange" and moved_b.props["ZoomX"] == 1.5
    assert list(new.markers) == [1500]
    # The title can't be re-placed from a script: left alone and reported.
    assert any("Title" in s for s in res.skipped)
    assert positions(original, "video", 1) == [("A.mov", 0, 96), ("B.mov", 144, 144)]
    assert res.warnings == []


def test_small_moves_that_overlap_the_clip_itself_keep_the_grade():
    project, tl = make()
    res = timeline_ops.move_clips(H(project), 48, tracks=["V1", "A1"], on_duplicate=False)
    assert res.moved == 4 and res.warnings == []
    assert positions(tl, "video", 1) == [("A.mov", 48, 96), ("B.mov", 192, 144)]
    assert tl.GetItemListInTrack("video", 1)[1].grade == "LUT:CanonLog"
    assert tl.video_tracks == 2  # the helper track was removed
    assert list(tl.markers) == [60]  # partial move leaves markers


def test_refuses_to_land_on_a_clip_that_is_not_moving():
    project, tl = make()
    with pytest.raises(timeline_ops.TimelineOpError, match="would land on"):
        timeline_ops.move_clips(H(project), 100, tracks=["V1"], to_frame=86400 + 10, on_duplicate=False)


def test_refuses_to_move_before_the_timeline_start():
    project, _ = make()
    with pytest.raises(timeline_ops.TimelineOpError, match="before the timeline start"):
        timeline_ops.move_clips(H(project), -10, tracks=["V1"], on_duplicate=False)


def test_track_spec_parsing():
    assert timeline_ops.parse_tracks(["V1", "audio2", "video"]) == {("video", 1), ("audio", 2), ("video", None)}
    with pytest.raises(timeline_ops.TimelineOpError):
        timeline_ops.parse_tracks(["X9"])


def test_vertical_version_sets_shape_and_reframes_on_a_duplicate():
    project, original = make()
    out = timeline_ops.make_aspect_version(H(project), "9:16")
    new = project.current
    assert new is not original and out["resolution"] == "1080x1920"
    assert new.settings["timelineResolutionWidth"] == "1080"
    assert new.settings["timelineInputResMismatchBehavior"] == "scaleToCrop"
    assert out["clips_smart_reframed"] == 3 and out["check_these"]  # the title
    assert "timelineResolutionWidth" not in original.settings


def test_resolve_subtitles_become_words_and_sentences():
    class Sub:
        def __init__(self, s, e, t):
            self.s, self.e, self.t = s, e, t

        def GetStart(self):
            return 86400 + self.s

        def GetEnd(self):
            return 86400 + self.e

        def GetName(self):
            return self.t

    class T:
        def GetStartFrame(self):
            return 86400

        def GetTrackCount(self, k):
            return 1

        def GetItemListInTrack(self, k, i):
            return [Sub(0, 12, "Hi,"), Sub(14, 30, "and I"), Sub(30, 48, "started."), Sub(98, 107, "Um,"),
                    Sub(110, 126, "honestly.")]

    words = transcribe._words_from_track(T(), 24.0)
    assert [w["punctuated_word"] for w in words] == ["Hi,", "and", "I", "started.", "Um,", "honestly."]
    assert words[1]["start_frame"] == 14 and words[2]["end_frame"] == 30
    sents = transcribe.sentences(words)
    assert [s["text"] for s in sents] == ["Hi, and I started.", "Um, honestly."]


def test_docs_search_finds_methods():
    from theodore.resolve import docs
    if not docs._readme_lines():
        pytest.skip("Resolve's scripting README isn't installed here")
    hits = docs.search("SmartReframe")["matches"]
    assert any("SmartReframe()" in m["text"] for m in hits)
