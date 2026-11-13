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


# --------------------------------------------------------------------------
# Pure frame math -- no Resolve object involved.
# --------------------------------------------------------------------------

def test_merge_cut_ranges_sorts_and_merges_overlaps():
    assert builder._merge_cut_ranges([(500, 600), (100, 200), (150, 300)]) == [(100, 300), (500, 600)]


def test_merge_cut_ranges_rejects_invalid_range():
    with pytest.raises(ValueError):
        builder._merge_cut_ranges([(300, 300)])
    with pytest.raises(ValueError):
        builder._merge_cut_ranges([(300, 200)])


def test_kept_subranges_cut_in_middle_splits_into_two():
    kept = builder._kept_subranges(1000, 2000, [(1400, 1600)])
    assert kept == [(1000, 1400), (1600, 2000)]


def test_kept_subranges_cut_covers_whole_item_returns_nothing():
    assert builder._kept_subranges(1000, 2000, [(500, 2500)]) == []


def test_kept_subranges_cut_outside_item_returns_whole_item_unchanged():
    assert builder._kept_subranges(1000, 2000, [(3000, 4000)]) == [(1000, 2000)]


def test_kept_subranges_multiple_cuts_leave_multiple_pieces():
    kept = builder._kept_subranges(0, 1000, [(100, 200), (500, 600)])
    assert kept == [(0, 100), (200, 500), (600, 1000)]


def test_kept_subranges_cut_flush_with_start_leaves_only_the_tail():
    assert builder._kept_subranges(1000, 2000, [(1000, 1400)]) == [(1400, 2000)]


# --------------------------------------------------------------------------
# trim_timeline_ranges -- the real duplicate-and-rebuild engine.
# --------------------------------------------------------------------------

def test_trim_removes_middle_range_and_ripples_video_and_audio_together():
    original = FakeAssemblyTimeline("Editor's real cut", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000, source_start=0)
    _clip(original, "audio", 1, lav, start=86400, duration=1000, source_start=0)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [(86800, 87000)])

    assert result.original_timeline_name == "Editor's real cut"
    assert result.original_clip_count == 2
    assert result.new_clip_count == 4
    assert result.frames_removed == 200

    video_items = result.timeline.tracks["video"][1]
    audio_items = result.timeline.tracks["audio"][1]
    assert [(i.GetStart(), i.GetEnd()) for i in video_items] == [(86400, 86800), (86800, 87200)]
    assert [(i.source_start, i.source_start + i.duration) for i in video_items] == [(0, 400), (600, 1000)]
    # Audio rippled identically -- same positions, same source math, in sync.
    assert [(i.GetStart(), i.GetEnd()) for i in audio_items] == [(86400, 86800), (86800, 87200)]


def test_trim_never_modifies_the_original_timeline():
    original = FakeAssemblyTimeline("Editor's real cut", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000, source_start=0)
    handles = _handles(original)

    builder.trim_timeline_ranges(handles, [(86800, 87000)])

    assert len(original.tracks["video"][1]) == 1
    only = original.tracks["video"][1][0]
    assert (only.GetStart(), only.GetEnd()) == (86400, 87400)


def test_trim_keeps_multiple_tracks_in_sync():
    original = FakeAssemblyTimeline("Multicam sync", start_frame=86400)
    original.tracks = {"video": {1: []}, "audio": {1: [], 2: []}}
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    boom = FakeMediaPoolItem(path="/media/boom.wav", frames=100000, start=0)
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    _clip(original, "audio", 1, boom, start=86400, duration=1000)
    _clip(original, "audio", 2, lav, start=86400, duration=1000)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [(86800, 87000)])

    durations = set()
    for track_type, index in (("video", 1), ("audio", 1), ("audio", 2)):
        items = result.timeline.tracks[track_type][index]
        total = sum(i.GetDuration() for i in items)
        durations.add(total)
    assert durations == {800}  # every track lost exactly the same 200 frames


def test_trim_with_no_cuts_rebuilds_everything_unchanged():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [])

    assert result.new_clip_count == 1
    assert result.frames_removed == 0
    items = result.timeline.tracks["video"][1]
    assert (items[0].GetStart(), items[0].GetEnd()) == (86400, 87400)


def test_trim_raises_when_original_has_no_readable_clips():
    original = FakeAssemblyTimeline("Empty", start_frame=86400)
    handles = _handles(original)

    with pytest.raises(builder.BuilderError, match="nothing to trim"):
        builder.trim_timeline_ranges(handles, [(0, 100)])


def test_trim_raises_when_cuts_remove_everything():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    with pytest.raises(builder.BuilderError, match="remove everything"):
        builder.trim_timeline_ranges(handles, [(86400, 87400)])


def test_trim_raises_when_duplicate_timeline_fails():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    original.duplicate_timeline_returns = None
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    with pytest.raises(builder.BuilderError, match="DuplicateTimeline"):
        builder.trim_timeline_ranges(handles, [(86800, 87000)])
    # The original is named in the failure message and was never touched.
    assert len(original.tracks["video"][1]) == 1


def test_trim_raises_when_delete_clips_fails():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    # DuplicateTimeline copies knobs onto a fresh object, so the failure
    # must be armed on the DUPLICATE, not the original -- patch it in via
    # the original's own DuplicateTimeline for this test.
    real_duplicate = original.DuplicateTimeline

    def duplicate_with_broken_delete(name=None):
        dup = real_duplicate(name)
        dup.delete_clips_returns = False
        return dup

    original.DuplicateTimeline = duplicate_with_broken_delete

    with pytest.raises(builder.BuilderError, match="DeleteClips"):
        builder.trim_timeline_ranges(handles, [(86800, 87000)])


def test_trim_raises_when_append_returns_nothing():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    project = FakeProject()
    project.media_pool.append_returns = []
    handles = _handles(original, project=project)

    with pytest.raises(builder.BuilderError, match="AppendToTimeline"):
        builder.trim_timeline_ranges(handles, [(86800, 87000)])


def test_trim_updates_handles_timeline_on_success():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [(86800, 87000)])

    assert handles.timeline is result.timeline
    assert handles.timeline is not original


def test_trim_honors_explicit_new_name():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [(86800, 87000)], new_name="Edit (trimmed)")

    assert result.timeline_name == "Edit (trimmed)"
    assert result.timeline.GetName() == "Edit (trimmed)"


def test_format_trim_result_reports_the_key_numbers():
    original = FakeAssemblyTimeline("Edit", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    _clip(original, "video", 1, cam, start=86400, duration=1000)
    handles = _handles(original)

    result = builder.trim_timeline_ranges(handles, [(86800, 87000)])
    text = builder.format_trim_result(result)

    assert "Edit" in text
    assert "200" in text
    assert "untouched" in text
