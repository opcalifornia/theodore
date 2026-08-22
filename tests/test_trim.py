import json

from theodore.assembly import trim


def _w(word, start, end, conf=0.99):
    return {"word": word, "start": start, "end": end, "confidence": conf}


_TRANSCRIPT = {
    "speakers": {"0": "Marcus", "1": "Interviewer"},
    "utterances": [
        {
            "id": "u001", "speaker": "1", "start": 0.0, "end": 1.0, "text": "Tell me about it.",
            "words": [_w("Tell", 0.0, 0.2), _w("me", 0.2, 0.4), _w("about", 0.4, 0.7), _w("it.", 0.7, 1.0)],
        },
        {
            "id": "u002", "speaker": "0", "start": 1.5, "end": 6.0,
            "text": "Um, you know, it was great. There was a long pause here. And that's it, like.",
            "words": [
                _w("Um,", 1.5, 1.7), _w("you", 1.7, 1.8), _w("know,", 1.8, 1.9),
                _w("it", 1.9, 2.0), _w("was", 2.0, 2.1), _w("great.", 2.1, 2.4),
                # dead air gap 2.4 -> 4.5 (2.1s, over the 1.2s default threshold)
                _w("There", 4.5, 4.6), _w("was", 4.6, 4.7), _w("a", 4.7, 4.75),
                _w("long", 4.75, 4.9), _w("pause", 4.9, 5.1), _w("here.", 5.1, 5.4),
                _w("And", 5.4, 5.5), _w("that's", 5.5, 5.7), _w("it,", 5.7, 5.8),
                _w("like.", 5.8, 6.0),
            ],
        },
        {
            "id": "u003", "speaker": "1", "start": 3.0, "end": 3.3, "text": "mm-hmm",
            "words": [_w("mm-hmm", 3.0, 3.3)],
        },
    ],
}

_SEGMENT = {
    "id": "s001", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
    "question_text": "Tell me about it.", "question_label": "The story",
}

_SELECT = {
    "segment_id": "s001", "strength": 0.7,
    "clean_start_utterance": "u002", "clean_end_utterance": "u002",
}


def test_leading_and_trailing_filler_removed():
    result = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT)
    assert result["leading_filler_removed"] == ["um", "you know"]
    assert result["trailing_filler_removed"] == ["like"]
    # trimmed_start should be at "it" (1.9), trimmed_end at "it," (5.8) -- "like." stripped.
    assert result["trimmed_start"] == 1.9
    assert result["trimmed_end"] == 5.8
    # original span is untouched (starts at "Um," / ends at "like.")
    assert result["original_start"] == 1.5
    assert result["original_end"] == 6.0


def test_dead_air_detected_above_threshold():
    result = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT, silence_threshold=1.2)
    assert [2.4, 4.5] in result["dead_air_cuts"]


def test_dead_air_not_flagged_below_threshold():
    result = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT, silence_threshold=5.0)
    assert result["dead_air_cuts"] == []


def test_interior_filler_only_cut_when_aggressive():
    conservative = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT, aggressive=False)
    assert conservative["interior_filler_cuts"] == []

    aggressive = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT, aggressive=True)
    assert aggressive["interior_filler_cuts"] == []  # no interior filler words in this fixture beyond boundaries


def test_interior_filler_word_cut_with_aggressive_flag():
    segment = {"id": "s003", "answer_start_utterance": "u020", "answer_end_utterance": "u020"}
    transcript = {
        "speakers": {"0": "Marcus"},
        "utterances": [{
            "id": "u020", "speaker": "0", "start": 0.0, "end": 2.0, "text": "It was, um, a big deal.",
            "words": [
                _w("It", 0.0, 0.2), _w("was,", 0.2, 0.4), _w("um,", 0.4, 0.6),
                _w("a", 0.6, 0.7), _w("big", 0.7, 0.9), _w("deal.", 0.9, 1.2),
            ],
        }],
    }
    conservative = trim.compute_trim(segment, None, transcript, aggressive=False)
    assert conservative["interior_filler_cuts"] == []
    assert conservative["cuts"] == []

    aggressive = trim.compute_trim(segment, None, transcript, aggressive=True)
    assert aggressive["interior_filler_cuts"] == [[0.4, 0.6]]
    assert aggressive["cuts"] == [[0.4, 0.6]]


def test_backchannel_overlap_flagged_not_removed():
    result = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT)
    assert len(result["backchannel_overlaps"]) == 1
    overlap = result["backchannel_overlaps"][0]
    assert overlap["speaker"] == "1"
    assert overlap["text"] == "mm-hmm"
    # It's flagged, not subtracted from cuts.
    assert [3.0, 3.3] not in result["cuts"]


def test_cuts_is_merged_dead_air_plus_interior_filler():
    result = trim.compute_trim(_SEGMENT, _SELECT, _TRANSCRIPT, silence_threshold=1.2)
    assert result["cuts"] == result["dead_air_cuts"]


def test_all_filler_segment_keeps_original_instead_of_collapsing():
    segment = {"id": "s002", "answer_start_utterance": "u010", "answer_end_utterance": "u010"}
    transcript = {
        "speakers": {"0": "Marcus"},
        "utterances": [{
            "id": "u010", "speaker": "0", "start": 0.0, "end": 1.0, "text": "Um, like, you know.",
            "words": [_w("Um,", 0.0, 0.3), _w("like,", 0.3, 0.6), _w("you", 0.6, 0.8), _w("know.", 0.8, 1.0)],
        }],
    }
    result = trim.compute_trim(segment, None, transcript)
    assert result["trimmed_start"] == result["original_start"] == 0.0
    assert result["trimmed_end"] == result["original_end"] == 1.0
    assert result["leading_filler_removed"] == []


def test_compute_trim_returns_none_for_unresolvable_utterance():
    segment = {"id": "s999", "answer_start_utterance": "u999", "answer_end_utterance": "u999"}
    assert trim.compute_trim(segment, None, _TRANSCRIPT) is None


def test_compute_trims_skips_unresolvable_and_keys_by_segment_id():
    analysis = {
        "segments": [_SEGMENT, {"id": "s999", "answer_start_utterance": "u999", "answer_end_utterance": "u999"}],
        "selects": [_SELECT],
    }
    trims = trim.compute_trims(analysis, _TRANSCRIPT)
    assert set(trims.keys()) == {"s001"}


def test_save_and_load_trims_round_trip(tmp_path):
    trims = {"s001": {"segment_id": "s001", "trimmed_start": 1.9, "trimmed_end": 5.8, "cuts": []}}
    trim.save_trims(trims, tmp_path)
    loaded = trim.load_trims(tmp_path)
    assert loaded == trims


def test_load_trims_returns_empty_dict_when_missing(tmp_path):
    assert trim.load_trims(tmp_path) == {}
