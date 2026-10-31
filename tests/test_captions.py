from theodore.assembly import plan as plan_mod
from theodore.export import captions

_TRANSCRIPT = {
    "fps": "24",
    "start_timecode": "01:00:00:00",  # frame 86400
    "speakers": {"0": "Marcus", "1": "Interviewer"},
    "utterances": [
        {
            "id": "u001", "speaker": "1", "start": 0.0, "end": 2.0, "text": "Q1?",
            "words": [{"word": "Q1?", "start": 0.0, "end": 2.0, "confidence": 0.9}],
        },
        {
            "id": "u002", "speaker": "0", "start": 10.0, "end": 12.0, "text": "First answer here.",
            "words": [
                {"word": "First", "start": 10.0, "end": 10.4, "confidence": 0.9},
                {"word": "answer", "start": 10.4, "end": 10.9, "confidence": 0.9},
                {"word": "here.", "start": 10.9, "end": 11.5, "confidence": 0.9},
            ],
        },
        {
            "id": "u003", "speaker": "1", "start": 20.0, "end": 21.0, "text": "Q2?",
            "words": [{"word": "Q2?", "start": 20.0, "end": 21.0, "confidence": 0.9}],
        },
        {
            "id": "u004", "speaker": "0", "start": 30.0, "end": 32.0, "text": "Second answer.",
            "words": [
                {"word": "Second", "start": 30.0, "end": 30.5, "confidence": 0.9},
                {"word": "answer.", "start": 30.5, "end": 31.2, "confidence": 0.9},
            ],
        },
    ],
}

_ANALYSIS = {
    "segments": [
        {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q1?"},
        {"id": "s002", "answer_start_utterance": "u004", "answer_end_utterance": "u004", "question_text": "Q2?"},
    ],
    "selects": [
        {"segment_id": "s001", "strength": 0.5, "clean_start_utterance": "u002", "clean_end_utterance": "u002",
         "best_line": "First answer here.", "best_line_start": 10.0},
        {"segment_id": "s002", "strength": 0.9, "clean_start_utterance": "u004", "clean_end_utterance": "u004",
         "best_line": "Second answer.", "best_line_start": 30.0},
    ],
}

_TRIMS = {}  # no trimming applied -- use full clean utterance range


def _build_plan(handle_frames=0):
    return plan_mod.build_plan(_TRANSCRIPT, _ANALYSIS, _TRIMS, ["s001", "s002"], handle_frames=handle_frames)


def test_build_cues_remaps_second_clip_onto_assembly_timeline_not_source():
    plan = _build_plan()
    cues = captions.build_cues(_TRANSCRIPT, _ANALYSIS, _TRIMS, plan)

    assert len(cues) == 2  # one cue per clip -- no natural pause inside either
    # First clip starts the assembly timeline at 0.0s (no gap for the question).
    assert cues[0].start_seconds == 0.0
    assert cues[0].text == "First answer here."
    # Second clip must start immediately after the first clip's duration on
    # the ASSEMBLY timeline, not at its original source time of 30.0s.
    first_clip_duration = plan[0].duration_frames / 24
    assert abs(cues[1].start_seconds - first_clip_duration) < 1e-6
    assert cues[1].text == "Second answer."


def test_build_cues_breaks_on_long_gap_within_a_clip():
    transcript = {
        "fps": "24", "start_timecode": "00:00:00:00",
        "speakers": {"0": "Marcus"},
        "utterances": [{
            "id": "u010", "speaker": "0", "start": 0.0, "end": 5.0, "text": "Hello. Long gap. Goodbye.",
            "words": [
                {"word": "Hello.", "start": 0.0, "end": 0.3, "confidence": 0.9},
                {"word": "Long", "start": 3.0, "end": 3.3, "confidence": 0.9},  # 2.7s gap, over the 0.5s break threshold
                {"word": "gap.", "start": 3.3, "end": 3.6, "confidence": 0.9},
                {"word": "Goodbye.", "start": 3.7, "end": 4.0, "confidence": 0.9},
            ],
        }],
    }
    analysis = {
        "segments": [{"id": "s001", "answer_start_utterance": "u010", "answer_end_utterance": "u010"}],
        "selects": [],
    }
    plan = plan_mod.build_plan(transcript, analysis, {}, ["s001"], handle_frames=0)
    cues = captions.build_cues(transcript, analysis, {}, plan)
    assert len(cues) == 2
    assert cues[0].text == "Hello."
    assert cues[1].text == "Long gap. Goodbye."


def test_srt_time_formatting():
    assert captions._format_srt_time(0.0) == "00:00:00,000"
    assert captions._format_srt_time(3661.5) == "01:01:01,500"


def test_vtt_time_formatting_uses_dot_not_comma():
    assert captions._format_vtt_time(3661.5) == "01:01:01.500"


def test_write_srt_produces_numbered_cues(tmp_path):
    cues = [captions.Cue(0.0, 1.5, "Hello."), captions.Cue(1.5, 3.0, "World.")]
    out = captions.write_srt(cues, tmp_path / "out.srt")
    text = out.read_text()
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello." in text
    assert "2\n00:00:01,500 --> 00:00:03,000\nWorld." in text


def test_write_vtt_starts_with_webvtt_header(tmp_path):
    cues = [captions.Cue(0.0, 1.0, "Hi.")]
    out = captions.write_vtt(cues, tmp_path / "out.vtt")
    text = out.read_text()
    assert text.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.000" in text


def test_write_quotes_sorted_by_strength_descending(tmp_path):
    out = captions.write_quotes(_ANALYSIS, _TRANSCRIPT, tmp_path / "quotes.txt")
    text = out.read_text()
    assert text.index("Second answer.") < text.index("First answer here.")


def test_write_quotes_skips_segments_without_best_line(tmp_path):
    analysis = {
        "segments": _ANALYSIS["segments"],
        "selects": [{"segment_id": "s001", "strength": 0.5}],  # no best_line
    }
    out = captions.write_quotes(analysis, _TRANSCRIPT, tmp_path / "quotes.txt")
    lines = out.read_text().splitlines()
    assert not any(line.startswith("[") for line in lines)


class _FakeTimelineWithImport:
    def __init__(self, result=True):
        self._result = result
        self.calls = []

    def ImportIntoTimeline(self, path, options):
        self.calls.append((path, options))
        return self._result


class _FakeTimelineNoImport:
    pass


class _FakeTimelineRaises:
    def ImportIntoTimeline(self, path, options):
        raise RuntimeError("boom")


class _FakeHandles:
    def __init__(self, timeline):
        self.timeline = timeline


def test_import_subtitles_success(tmp_path):
    srt_path = tmp_path / "out.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi.\n")
    handles = _FakeHandles(_FakeTimelineWithImport(result=True))
    assert captions.import_subtitles_to_timeline(handles, srt_path) is True


def test_import_subtitles_missing_method_returns_false(tmp_path):
    handles = _FakeHandles(_FakeTimelineNoImport())
    assert captions.import_subtitles_to_timeline(handles, tmp_path / "out.srt") is False


def test_import_subtitles_exception_is_caught(tmp_path):
    handles = _FakeHandles(_FakeTimelineRaises())
    assert captions.import_subtitles_to_timeline(handles, tmp_path / "out.srt") is False
