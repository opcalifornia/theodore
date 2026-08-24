import pytest
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeProject, FakeTimelineItem

from theodore.assembly import builder
from theodore.resolve.connection import ResolveHandles


def _clip(timeline, track_type, index, media_item, start, duration, source_start=0):
    item = FakeTimelineItem(media_item, start, duration, source_start=source_start)
    timeline.tracks.setdefault(track_type, {}).setdefault(index, []).append(item)
    return item


def _handles(timeline, project=None):
    project = project or FakeProject(timeline_fps="24")
    project.timelines.append(timeline)
    project.current_timeline = timeline
    return ResolveHandles(resolve=None, project_manager=None, project=project, timeline=timeline)


# Interview: interviewer asks (u001, 0-8s), subject answers (u002, 10-20s),
# interviewer asks again (u003, 22-25s), subject answers (u004, 27-37s).
# Placed on a timeline that runs continuously from frame 0 to 37s @ 24fps.
TRANSCRIPT = {
    "source_file": "/media/lav.wav",
    "sources": [{"path": "/media/lav.wav"}],
    "fps": "0/1",
    "start_timecode": "00:00:00:00",
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 8.0,
         "source_index": 0, "source_start": 0.0, "source_end": 8.0},
        {"id": "u002", "speaker": "0", "start": 10.0, "end": 20.0,
         "source_index": 0, "source_start": 10.0, "source_end": 20.0},
        {"id": "u003", "speaker": "1", "start": 22.0, "end": 25.0,
         "source_index": 0, "source_start": 22.0, "source_end": 25.0},
        {"id": "u004", "speaker": "0", "start": 27.0, "end": 37.0,
         "source_index": 0, "source_start": 27.0, "source_end": 37.0},
    ],
}

ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q1?"},
        {"id": "s002", "answer_start_utterance": "u004", "answer_end_utterance": "u004", "question_text": "Q2?"},
    ],
    "selects": [
        {"segment_id": "s001", "clean_start_utterance": "u002", "clean_end_utterance": "u002"},
        {"segment_id": "s002", "clean_start_utterance": "u004", "clean_end_utterance": "u004"},
    ],
}


def _timeline_covering_the_whole_interview():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=37 * 24, source_start=0)
    return timeline


# --------------------------------------------------------------------------
# excluded_and_gap_timeline_cut_ranges
# --------------------------------------------------------------------------

def test_interviewer_questions_and_gaps_are_cut_answers_are_kept():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {}  # untrimmed -- falls back to clean_start/end_utterance

    ranges, warnings = builder.excluded_and_gap_timeline_cut_ranges(handles, TRANSCRIPT, ANALYSIS, trims)

    assert warnings == []
    # Cuts: [0,10) before first answer, [20,27) between answers, [37,37+pad) tail is empty since clip ends at 37s.
    expected = [(0, 10 * 24), (20 * 24, 27 * 24)]
    assert ranges == expected


def test_excluding_a_segment_cuts_its_whole_span_too():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {}

    ranges, warnings = builder.excluded_and_gap_timeline_cut_ranges(
        handles, TRANSCRIPT, ANALYSIS, trims, excluded_segment_ids={"s002"},
    )

    assert warnings == []
    # s002 (27-37s) is now cut along with the gap right up to it and the tail after it.
    expected = [(0, 10 * 24), (20 * 24, 37 * 24)]
    assert ranges == expected


def test_trimmed_answer_uses_trimmed_bounds_not_raw_utterance():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    # s001's answer is trimmed to 11-19s (narrower than the raw 10-20s utterance).
    trims = {"s001": {"trimmed_start": 11.0, "trimmed_end": 19.0, "cuts": []}}

    ranges, warnings = builder.excluded_and_gap_timeline_cut_ranges(handles, TRANSCRIPT, ANALYSIS, trims)

    assert warnings == []
    # The gap before s001 (0-10s) and its own trimmed-off leading edge
    # (10-11s) are adjacent, so they merge into one cut range -- likewise
    # the trailing edge (19-20s) merges into the gap after it (20-27s).
    assert (0, 11 * 24) in ranges
    assert (19 * 24, 27 * 24) in ranges


def test_no_dead_air_double_counted_with_interior_cuts():
    # A dead-air cut INSIDE a kept segment must not appear in this
    # function's output -- that's dead_air_timeline_cut_ranges's job.
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {"s001": {"trimmed_start": 10.0, "trimmed_end": 20.0, "cuts": [(14.0, 16.0)]}}

    ranges, warnings = builder.excluded_and_gap_timeline_cut_ranges(handles, TRANSCRIPT, ANALYSIS, trims)

    assert warnings == []
    for start, end in ranges:
        assert not (start < 16 * 24 and end > 14 * 24), "interior dead air leaked into gap cuts"


def test_source_not_on_timeline_returns_empty_no_crash():
    timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    handles = _handles(timeline)

    ranges, warnings = builder.excluded_and_gap_timeline_cut_ranges(handles, TRANSCRIPT, ANALYSIS, {})

    assert ranges == []
    assert warnings == []  # no matched clip means no cuts to compute at all, not a warning


# --------------------------------------------------------------------------
# plan_clean_cut / format_clean_cut_preview
# --------------------------------------------------------------------------

def test_plan_clean_cut_combines_dead_air_and_gaps():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {
        "s001": {"trimmed_start": 10.0, "trimmed_end": 20.0, "cuts": [(14.0, 16.0)]},
        "s002": {"trimmed_start": 27.0, "trimmed_end": 37.0, "cuts": []},
    }

    plan = builder.plan_clean_cut(handles, TRANSCRIPT, ANALYSIS, trims)

    assert plan.kept_segment_ids == ["s001", "s002"]
    assert plan.excluded_segment_ids == []
    assert plan.dead_air_ranges == [(14 * 24, 16 * 24)]
    assert (0, 10 * 24) in plan.gap_ranges
    assert (20 * 24, 27 * 24) in plan.gap_ranges
    assert set(plan.all_cut_ranges) == set(plan.dead_air_ranges) | set(plan.gap_ranges)


def test_plan_clean_cut_excluded_segment_not_in_kept_ids():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)

    plan = builder.plan_clean_cut(handles, TRANSCRIPT, ANALYSIS, {}, excluded_segment_ids={"s002"})

    assert plan.kept_segment_ids == ["s001"]
    assert plan.excluded_segment_ids == ["s002"]


def test_plan_clean_cut_feeds_directly_into_trim_timeline_ranges():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {
        "s001": {"trimmed_start": 10.0, "trimmed_end": 20.0, "cuts": []},
        "s002": {"trimmed_start": 27.0, "trimmed_end": 37.0, "cuts": []},
    }

    plan = builder.plan_clean_cut(handles, TRANSCRIPT, ANALYSIS, trims)
    result = builder.trim_timeline_ranges(handles, plan.all_cut_ranges)

    # Kept audio: 10s (s001) + 10s (s002) = 20s survives out of 37s.
    kept_seconds = sum(i.GetDuration() for i in result.timeline.tracks["audio"][1]) / 24
    assert kept_seconds == pytest.approx(20.0)


def test_format_clean_cut_preview_reports_categories():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    trims = {
        "s001": {"trimmed_start": 10.0, "trimmed_end": 20.0, "cuts": [(14.0, 16.0)]},
        "s002": {"trimmed_start": 27.0, "trimmed_end": 37.0, "cuts": []},
    }
    plan = builder.plan_clean_cut(handles, TRANSCRIPT, ANALYSIS, trims, excluded_segment_ids=set())

    text = builder.format_clean_cut_preview(plan, builder.read_timeline_fps(handles))

    assert "2 segment(s) kept, 0 excluded" in text
    assert "interviewer speech / gaps" in text
    assert "dead air / filler" in text
    assert "total to remove" in text


def test_format_clean_cut_preview_lists_excluded_ids():
    timeline = _timeline_covering_the_whole_interview()
    handles = _handles(timeline)
    plan = builder.plan_clean_cut(handles, TRANSCRIPT, ANALYSIS, {}, excluded_segment_ids={"s002"})

    text = builder.format_clean_cut_preview(plan, builder.read_timeline_fps(handles))

    assert "excluded: s002" in text
