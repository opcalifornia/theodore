import pytest
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeProject, FakeTimelineItem

from theodore.assembly import builder
from theodore.export import captions
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


TRANSCRIPT = {
    "source_file": "/media/lav.wav",
    "sources": [{"path": "/media/lav.wav"}],
    "fps": "0/1",
    "start_timecode": "00:00:00:00",
    "utterances": [
        {
            "id": "u001", "speaker": "0", "start": 10.0, "end": 12.0,
            "source_index": 0, "source_start": 10.0, "source_end": 12.0,
            "words": [
                {"word": "First", "start": 10.0, "end": 10.4},
                {"word": "answer", "start": 10.4, "end": 10.9},
                {"word": "here.", "start": 10.9, "end": 11.5},
            ],
        },
    ],
}

ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u001", "answer_end_utterance": "u001", "question_text": "Q1?"},
    ],
    "selects": [
        {"segment_id": "s001", "strength": 0.5, "clean_start_utterance": "u001", "clean_end_utterance": "u001"},
    ],
}


def test_words_placed_at_their_real_timeline_position():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    # File starts at timeline frame 240 (10s in) -- source_start=0 means
    # frame 240 on the timeline corresponds to second 0 of the source file.
    _clip(timeline, "audio", 1, lav, start=240, duration=480, source_start=0)
    handles = _handles(timeline)

    cues, warnings = captions.build_cues_for_open_timeline(handles, TRANSCRIPT, ANALYSIS, {})

    assert warnings == []
    assert len(cues) == 1
    # Word "First" starts at source second 10 -> timeline frame 240+240=480 -> 20.0s.
    assert cues[0].start_seconds == pytest.approx(20.0)
    assert cues[0].text == "First answer here."


def test_trimmed_out_words_get_no_caption():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=480, source_start=0)
    handles = _handles(timeline)
    # Trim narrows the kept range to exclude the leading word "First".
    trims = {"s001": {"trimmed_start": 10.4, "trimmed_end": 11.5, "cuts": []}}

    cues, warnings = captions.build_cues_for_open_timeline(handles, TRANSCRIPT, ANALYSIS, trims)

    assert warnings == []
    assert len(cues) == 1
    assert cues[0].text == "answer here."


def test_word_with_no_matching_clip_on_timeline_warns_and_is_skipped():
    timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    handles = _handles(timeline)

    cues, warnings = captions.build_cues_for_open_timeline(handles, TRANSCRIPT, ANALYSIS, {})

    assert cues == []
    assert len(warnings) == 1
    assert "lav.wav" in warnings[0]


def test_multi_source_words_land_on_their_own_matched_clip():
    transcript = {
        "source_file": "/media/a.wav",
        "sources": [{"path": "/media/a.wav"}, {"path": "/media/b.wav"}],
        "fps": "0/1",
        "start_timecode": "00:00:00:00",
        "utterances": [
            {"id": "u001", "speaker": "0", "start": 0.0, "end": 10.0,
             "source_index": 0, "source_start": 0.0, "source_end": 10.0,
             "words": [{"word": "Hello", "start": 1.0, "end": 1.5}]},
            {"id": "u002", "speaker": "0", "start": 10.0, "end": 20.0,
             "source_index": 1, "source_start": 0.0, "source_end": 10.0,
             "words": [{"word": "World", "start": 12.0, "end": 12.5}]},
        ],
    }
    analysis = {
        "segments": [
            {"id": "s001", "answer_start_utterance": "u001", "answer_end_utterance": "u001", "question_text": None},
            {"id": "s002", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": None},
        ],
        "selects": [],
    }
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    a = FakeMediaPoolItem(path="/media/a.wav", frames=100000, start=0)
    b = FakeMediaPoolItem(path="/media/b.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, a, start=0, duration=240, source_start=0)     # 0-10s
    _clip(timeline, "audio", 1, b, start=240, duration=240, source_start=0)   # 10-20s (merged) -> file B 0-10s
    handles = _handles(timeline)

    cues, warnings = captions.build_cues_for_open_timeline(handles, transcript, analysis, {})

    assert warnings == []
    assert len(cues) == 2
    texts_by_start = {round(c.start_seconds, 2): c.text for c in cues}
    assert texts_by_start[1.0] == "Hello"
    assert texts_by_start[240 / 24 + 2.0] == "World"  # clip B starts at timeline 10s + word at file-second 2


def test_no_segments_returns_no_cues():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    handles = _handles(timeline)
    empty_analysis = {"segments": [], "selects": []}

    cues, warnings = captions.build_cues_for_open_timeline(handles, TRANSCRIPT, empty_analysis, {})

    assert cues == []
    assert warnings == []


def test_missing_timeline_frame_rate_raises_builder_error():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=480, source_start=0)
    project = FakeProject(timeline_fps=None)
    handles = _handles(timeline, project=project)

    with pytest.raises(builder.BuilderError, match="frame rate"):
        captions.build_cues_for_open_timeline(handles, TRANSCRIPT, ANALYSIS, {})


def test_cues_are_sorted_by_start_time():
    transcript = {
        "source_file": "/media/lav.wav",
        "sources": [{"path": "/media/lav.wav"}],
        "fps": "0/1",
        "start_timecode": "00:00:00:00",
        "utterances": [
            {"id": "u001", "speaker": "0", "start": 20.0, "end": 21.0,
             "source_index": 0, "source_start": 20.0, "source_end": 21.0,
             "words": [{"word": "Second.", "start": 20.0, "end": 20.5}]},
            {"id": "u002", "speaker": "0", "start": 0.0, "end": 1.0,
             "source_index": 0, "source_start": 0.0, "source_end": 1.0,
             "words": [{"word": "First.", "start": 0.0, "end": 0.5}]},
        ],
    }
    analysis = {
        "segments": [
            {"id": "s001", "answer_start_utterance": "u001", "answer_end_utterance": "u001", "question_text": None},
            {"id": "s002", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": None},
        ],
        "selects": [],
    }
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    _clip(timeline, "audio", 1, lav, start=0, duration=600, source_start=0)
    handles = _handles(timeline)

    cues, warnings = captions.build_cues_for_open_timeline(handles, transcript, analysis, {})

    assert warnings == []
    assert [c.text for c in cues] == ["First.", "Second."]
