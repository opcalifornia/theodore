from theodore.transcribe import deepgram as dg


def _fake_raw_response():
    return {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "Hello there General Kenobi.",
                    "words": [
                        {"word": "hello", "punctuated_word": "Hello", "start": 0.0, "end": 0.3, "confidence": 0.99, "speaker": 0},
                        {"word": "there", "punctuated_word": "there", "start": 0.3, "end": 0.6, "confidence": 0.98, "speaker": 0},
                        {"word": "general", "punctuated_word": "General", "start": 0.7, "end": 1.0, "confidence": 0.97, "speaker": 1},
                        {"word": "kenobi", "punctuated_word": "Kenobi.", "start": 1.0, "end": 1.5, "confidence": 0.96, "speaker": 1},
                    ],
                }],
            }],
            "utterances": [
                {
                    "speaker": 0, "start": 0.0, "end": 0.6, "transcript": "Hello there.",
                    "words": [
                        {"word": "hello", "punctuated_word": "Hello", "start": 0.0, "end": 0.3, "confidence": 0.99},
                        {"word": "there", "punctuated_word": "there.", "start": 0.3, "end": 0.6, "confidence": 0.98},
                    ],
                },
                {
                    "speaker": 1, "start": 0.7, "end": 1.5, "transcript": "General Kenobi.",
                    "words": [
                        {"word": "general", "punctuated_word": "General", "start": 0.7, "end": 1.0, "confidence": 0.97},
                        {"word": "kenobi", "punctuated_word": "Kenobi.", "start": 1.0, "end": 1.5, "confidence": 0.96},
                    ],
                },
            ],
        },
    }


def test_normalize_produces_theodore_schema():
    raw = _fake_raw_response()
    transcript = dg.normalize(raw, source_file="interview.wav", fps="24000/1001", start_timecode="01:00:00:00")

    assert transcript["source_file"] == "interview.wav"
    assert transcript["fps"] == "24000/1001"
    assert transcript["start_timecode"] == "01:00:00:00"
    assert set(transcript["speakers"].keys()) == {"0", "1"}

    assert len(transcript["utterances"]) == 2
    u0 = transcript["utterances"][0]
    assert u0["id"] == "u001"
    assert u0["speaker"] == "0"
    assert u0["text"] == "Hello there."
    assert u0["start"] == 0.0 and u0["end"] == 0.6
    assert u0["words"][0] == {"word": "Hello", "start": 0.0, "end": 0.3, "confidence": 0.99}

    u1 = transcript["utterances"][1]
    assert u1["id"] == "u002"
    assert u1["speaker"] == "1"


def test_normalize_falls_back_to_word_level_speaker_runs_without_utterances():
    raw = _fake_raw_response()
    del raw["results"]["utterances"]

    transcript = dg.normalize(raw, source_file="x.wav", fps="24", start_timecode="00:00:00:00")

    # Fallback path builds one utterance per contiguous same-speaker word run.
    assert len(transcript["utterances"]) == 2
    assert transcript["utterances"][0]["speaker"] == "0"
    assert transcript["utterances"][0]["text"] == "Hello there"
    assert transcript["utterances"][1]["speaker"] == "1"
    assert transcript["utterances"][1]["text"] == "General Kenobi."
    assert set(transcript["speakers"].keys()) == {"0", "1"}


def test_apply_speaker_names():
    raw = _fake_raw_response()
    transcript = dg.normalize(raw, source_file="x.wav", fps="24", start_timecode="00:00:00:00")
    transcript = dg.apply_speaker_names(transcript, {"0": "Marcus (subject)", "1": "Interviewer"})
    assert transcript["speakers"]["0"] == "Marcus (subject)"
    assert transcript["speakers"]["1"] == "Interviewer"


def test_apply_speaker_names_leaves_unmapped_speakers_alone():
    raw = _fake_raw_response()
    transcript = dg.normalize(raw, source_file="x.wav", fps="24", start_timecode="00:00:00:00")
    transcript = dg.apply_speaker_names(transcript, {"0": "Marcus (subject)"})
    assert transcript["speakers"]["0"] == "Marcus (subject)"
    assert transcript["speakers"]["1"] == "Speaker 1"


def test_save_and_reload_round_trips(tmp_path):
    raw = _fake_raw_response()
    transcript = dg.normalize(raw, source_file="x.wav", fps="24", start_timecode="00:00:00:00")
    path = dg.save_normalized(transcript, tmp_path)

    import json
    reloaded = json.loads(path.read_text())
    assert reloaded == transcript


# --------------------------------------------------------------------------
# merge_normalized_transcripts -- combining multiple separate audio files
# (e.g. an external recorder stopped and restarted through a session) into
# one continuous transcript.
# --------------------------------------------------------------------------

def _single_utterance_raw(speaker: int, text: str, start: float, end: float):
    """A minimal raw Deepgram response with one utterance, for building
    merge test fixtures without the full multi-utterance fixture above."""
    words = [{"word": w, "punctuated_word": w, "start": start, "end": end, "confidence": 0.9} for w in text.split()]
    return {
        "results": {
            "channels": [{"alternatives": [{"transcript": text, "words": words}]}],
            "utterances": [{"speaker": speaker, "start": start, "end": end, "transcript": text, "words": words}],
        },
    }


def test_single_entry_merge_is_the_unmodified_transcript_plus_sources():
    raw = _fake_raw_response()
    transcript = dg.normalize(raw, source_file="only.wav", fps="24000/1001", start_timecode="01:00:00:00")

    merged = dg.merge_normalized_transcripts([(transcript, 90.0)])

    assert merged["source_file"] == "only.wav"
    assert merged["fps"] == "24000/1001"
    assert merged["sources"] == [{
        "path": "only.wav", "fps": "24000/1001",
        "start_timecode": "01:00:00:00", "duration_seconds": 90.0,
    }]
    # Utterance content/order/ids unchanged; only the new tracking fields added.
    assert [u["id"] for u in merged["utterances"]] == ["u001", "u002"]
    assert merged["utterances"][0]["start"] == 0.0
    assert merged["utterances"][0]["source_index"] == 0
    assert merged["utterances"][0]["source_start"] == 0.0


def test_multi_file_merge_offsets_utterances_by_cumulative_duration():
    t1 = dg.normalize(
        _single_utterance_raw(0, "First file question", 5.0, 8.0),
        source_file="take1.wav", fps="0/1", start_timecode="00:00:00:00",
    )
    t2 = dg.normalize(
        _single_utterance_raw(0, "Second file answer", 2.0, 6.0),
        source_file="take2.wav", fps="0/1", start_timecode="00:00:00:00",
    )

    merged = dg.merge_normalized_transcripts([(t1, 100.0), (t2, 50.0)])

    assert len(merged["utterances"]) == 2
    first, second = merged["utterances"]
    # File 1's utterance is untouched (offset 0).
    assert first["start"] == 5.0 and first["end"] == 8.0
    assert first["source_index"] == 0
    assert first["source_start"] == 5.0 and first["source_end"] == 8.0
    # File 2's utterance is shifted by file 1's full duration (100s), not
    # its last utterance's end -- trailing silence after the last word
    # must not shrink the offset.
    assert second["start"] == 102.0 and second["end"] == 106.0
    assert second["source_index"] == 1
    assert second["source_start"] == 2.0 and second["source_end"] == 6.0


def test_multi_file_merge_offsets_word_level_timestamps_too():
    t1 = dg.normalize(_single_utterance_raw(0, "hello", 0.0, 1.0), "a.wav", "0/1", "00:00:00:00")
    t2 = dg.normalize(_single_utterance_raw(0, "world", 0.0, 1.0), "b.wav", "0/1", "00:00:00:00")

    merged = dg.merge_normalized_transcripts([(t1, 10.0), (t2, 10.0)])

    word = merged["utterances"][1]["words"][0]
    assert word["start"] == 10.0
    assert word["end"] == 11.0


def test_multi_file_merge_renumbers_utterance_ids_sequentially():
    t1 = dg.normalize(_fake_raw_response(), "a.wav", "0/1", "00:00:00:00")  # 2 utterances
    t2 = dg.normalize(_fake_raw_response(), "b.wav", "0/1", "00:00:00:00")  # 2 more

    merged = dg.merge_normalized_transcripts([(t1, 10.0), (t2, 10.0)])

    assert [u["id"] for u in merged["utterances"]] == ["u001", "u002", "u003", "u004"]


def test_multi_file_merge_assumes_consistent_speaker_ids_across_files():
    # Same raw speaker "0" in both files -> treated as the same real person,
    # merged into one speakers entry, not duplicated.
    t1 = dg.normalize(_single_utterance_raw(0, "hi", 0.0, 1.0), "a.wav", "0/1", "00:00:00:00")
    t2 = dg.normalize(_single_utterance_raw(0, "bye", 0.0, 1.0), "b.wav", "0/1", "00:00:00:00")

    merged = dg.merge_normalized_transcripts([(t1, 10.0), (t2, 10.0)])

    assert set(merged["speakers"].keys()) == {"0"}


def test_merge_lists_every_source_in_order():
    t1 = dg.normalize(_single_utterance_raw(0, "a", 0.0, 1.0), "take1.wav", "24", "00:00:00:00")
    t2 = dg.normalize(_single_utterance_raw(0, "b", 0.0, 1.0), "take2.wav", "24", "00:00:00:00")
    t3 = dg.normalize(_single_utterance_raw(0, "c", 0.0, 1.0), "take3.wav", "24", "00:00:00:00")

    merged = dg.merge_normalized_transcripts([(t1, 10.0), (t2, 20.0), (t3, 30.0)])

    assert [s["path"] for s in merged["sources"]] == ["take1.wav", "take2.wav", "take3.wav"]
    assert [s["duration_seconds"] for s in merged["sources"]] == [10.0, 20.0, 30.0]


def test_merge_requires_at_least_one_entry():
    import pytest
    with pytest.raises(ValueError):
        dg.merge_normalized_transcripts([])
