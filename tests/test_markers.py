import json

from theodore.resolve import markers
from theodore.resolve.connection import ResolveHandles


class FakeTimeline:
    """Minimal stand-in for Resolve's Timeline object -- just enough surface
    to exercise write_markers()'s frame math and collision handling without
    a real Resolve instance running."""

    def __init__(self, start_frame: int, fps_str: str = "24"):
        self._start_frame = start_frame
        self._fps_str = fps_str
        self._markers = {}

    def GetStartFrame(self):
        return self._start_frame

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self._fps_str
        return None

    def GetMarkers(self):
        return dict(self._markers)

    def AddMarker(self, frame_id, color, name, note, duration, custom_data):
        self._markers[frame_id] = {
            "color": color, "name": name, "note": note,
            "duration": duration, "customData": custom_data,
        }
        return True

    def DeleteMarkerAtFrame(self, frame_id):
        self._markers.pop(frame_id, None)
        return True

    def GetName(self):
        return "Fake Timeline"

    def GetStartTimecode(self):
        return "01:00:00:00"


def _handles(timeline):
    return ResolveHandles(resolve=None, project_manager=None, project=None, timeline=timeline)


def _plan(absolute_frame, segment_id="s001", color="Blue"):
    return markers.MarkerPlan(
        absolute_frame=absolute_frame, color=color, name="Test segment",
        note="note", duration_frames=48,
        custom_data={"source": "theodore", "segment_id": segment_id},
        segment_id=segment_id,
    )


def test_write_markers_subtracts_timeline_start_frame():
    # This is the bug the spec calls out explicitly: a timeline starting at
    # 01:00:00:00 (frame 86400 @ 24fps) must not offset every marker by an
    # hour. A marker meant to land at absolute frame 87600 (an hour + 1200
    # frames in) must be written at relative frame 1200, not 87600.
    timeline = FakeTimeline(start_frame=86400)
    handles = _handles(timeline)
    plans = [_plan(absolute_frame=87600)]

    result = markers.write_markers(handles, plans)

    assert result == {"written": 1, "replaced": 0, "offset": 0, "skipped": 0, "total": 1}
    assert 1200 in timeline._markers
    assert 87600 not in timeline._markers


def test_write_markers_offsets_on_collision_with_foreign_marker():
    timeline = FakeTimeline(start_frame=0)
    timeline._markers[100] = {"color": "Red", "customData": ""}  # someone else's marker
    handles = _handles(timeline)
    plans = [_plan(absolute_frame=100)]

    result = markers.write_markers(handles, plans)

    assert result["offset"] == 1
    assert 101 in timeline._markers
    assert timeline._markers[100]["color"] == "Red"  # untouched


def test_write_markers_overwrite_replaces_only_theodores_own_markers():
    timeline = FakeTimeline(start_frame=0)
    timeline._markers[100] = {
        "color": "Blue",
        "customData": json.dumps({"source": "theodore", "segment_id": "old"}),
    }
    handles = _handles(timeline)
    plans = [_plan(absolute_frame=100, segment_id="new")]

    result = markers.write_markers(handles, plans, overwrite=True)

    assert result["replaced"] == 1
    assert json.loads(timeline._markers[100]["customData"])["segment_id"] == "new"


def test_overwrite_does_not_clobber_a_foreign_marker():
    timeline = FakeTimeline(start_frame=0)
    timeline._markers[100] = {"color": "Red", "customData": ""}
    handles = _handles(timeline)
    plans = [_plan(absolute_frame=100)]

    result = markers.write_markers(handles, plans, overwrite=True)

    assert result["offset"] == 1
    assert timeline._markers[100]["color"] == "Red"


def test_skips_segments_landing_before_timeline_start():
    timeline = FakeTimeline(start_frame=1000)
    handles = _handles(timeline)
    plans = [_plan(absolute_frame=10)]

    result = markers.write_markers(handles, plans)

    assert result == {"written": 0, "replaced": 0, "offset": 0, "skipped": 1, "total": 1}
    assert timeline._markers == {}


def test_choose_color_thresholds():
    assert markers.choose_color(0.9, is_volunteered=False) == markers.COLOR_STRONG
    assert markers.choose_color(0.3, is_volunteered=False) == markers.COLOR_WEAK
    assert markers.choose_color(0.5, is_volunteered=False) == markers.COLOR_STANDARD
    assert markers.choose_color(0.9, is_volunteered=True) == markers.COLOR_VOLUNTEERED
    assert markers.choose_color(None, is_volunteered=False) == markers.COLOR_STANDARD


def test_check_fps_match():
    assert markers.check_fps_match("24", "24") is True
    assert markers.check_fps_match("23.976", "24000/1001") is True
    assert markers.check_fps_match("24", "23.976") is False


def test_plan_markers_computes_absolute_frame_from_source_start_timecode():
    transcript = {
        "fps": "24",
        "start_timecode": "01:00:00:00",
        "utterances": [
            {"id": "u001", "start": 0.0, "end": 2.0},
            {"id": "u002", "start": 2.0, "end": 5.0},
        ],
    }
    analysis = {
        "segments": [{
            "id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
            "question_text": "What happened?", "question_label": "The event",
            "answer_summary": "They explained.",
        }],
        "selects": [], "themes": [], "theme_assignments": {},
    }
    plans = markers.plan_markers(transcript, analysis)
    assert len(plans) == 1
    # 01:00:00:00 @ 24fps is frame 86400; u002 starts 2.0s in = +48 frames.
    assert plans[0].absolute_frame == 86400 + 48
    assert plans[0].duration_frames == 3 * 24  # u002 spans 2.0s -> 5.0s = 3s = 72 frames


def test_plan_markers_volunteered_segment_gets_cyan_and_null_question():
    transcript = {
        "fps": "24", "start_timecode": "00:00:00:00",
        "utterances": [{"id": "u001", "start": 0.0, "end": 1.0}],
    }
    analysis = {
        "segments": [{
            "id": "s001", "answer_start_utterance": "u001", "answer_end_utterance": "u001",
            "question_text": None, "question_label": "Unprompted story", "answer_summary": "...",
        }],
        "selects": [], "themes": [], "theme_assignments": {},
    }
    plans = markers.plan_markers(transcript, analysis)
    assert plans[0].color == markers.COLOR_VOLUNTEERED
    assert "(volunteered, unprompted)" in plans[0].note


def test_plan_markers_skips_segments_with_unknown_utterance_ids():
    transcript = {"fps": "24", "start_timecode": "00:00:00:00", "utterances": []}
    analysis = {
        "segments": [{
            "id": "s001", "answer_start_utterance": "u999", "answer_end_utterance": "u999",
            "question_text": "?", "question_label": "x",
        }],
        "selects": [], "themes": [], "theme_assignments": {},
    }
    assert markers.plan_markers(transcript, analysis) == []
