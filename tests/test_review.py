from theodore.assembly import trim as trim_mod
from theodore.export import review

_TRANSCRIPT = {
    "fps": "24",
    "start_timecode": "01:00:00:00",
    "speakers": {"0": "Marcus", "1": "Interviewer"},
    "utterances": [
        {
            "id": "u001", "speaker": "1", "start": 0.0, "end": 1.0, "text": "Tell me about it.",
            "words": [{"word": "Tell", "start": 0.0, "end": 0.3, "confidence": 0.9}],
        },
        {
            "id": "u002", "speaker": "0", "start": 1.5, "end": 5.0, "text": "Um, it was a big deal.",
            "words": [
                {"word": "Um,", "start": 1.5, "end": 1.7, "confidence": 0.9},
                {"word": "it", "start": 1.7, "end": 1.8, "confidence": 0.9},
                {"word": "was", "start": 1.8, "end": 1.9, "confidence": 0.9},
                {"word": "a", "start": 1.9, "end": 2.0, "confidence": 0.9},
                {"word": "big", "start": 2.0, "end": 2.2, "confidence": 0.9},
                {"word": "deal.", "start": 2.2, "end": 2.5, "confidence": 0.9},
            ],
        },
    ],
}

_ANALYSIS = {
    "segments": [{
        "id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
        "question_text": "Tell me about it.", "question_label": "The big deal",
        "answer_summary": "It was a big deal.",
    }],
    "selects": [{
        "segment_id": "s001", "strength": 0.6, "clean_start_utterance": "u002",
        "clean_end_utterance": "u002", "issues": [],
    }],
    "themes": [{"id": "t01", "label": "Big moments", "description": "..."}],
    "theme_assignments": {"s001": ["t01"]},
}


def _trims():
    return trim_mod.compute_trims(_ANALYSIS, _TRANSCRIPT)


def test_build_rows_strikes_leading_filler_word():
    trims = _trims()
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, trims)
    assert len(rows) == 1
    row = rows[0]
    assert row["speaker"] == "Marcus"
    assert row["theme_labels"] == ["Big moments"]
    assert "<s>Um,</s>" in row["html_text"]
    assert "<s>it</s>" not in row["html_text"]  # "it" is not filler, should render plain
    assert "deal." in row["html_text"]


def test_build_rows_shows_cutmark_for_dead_air_gap_between_words():
    transcript = {
        "fps": "24", "start_timecode": "00:00:00:00",
        "speakers": {"0": "Marcus"},
        "utterances": [{
            "id": "u010", "speaker": "0", "start": 0.0, "end": 5.0, "text": "It was great. Long pause. Then this.",
            "words": [
                {"word": "It", "start": 0.0, "end": 0.2, "confidence": 0.9},
                {"word": "great.", "start": 0.2, "end": 0.5, "confidence": 0.9},
                # 2.0s gap here, over the 1.2s default threshold
                {"word": "Then", "start": 2.5, "end": 2.7, "confidence": 0.9},
                {"word": "this.", "start": 2.7, "end": 2.9, "confidence": 0.9},
            ],
        }],
    }
    analysis = {
        "segments": [{"id": "s001", "answer_start_utterance": "u010", "answer_end_utterance": "u010", "question_text": "Q?"}],
        "selects": [], "themes": [], "theme_assignments": {},
    }
    trims = trim_mod.compute_trims(analysis, transcript)
    rows = review.build_rows(transcript, analysis, trims)
    assert "cutmark" in rows[0]["html_text"]
    assert "2.0s" in rows[0]["html_text"]


def test_build_rows_timecode_uses_trimmed_range_not_full_utterance():
    trims = _trims()
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, trims)
    # "Um," (1.5-1.7) is stripped as leading filler, so trimmed_start = "it" at 1.7.
    # 01:00:00:00 @ 24fps = frame 86400; +1.7s = +40.8 -> floor 40 frames = 86440.
    assert rows[0]["timecode_in"] == "01:00:01:16"


def test_build_rows_respects_explicit_segment_order():
    analysis = {
        "segments": [
            {"id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q"},
            {"id": "s002", "answer_start_utterance": "u002", "answer_end_utterance": "u002", "question_text": "Q2"},
        ],
        "selects": [], "themes": [], "theme_assignments": {},
    }
    rows = review.build_rows(_TRANSCRIPT, analysis, {}, segment_order=["s002", "s001"])
    assert [r["segment_id"] for r in rows] == ["s002", "s001"]


def test_render_html_contains_checkbox_and_copy_button():
    trims = _trims()
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, trims)
    doc = review.render_html("acme_doc", rows)
    assert 'class="seg-toggle"' in doc
    assert "Copy excluded IDs" in doc
    assert "data-seg-id=\"s001\"" in doc
    assert "<!doctype html>" in doc.lower()


def test_render_html_includes_narrative_rationale_when_given():
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, {})
    doc = review.render_html("acme_doc", rows, mode="narrative", rationale="Opens on the big deal, then resolves.")
    assert "Structure rationale" in doc
    assert "Opens on the big deal" in doc


def test_render_html_omits_rationale_block_when_not_given():
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, {})
    doc = review.render_html("acme_doc", rows)
    assert '<div class="rationale">' not in doc


def test_render_html_excluded_segment_unchecked():
    rows = review.build_rows(_TRANSCRIPT, _ANALYSIS, {})
    doc = review.render_html("acme_doc", rows, excluded={"s001"})
    # The checkbox for an excluded segment should not carry `checked`.
    segment_block = doc.split('data-seg-id="s001"')[1].split("</label>")[0]
    assert "checked" not in segment_block


def test_write_review_html_creates_file(tmp_path):
    trims = _trims()
    out = review.write_review_html("acme_doc", _TRANSCRIPT, _ANALYSIS, trims, tmp_path / "review.html")
    assert out.exists()
    assert "acme_doc" in out.read_text()
