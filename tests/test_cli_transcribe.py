import json

from click.testing import CliRunner

from theodore import registry
from theodore.cli import cli
from theodore.transcribe import deepgram as dg


def _single_utterance_raw(speaker, text, start, end):
    words = [{"word": w, "punctuated_word": w, "start": start, "end": end, "confidence": 0.9} for w in text.split()]
    return {
        "results": {
            "channels": [{"alternatives": [{"transcript": text, "words": words}]}],
            "utterances": [{"speaker": speaker, "start": start, "end": end, "transcript": text, "words": words}],
        },
    }


def _setup_subject_with_audio(tmp_path, monkeypatch, files):
    """files: list of (original_name, duration_seconds, raw_response). Each
    gets a real (empty-content, since transcribe_audio is mocked) wav file
    named by a fake per-file hash, plus a matching media.json entry."""
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")

    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    subj_dir = registry.subject_dir(project_dir, "haylee")
    audio_dir = subj_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    media_list = []
    raws_by_hash = {}
    for i, (name, duration, raw) in enumerate(files):
        fake_hash = f"hash{i}"
        (audio_dir / f"{fake_hash}.wav").write_bytes(b"")
        media_list.append({
            "path": f"/footage/{name}", "fps": "0/1", "duration_seconds": duration,
            "start_timecode": "00:00:00:00", "has_video": False, "has_audio": True,
            "audio_hash": fake_hash,
        })
        raws_by_hash[fake_hash] = raw
    (subj_dir / "media.json").write_text(json.dumps(media_list))

    return subj_dir, raws_by_hash


def test_transcribe_processes_every_file_in_chronological_order(monkeypatch, tmp_path):
    # Deliberately out of chronological order in the files list / hash
    # naming, so a bug that just sorts by hash filename would be caught.
    files = [
        ("DJI_25_20260731_113601.WAV", 20.0, _single_utterance_raw(0, "second take", 0.0, 2.0)),
        ("DJI_17_20260731_112649.WAV", 100.0, _single_utterance_raw(0, "first take", 0.0, 2.0)),
    ]
    subj_dir, raws_by_hash = _setup_subject_with_audio(tmp_path, monkeypatch, files)
    (subj_dir / "speakers.json").write_text(json.dumps({"0": "Haylee"}))

    def fake_transcribe_audio(wav_path, project_dir, force=False):
        return raws_by_hash[wav_path.stem]
    monkeypatch.setattr(dg, "transcribe_audio", fake_transcribe_audio)

    runner = CliRunner()
    result = runner.invoke(cli, ["transcribe", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "DJI_17_20260731_112649.WAV" in result.output
    assert "DJI_25_20260731_113601.WAV" in result.output
    # DJI_17 (chronologically first) must be transcribed before DJI_25,
    # regardless of hash-name or list order.
    assert result.output.index("DJI_17_20260731_112649.WAV") < result.output.index("DJI_25_20260731_113601.WAV")

    transcript = json.loads((subj_dir / "transcript.json").read_text())
    assert [s["path"] for s in transcript["sources"]] == [
        "/footage/DJI_17_20260731_112649.WAV", "/footage/DJI_25_20260731_113601.WAV",
    ]
    assert len(transcript["utterances"]) == 2
    assert transcript["utterances"][0]["text"] == "first take"
    assert transcript["utterances"][1]["text"] == "second take"
    # Second file's utterance offset by the first file's full duration (100s).
    assert transcript["utterances"][1]["start"] == 100.0


def test_transcribe_handles_real_world_scrambled_ingest_order(monkeypatch, tmp_path):
    # The exact filenames and out-of-order sequence a real DJI Mic 2 kit
    # produced this session: ingest discovers files in directory-listing
    # order (24, 25, 26, ..., 37, then 17, 18), not chronological order.
    # Durations are the real probed values.
    real_files_in_ingest_order = [
        ("DJI_24_20260731_112701.WAV", 523.7),
        ("DJI_25_20260731_113601.WAV", 15.2),
        ("DJI_26_20260731_114038.WAV", 596.5),
        ("DJI_17_20260731_112649.WAV", 535.0),
        ("DJI_18_20260731_114032.WAV", 602.5),
    ]
    files = [
        (name, duration, _single_utterance_raw(0, name.split("_")[1], 0.0, 1.0))
        for name, duration in real_files_in_ingest_order
    ]
    subj_dir, raws_by_hash = _setup_subject_with_audio(tmp_path, monkeypatch, files)
    (subj_dir / "speakers.json").write_text(json.dumps({"0": "Haylee"}))
    monkeypatch.setattr(dg, "transcribe_audio", lambda wav_path, project_dir, force=False: raws_by_hash[wav_path.stem])

    runner = CliRunner()
    result = runner.invoke(cli, ["transcribe", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    transcript = json.loads((subj_dir / "transcript.json").read_text())
    # Must come out in real chronological order (17, 18, 24, 25, 26), not
    # ingest/directory-listing order, despite string-sorting "17" < "24"
    # working here only because these happen to be same-width numbers --
    # the real assertion is against the exact filenames, not just a count.
    assert [s["path"].split("/")[-1] for s in transcript["sources"]] == [
        "DJI_17_20260731_112649.WAV",
        "DJI_18_20260731_114032.WAV",
        "DJI_24_20260731_112701.WAV",
        "DJI_25_20260731_113601.WAV",
        "DJI_26_20260731_114038.WAV",
    ]
    # Each utterance's text names which file it came from -- confirms the
    # right transcript content landed at the right position, not just the
    # right file list.
    assert [u["text"] for u in transcript["utterances"]] == ["17", "18", "24", "25", "26"]


def test_transcribe_single_file_still_works_unchanged(monkeypatch, tmp_path):
    files = [("only.wav", 10.0, _single_utterance_raw(0, "hello there", 0.0, 1.0))]
    subj_dir, raws_by_hash = _setup_subject_with_audio(tmp_path, monkeypatch, files)
    (subj_dir / "speakers.json").write_text(json.dumps({"0": "Haylee"}))

    monkeypatch.setattr(dg, "transcribe_audio", lambda wav_path, project_dir, force=False: raws_by_hash[wav_path.stem])

    runner = CliRunner()
    result = runner.invoke(cli, ["transcribe", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    transcript = json.loads((subj_dir / "transcript.json").read_text())
    assert len(transcript["utterances"]) == 1
    assert transcript["utterances"][0]["start"] == 0.0
