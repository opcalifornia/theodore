"""Fake DaVinci Resolve objects for tests.

Extends the FakeTimeline pattern from tests/test_markers.py to the media
pool / project / timeline-item surface that assembly/builder.py and
resolve/multicam.py touch. Everything is a plain Python class storing state
in dicts and lists so tests can assert on it directly.

The doubles deliberately reproduce the *failure* behaviour of the real API
as well as the happy path: Resolve reports failure by returning False, None
or an empty list rather than raising, and these fakes do the same (see the
injection knobs on FakeMediaPool).
"""
from __future__ import annotations

from pathlib import Path


class FakeMediaPoolItem:
    """A MediaPoolItem. `frames` is the media's length; `start_tc` is its
    embedded start timecode, which is what makes the absolute-frame vs
    media-relative-frame distinction observable."""

    def __init__(self, path="/media/interview.mov", name=None, frames=100000,
                 start_tc="01:00:00:00", start=0, expose_properties=True):
        self.path = str(path)
        self.name = name or Path(self.path).name
        self.frames = frames
        self.start_tc = start_tc
        self.start = start
        self.expose_properties = expose_properties

    def GetName(self):
        return self.name

    def GetClipProperty(self, name=None):
        if not self.expose_properties:
            # Some clip kinds (notably multicam/compound clips) expose very
            # little; builder.py must cope with that.
            props = {"Clip Name": self.name}
        else:
            props = {
                "File Path": self.path,
                "File Name": Path(self.path).name,
                "Clip Name": self.name,
                "Start TC": self.start_tc,
                "Start": self.start,
                "End": self.start + self.frames - 1,
                "Frames": self.frames,
            }
        if name is None:
            return props
        return props.get(name, "")


class FakeFolder:
    def __init__(self, name="Master", clips=None, subfolders=None):
        self.name = name
        self.clips = list(clips or [])
        self.subfolders = list(subfolders or [])

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subfolders)


class FakeTimelineItem:
    def __init__(self, media_pool_item, start, duration):
        self.media_pool_item = media_pool_item
        self.start = start
        self.duration = duration

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration

    def GetName(self):
        return self.media_pool_item.GetName()


class FakeAssemblyTimeline:
    """A Timeline, with tracks and markers. Same method surface as
    tests/test_markers.py's FakeTimeline plus the track read-back
    builder.py verifies an append with."""

    def __init__(self, name, start_frame=86400, fps_str="24"):
        self.name = name
        self._start_frame = start_frame
        self._fps_str = fps_str
        self.markers = {}
        self.tracks = {"video": {1: []}, "audio": {1: []}}
        self.add_marker_returns = True
        self.has_get_item_list = True

    # -- identity / settings ------------------------------------------------
    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return self._start_frame

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self._fps_str
        return None

    # -- markers ------------------------------------------------------------
    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, frame_id, color, name, note, duration, custom_data):
        if not self.add_marker_returns:
            return False
        self.markers[frame_id] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom_data,
        }
        return True

    def DeleteMarkerAtFrame(self, frame_id):
        self.markers.pop(frame_id, None)
        return True

    # -- tracks -------------------------------------------------------------
    def GetTrackCount(self, track_type):
        return len(self.tracks.get(track_type, {}))

    def GetItemListInTrack(self, track_type, index):
        if not self.has_get_item_list:
            raise AttributeError("GetItemListInTrack")
        return list(self.tracks.get(track_type, {}).get(index, []))


class FakeMediaPool:
    """A MediaPool. The knobs below inject the failure modes Resolve
    signals by return value rather than by raising."""

    def __init__(self, project, root=None, video=True, audio=True, inclusive_end=False):
        self.project = project
        self.root = root or FakeFolder()
        self.current_folder = self.root
        self.video = video          # source has a video stream
        self.audio = audio          # source has an audio stream
        self.inclusive_end = inclusive_end  # endFrame counted inclusively?

        # Failure injection
        self.create_timeline_returns = "ok"   # or None/False to fail
        self.import_returns = "ok"            # or [] to fail
        self.append_returns = "ok"            # or [] / False to fail
        self.drop_appends_after = None        # simulate a partial append
        self.duration_delta = 0               # simulate clamping / off-by-one

        # A real import reads each file's OWN embedded start timecode, so a
        # cross-subject build can import two files with different origins.
        # Keyed by path; anything not listed gets `import_start_tc`.
        self.import_start_tc = "01:00:00:00"
        self.import_start_tc_by_path = {}

        # Recorded calls
        self.imported = []
        self.appended_clip_infos = []
        self.created_timelines = []
        self.added_subfolders = []

    def GetRootFolder(self):
        return self.root

    def SetCurrentFolder(self, folder):
        self.current_folder = folder
        return True

    def AddSubFolder(self, folder, name):
        sub = FakeFolder(name=name)
        folder.subfolders.append(sub)
        self.added_subfolders.append(name)
        return sub

    def ImportMedia(self, paths):
        if self.import_returns != "ok":
            return self.import_returns
        items = [
            FakeMediaPoolItem(
                path=p,
                start_tc=self.import_start_tc_by_path.get(str(p), self.import_start_tc),
            )
            for p in paths
        ]
        self.imported.extend(paths)
        self.current_folder.clips.extend(items)
        return items

    def CreateEmptyTimeline(self, name):
        if self.create_timeline_returns != "ok":
            return self.create_timeline_returns
        timeline = FakeAssemblyTimeline(name)
        self.created_timelines.append(timeline)
        self.project.timelines.append(timeline)
        # Resolve makes a newly created timeline current.
        self.project.current_timeline = timeline
        return timeline

    def AppendToTimeline(self, clip_infos):
        self.appended_clip_infos.append(list(clip_infos))
        if self.append_returns != "ok":
            return self.append_returns

        timeline = self.project.current_timeline
        if timeline is None:
            return []

        created = []
        cursor = timeline.GetStartFrame()
        for index, info in enumerate(clip_infos):
            item = info["mediaPoolItem"]
            start, end = info["startFrame"], info["endFrame"]
            duration = end - start + (1 if self.inclusive_end else 0) + self.duration_delta

            landed = True
            if self.drop_appends_after is not None and index >= self.drop_appends_after:
                landed = False
            # Resolve will not place an edit that falls outside the media.
            if start < 0 or end > item.start + item.frames:
                landed = False

            if landed:
                tl_item = FakeTimelineItem(item, cursor, duration)
                if self.video:
                    timeline.tracks["video"][1].append(tl_item)
                if self.audio:
                    timeline.tracks["audio"].setdefault(1, []).append(tl_item)
                cursor += duration
                created.append(tl_item)
        return created


class FakeProject:
    def __init__(self, name="Test Project", timeline_fps="24", root=None,
                 video=True, audio=True, inclusive_end=False):
        self.name = name
        self.timeline_fps = timeline_fps
        self.timelines = []
        self.current_timeline = None
        self.media_pool = FakeMediaPool(
            self, root=root, video=video, audio=audio, inclusive_end=inclusive_end,
        )
        self.set_current_timeline_returns = True
        self.honour_set_current_timeline = True

    def GetName(self):
        return self.name

    def GetMediaPool(self):
        return self.media_pool

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self.timeline_fps
        return None

    def GetCurrentTimeline(self):
        return self.current_timeline

    def SetCurrentTimeline(self, timeline):
        if self.honour_set_current_timeline:
            self.current_timeline = timeline
        return self.set_current_timeline_returns
