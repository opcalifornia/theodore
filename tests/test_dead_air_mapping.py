import pytest
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeProject, FakeTimelineItem

from theodore.assembly import builder
from theodore.resolve.connection import ResolveHandles


def _clip(timeline, track_type, index, media_item, start, duration, source_start=0):
    item = FakeTimelineItem(media_item, start, duration, source_start=source_start)
    timeline.tracks.setdefault(track_type, {}).setdefault(index, []).append(item)
    return item


def _handles(timeline, project=None):
    project = project or FakeProject()
    project.timelines.append(timeline)
    project.current_timeline = timeline
    return ResolveHandles(resolve=None, project_manager=None, project=project, timeline=timeline)


SINGLE_SOURCE_TRANSCRIPT = {
    "source_file": "/media/lav.wav",
    "sources": [{"path": "/media/lav.wav"}],
    "fps": "0/1",  # audio-only: no fps of its own -- must never be used for frame math
    "start_timecode": "00:00:00:00",
    "utterances": [
        {"id": "u001", "start": 0.0, "end": 20.0, "source_index": 0, "source_start": 0.0, "source_end": 20.0},
    ],
}

MULTI_SOURCE_TRANSCRIPT = {
    "source_file": "/media/a.wav",
    "sources": [{"path": "/media/a.wav"}, {"path": "/media/b.wav"}],
    "fps": "0/1",
    "start_timecode": "00:00:00:00",
    "utterances": [
        {"id": "u001", "start": 0.0, "end": 10.0, "source_index": 0, "source_start": 0.0, "source_end": 10.0},
        # File B starts recording fresh at 0s, but in the MERGED conversation it begins at 10s.
        {"id": "u002", "start": 10.0, "end": 20.0, "source_index": 1, "source_start": 0.0, "source_end": 10.0},
    ],
}


# --------------------------------------------------------------------------
# _cut_ranges_by_source_index -- pure seconds math, no Resolve involved.
# --------------------------------------------------------------------------

def test_cut_within_a_single_utterance_converts_to_file_relative_seconds():
    by_source = builder._cut_ranges_by_source_index(SINGLE_SOURCE_TRANSCRIPT, [(5.0, 6.0)])
    assert by_source == {0: [(5.0, 6.0)]}


def test_cut_offset_by_merge_converts_back_to_the_original_files_own_time():
    # This cut sits at merged-second 12-13, which is source_index 1's own
    # second 2-3 (file B starts fresh at 0 once merged in at second 10).
    by_source = builder._cut_ranges_by_source_index(MULTI_SOURCE_TRANSCRIPT, [(12.0, 13.0)])
    assert by_source == {1: [(2.0, 3.0)]}


def test_cut_crossing_an_utterance_boundary_splits_per_source():
    by_source = builder._cut_ranges_by_source_index(MULTI_SOURCE_TRANSCRIPT, [(9.0, 11.0)])
    assert by_source == {0: [(9.0, 10.0)], 1: [(0.0, 1.0)]}


def test_transcript_with_no_source_metadata_defaults_to_source_zero_no_offset():
    old_style = {"utterances": [{"id": "u001", "start": 0.0, "end": 20.0}]}
    by_source = builder._cut_ranges_by_source_index(old_style, [(5.0, 6.0)])
    assert by_source == {0: [(5.0, 6.0)]}


# --------------------------------------------------------------------------
# dead_air_timeline_cut_ranges -- the real Resolve-facing mapping.
# --------------------------------------------------------------------------

def test_no_cuts_in_trims_returns_nothing():
    timeline = FakeAssemblyTimeline("Edit", start_frame=86400, fps_str="24")
    handles = _handles(timeline)
    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, {})
    assert ranges == []
    assert warnings == []


def test_single_source_dead_air_maps_to_timeline_frames():
    timeline = FakeAssemblyTimeline("Edit", start_frame=86400, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    # The whole 20s utterance is placed on the timeline starting at 86400.
    _clip(timeline, "audio", 1, lav, start=86400, duration=480, source_start=0)  # 20s @ 24fps
    handles = _handles(timeline)
    trims = {"seg1": {"cuts": [(5.0, 6.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)

    assert warnings == []
    assert ranges == [(86400 + 120, 86400 + 144)]  # 5s-6s @ 24fps = frames 120-144


def test_dead_air_never_uses_the_transcripts_own_fps():
    # transcript["fps"] is "0/1" (audio-only) -- if this were ever used for
    # frame math every cut would silently collapse to frame 0. The timeline
    # itself is a real 30fps project; the result must reflect THAT rate.
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="30")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=600, source_start=0)  # 20s @ 30fps
    handles = _handles(timeline, project=FakeProject(timeline_fps="30"))
    trims = {"seg1": {"cuts": [(5.0, 6.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)

    assert warnings == []
    assert ranges == [(150, 180)]  # 5s-6s @ 30fps


def test_multi_source_cuts_land_on_their_own_matched_clip():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    a = FakeMediaPoolItem(path="/media/a.wav", frames=100000, start=0)
    b = FakeMediaPoolItem(path="/media/b.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, a, start=0, duration=240, source_start=0)      # file A: frames 0-240 (10s)
    _clip(timeline, "audio", 1, b, start=240, duration=240, source_start=0)    # file B: frames 240-480 (10s)
    handles = _handles(timeline)
    # One cut in file A's territory (merged 9-10s), one in file B's (merged 12-13s).
    trims = {"seg1": {"cuts": [(9.0, 10.0), (12.0, 13.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, MULTI_SOURCE_TRANSCRIPT, trims)

    assert warnings == []
    assert ranges == [(216, 240), (288, 312)]


def test_cut_with_no_matching_clip_on_timeline_warns_and_is_skipped():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    handles = _handles(timeline)  # empty timeline -- the source isn't placed anywhere
    trims = {"seg1": {"cuts": [(5.0, 6.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)

    assert ranges == []
    assert len(warnings) == 1
    assert "lav.wav" in warnings[0]


def test_missing_timeline_frame_rate_raises_builder_error():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=480, source_start=0)
    project = FakeProject(timeline_fps=None)
    handles = _handles(timeline, project=project)
    trims = {"seg1": {"cuts": [(5.0, 6.0)]}}

    with pytest.raises(builder.BuilderError, match="frame rate"):
        builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)


def test_cuts_are_sorted_by_timeline_position():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=480, source_start=0)
    handles = _handles(timeline)
    trims = {"seg1": {"cuts": [(15.0, 16.0), (2.0, 3.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)

    assert warnings == []
    assert ranges == [(48, 72), (360, 384)]


def test_result_feeds_directly_into_trim_timeline_ranges():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=480, source_start=0)
    handles = _handles(timeline)
    trims = {"seg1": {"cuts": [(5.0, 6.0)]}}

    ranges, warnings = builder.dead_air_timeline_cut_ranges(handles, SINGLE_SOURCE_TRANSCRIPT, trims)
    assert warnings == []

    result = builder.trim_timeline_ranges(handles, ranges)

    assert result.frames_removed == 24
    assert result.new_clip_count == 2
