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
