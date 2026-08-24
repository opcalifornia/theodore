"""End-to-end CLI coverage for the multi-file delivery fix: `theodore
delivery` used to grab a single wav file (sorted().[0]) regardless of how
many source files a subject actually had, silently scoring every
utterance past the first against the wrong audio. This drives the real
`delivery` command against a subject with TWO real audio files and checks
segments from BOTH actually get real prosody features -- not just that
the command exits 0.
"""
import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from theodore import registry
from theodore.cli import cli

FIXTURES = Path(__file__).parent / "fixtures"


def _setup_two_file_subject(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")
    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    subj_dir = registry.subject_dir(project_dir, "haylee")
    (subj_dir / "audio").mkdir(parents=True, exist_ok=True)

    hash_a, hash_b = "hashaaa", "hashbbb"
    shutil.copy(FIXTURES / "speech_a.wav", subj_dir / "audio" / f"{hash_a}.wav")
    shutil.copy(FIXTURES / "speech_b.wav", subj_dir / "audio" / f"{hash_b}.wav")

    media = [
        {"path": "/media/DJI_01.WAV", "fps": "0/1", "duration_seconds": 8.0,
         "start_timecode": "00:00:00:00", "has_video": False, "has_audio": True, "audio_hash": hash_a},
        {"path": "/media/DJI_02.WAV", "fps": "0/1", "duration_seconds": 8.0,
         "start_timecode": "00:00:00:00", "has_video": False, "has_audio": True, "audio_hash": hash_b},
    ]
    (subj_dir / "media.json").write_text(json.dumps(media))

    import parselmouth
    a_duration = parselmouth.Sound(str(FIXTURES / "speech_a.wav")).duration
    b_duration = parselmouth.Sound(str(FIXTURES / "speech_b.wav")).duration

    transcript = {
        "source_file": "/media/DJI_01.WAV",
        "sources": [
            {"path": "/media/DJI_01.WAV", "fps": "0/1", "start_timecode": "00:00:00:00", "duration_seconds": a_duration},
            {"path": "/media/DJI_02.WAV", "fps": "0/1", "start_timecode": "00:00:00:00", "duration_seconds": b_duration},
        ],
        "fps": "0/1",
        "start_timecode": "00:00:00:00",
        "speakers": {"0": "Haylee", "1": "Interviewer"},
        "utterances": [
            {
                "id": "u001", "speaker": "0", "start": 0.0, "end": a_duration,
                "source_index": 0, "source_start": 0.0, "source_end": a_duration,
                "text": "answer one",
                "words": [{"word": "It", "start": 0.0, "end": 0.3}, {"word": "was.", "start": 0.3, "end": a_duration}],
            },
            {
                "id": "u002", "speaker": "0",
                "start": a_duration + 0.2, "end": a_duration + 0.2 + b_duration,
                "source_index": 1, "source_start": 0.0, "source_end": b_duration,
                "text": "answer two",
                "words": [{"word": "Then", "start": a_duration + 0.2, "end": a_duration + 0.5}],
            },
        ],
    }
    (subj_dir / "transcript.json").write_text(json.dumps(transcript))

    analysis = {
        "segments": [
            {"id": "haylee.q01", "answer_start_utterance": "u001", "answer_end_utterance": "u001",
             "question_text": "Q1?", "question_label": "Q1", "answer_summary": "..."},
            {"id": "haylee.q02", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
             "question_text": "Q2?", "question_label": "Q2", "answer_summary": "..."},
        ],
        "selects": [],
    }
    (subj_dir / "analysis.json").write_text(json.dumps(analysis))
    return subj_dir


def test_delivery_scores_segments_from_every_source_file(tmp_path, monkeypatch):
    _setup_two_file_subject(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["delivery", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "2 audio file(s)" in result.output

    delivery_path = tmp_path / "data" / "docproj" / "subjects" / "haylee" / "delivery.json"
    delivery = json.loads(delivery_path.read_text())

    # Both segments -- one from EACH source file -- must have real,
    # non-null acoustic features. Before the fix, only segments from
    # whichever single file _find_subject_wav happened to pick would.
    assert delivery["segments"]["haylee.q01"]["features"]["pitch_mean_hz"] is not None
    assert delivery["segments"]["haylee.q02"]["features"]["pitch_mean_hz"] is not None
