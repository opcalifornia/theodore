from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeTimelineItem
from theodore.assembly import builder


def _add_clip(timeline, track_type, index, media_item, start, duration):
    item = FakeTimelineItem(media_item, start, duration)
    timeline.tracks.setdefault(track_type, {}).setdefault(index, []).append(item)
    return item


def test_matches_a_clip_on_video_track_by_file_path():
    timeline = FakeAssemblyTimeline("Edit")
    dji = FakeMediaPoolItem(path="/media/haylee.mov", start=0, frames=1000)
    _add_clip(timeline, "video", 1, dji, start=86400, duration=500)

    matches = builder.match_timeline_to_sources(timeline, ["/media/haylee.mov"])

    assert len(matches) == 1
    m = matches[0]
    assert m.source_path == "/media/haylee.mov"
    assert m.track_type == "video"
    assert m.track_index == 1
    assert m.timeline_start_frame == 86400
    assert m.timeline_end_frame == 86400 + 500


def test_matches_external_audio_on_a_second_audio_track():
    # The real-world case this exists for: Auto Sync Audio -> Append Tracks
    # lands the external recorder's audio on a track beyond the first.
    timeline = FakeAssemblyTimeline("Edit")
    timeline.tracks = {"video": {1: []}, "audio": {1: [], 2: []}}
    camera = FakeMediaPoolItem(path="/media/cam_a.mov")
    lav = FakeMediaPoolItem(path="/media/DJI_17.WAV")
    _add_clip(timeline, "video", 1, camera, start=100, duration=50)
    _add_clip(timeline, "audio", 2, lav, start=100, duration=50)

    matches = builder.match_timeline_to_sources(timeline, ["/media/cam_a.mov", "/media/DJI_17.WAV"])

    assert {m.source_path for m in matches} == {"/media/cam_a.mov", "/media/DJI_17.WAV"}
    audio_match = next(m for m in matches if m.source_path == "/media/DJI_17.WAV")
    assert audio_match.track_type == "audio"
    assert audio_match.track_index == 2


def test_unmatched_clips_are_silently_skipped_not_an_error():
    timeline = FakeAssemblyTimeline("Edit")
    broll = FakeMediaPoolItem(path="/media/broll_drone_shot.mov")
    _add_clip(timeline, "video", 1, broll, start=0, duration=100)

    matches = builder.match_timeline_to_sources(timeline, ["/media/haylee.mov"])

    assert matches == []


def test_same_clip_used_twice_produces_two_matches():
    timeline = FakeAssemblyTimeline("Edit")
    clip = FakeMediaPoolItem(path="/media/haylee.mov")
    _add_clip(timeline, "video", 1, clip, start=0, duration=100)
    _add_clip(timeline, "video", 1, clip, start=500, duration=100)

    matches = builder.match_timeline_to_sources(timeline, ["/media/haylee.mov"])

    assert len(matches) == 2
    assert [m.timeline_start_frame for m in matches] == [0, 500]


def test_no_known_sources_returns_nothing_without_touching_the_timeline():
    timeline = FakeAssemblyTimeline("Edit")
    matches = builder.match_timeline_to_sources(timeline, [])
    assert matches == []


def test_results_are_sorted_by_timeline_position():
    timeline = FakeAssemblyTimeline("Edit")
    clip_a = FakeMediaPoolItem(path="/media/a.mov")
    clip_b = FakeMediaPoolItem(path="/media/b.mov")
    _add_clip(timeline, "video", 1, clip_b, start=500, duration=50)
    _add_clip(timeline, "video", 1, clip_a, start=0, duration=50)

    matches = builder.match_timeline_to_sources(timeline, ["/media/a.mov", "/media/b.mov"])

    assert [m.source_path for m in matches] == ["/media/a.mov", "/media/b.mov"]


def test_get_item_list_in_track_unavailable_does_not_crash():
    timeline = FakeAssemblyTimeline("Edit")
    timeline.has_get_item_list = False
    clip = FakeMediaPoolItem(path="/media/haylee.mov")
    _add_clip(timeline, "video", 1, clip, start=0, duration=100)

    matches = builder.match_timeline_to_sources(timeline, ["/media/haylee.mov"])

    assert matches == []


def test_format_timeline_matches_reports_none_found():
    text = builder.format_timeline_matches([])
    assert "No clips" in text


def test_format_timeline_matches_lists_each_match():
    timeline = FakeAssemblyTimeline("Edit")
    clip = FakeMediaPoolItem(path="/media/DJI_17.WAV", name="DJI_17.WAV")
    _add_clip(timeline, "audio", 1, clip, start=1000, duration=500)
    matches = builder.match_timeline_to_sources(timeline, ["/media/DJI_17.WAV"])

    text = builder.format_timeline_matches(matches)

    assert "DJI_17.WAV" in text
    assert "1000" in text
    assert "1500" in text
