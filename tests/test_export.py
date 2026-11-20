from theodore.export import edl, notes
from theodore.resolve.markers import plan_markers

_TRANSCRIPT = {
    "source_file": "interview.wav",
    "fps": "24",
    "start_timecode": "01:00:00:00",
    "speakers": {"0": "Marcus", "1": "Interviewer"},
    "utterances": [
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 2.0, "text": "What happened?"},
        {"id": "u002", "speaker": "0", "start": 2.0, "end": 10.0, "text": "I went home and thought about it."},
        {"id": "u003", "speaker": "0", "start": 11.0, "end": 15.0, "text": "Also, my dog ran away that week."},
    ],
}

_ANALYSIS = {
    "segments": [
        {
            "id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
            "question_text": "What happened?", "question_label": "Going home",
            "answer_summary": "They went home and thought about it.",
        },
        {
            "id": "s002", "answer_start_utterance": "u003", "answer_end_utterance": "u003",
            "question_text": None, "question_label": "The dog ran away",
            "answer_summary": "Their dog ran away that week.",
        },
    ],
    "selects": [
        {
            "segment_id": "s001", "strength": 0.82, "clean_start_utterance": "u002",
            "clean_end_utterance": "u002", "best_line": "I went home and thought about it.",
            "issues": [],
        },
        {"segment_id": "s002", "strength": 0.3, "issues": ["trails off"]},
    ],
    "themes": [{"id": "t01", "label": "Home life", "description": "..."}],
    "theme_assignments": {"s001": ["t01"], "s002": ["t01"]},
}


def test_build_rows_computes_timecodes_relative_to_source_start():
    rows = notes.build_rows(_TRANSCRIPT, _ANALYSIS)
    assert len(rows) == 2
    # 01:00:00:00 @ 24fps = frame 86400; u002 starts at 2.0s = +48 frames = 86448.
    assert rows[0]["timecode_in"] == "01:00:02:00"
    assert rows[0]["frames_in"] == 86448
    assert rows[0]["theme_labels"] == ["Home life"]


def test_write_markdown_contains_expected_content(tmp_path):
    out = notes.write_markdown(_TRANSCRIPT, _ANALYSIS, tmp_path / "interview_notes.md")
    text = out.read_text()
    assert "Going home" in text
    assert "The dog ran away" in text
    assert "(volunteered, unprompted)" not in text  # notes.py uses its own volunteered marker text
    assert "volunteered" in text
    assert "> I went home and thought about it." in text
    assert "strength 0.82" in text


def test_write_csv_sorted_by_strength_descending(tmp_path):
    out = notes.write_csv(_TRANSCRIPT, _ANALYSIS, tmp_path / "selects.csv")
    lines = out.read_text().splitlines()
    assert lines[0].startswith("timecode_in,")
    # s001 (strength 0.82) should sort above s002 (strength 0.3).
    assert "Going home" in lines[1]
    assert "0.82" in lines[1]
    assert "The dog ran away" in lines[2]


def test_write_edl_uses_loc_comments_and_absolute_frames(tmp_path):
    plans = plan_markers(_TRANSCRIPT, _ANALYSIS)
    out = edl.write_edl(plans, _TRANSCRIPT["fps"], "test_project", tmp_path / "markers.edl")
    text = out.read_text()
    assert "TITLE: test_project" in text
    # s001 has strength 0.82 (>= 0.75) -> Green; s002 is volunteered -> Cyan.
    assert "* LOC: 01:00:02:00 GREEN" in text
    assert "* LOC: 01:00:11:00 CYAN" in text
