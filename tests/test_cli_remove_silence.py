import json

from click.testing import CliRunner
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeProject, FakeTimelineItem

from theodore import registry
from theodore.cli import cli


class FakeHandles:
    def __init__(self, timeline, project):
        self.timeline = timeline
        self.project = project


TRANSCRIPT = {
    "source_file": "/media/lav.wav",
    "sources": [{"path": "/media/lav.wav"}],
    "fps": "0/1",
    "start_timecode": "00:00:00:00",
    "speakers": {"0": "Haylee", "1": "Interviewer"},
    "utterances": [
        {
            "id": "u001", "speaker": "0", "start": 0.0, "end": 20.0,
            "source_index": 0, "source_start": 0.0, "source_end": 20.0,
            "text": "It was a long pause then a lot to say.",
            "words": [
                {"word": "It", "start": 0.0, "end": 0.3},
                {"word": "was", "start": 0.3, "end": 0.6},
                # A 5s dead-air gap lands here (0.6s -> 5.6s).
                {"word": "a", "start": 5.6, "end": 5.8},
                {"word": "long", "start": 5.8, "end": 6.2},
                {"word": "pause", "start": 6.2, "end": 6.6},
                {"word": "then", "start": 6.6, "end": 7.0},
                {"word": "a", "start": 7.0, "end": 7.2},
                {"word": "lot", "start": 7.2, "end": 7.5},
                {"word": "to", "start": 7.5, "end": 7.7},
                {"word": "say", "start": 7.7, "end": 8.0},
            ],
        },
    ],
}

ANALYSIS = {
    "segments": [
        {"id": "haylee.q01", "answer_start_utterance": "u001", "answer_end_utterance": "u001",
         "question_text": "Tell me.", "question_label": "Q1", "answer_summary": "..."},
    ],
    "selects": [
        {"segment_id": "haylee.q01", "strength": 0.8,
         "clean_start_utterance": "u001", "clean_end_utterance": "u001",
         "best_line": "a long pause", "issues": []},
    ],
}


def _setup_subject(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")
    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    subj_dir = registry.subject_dir(project_dir, "haylee")
    subj_dir.mkdir(parents=True, exist_ok=True)
    (subj_dir / "transcript.json").write_text(json.dumps(TRANSCRIPT))
    (subj_dir / "analysis.json").write_text(json.dumps(ANALYSIS))
    return subj_dir


def _timeline_with_lav_clip():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    item = FakeTimelineItem(lav, start=0, duration=192, source_start=0)  # 8s @ 24fps
    timeline.tracks["audio"][1].append(item)
    project = FakeProject(timeline_fps="24")
    project.timelines.append(timeline)
    project.current_timeline = timeline
    return timeline, project


def test_remove_silence_dry_run_reports_ranges_without_building(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "remove-silence", "--project", "docproj", "--subject", "haylee", "--dry-run",
    ])

    assert result.exit_code == 0, result.output
    assert "dead-air range" in result.output
    assert "dry run" in result.output
    # Nothing built: the original timeline's only clip is untouched.
    assert len(timeline.tracks["audio"][1]) == 1


def test_remove_silence_applies_the_cut_by_default(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "remove-silence", "--project", "docproj", "--subject", "haylee",
    ])

    assert result.exit_code == 0, result.output
    assert "Built '" in result.output
    assert "untouched" in result.output
    # The original is genuinely unmodified -- still exactly the one 8s clip.
    assert len(timeline.tracks["audio"][1]) == 1
    assert timeline.tracks["audio"][1][0].GetDuration() == 192


def test_remove_silence_with_nothing_above_threshold_says_so(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "remove-silence", "--project", "docproj", "--subject", "haylee",
        "--silence-threshold", "100",
    ])

    assert result.exit_code == 0, result.output
    assert "nothing to cut" in result.output


def test_remove_silence_auto_detects_subject_from_the_open_timeline(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["remove-silence", "--project", "docproj"])

    assert result.exit_code == 0, result.output
    assert "Auto-detected subject 'haylee'" in result.output
    assert "Built '" in result.output


def test_remove_silence_fails_clearly_when_subject_cannot_be_auto_detected(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    empty_timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    project = FakeProject(timeline_fps="24")
    project.timelines.append(empty_timeline)
    project.current_timeline = empty_timeline
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(empty_timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["remove-silence", "--project", "docproj"])

    assert result.exit_code != 0
    assert "--subject" in result.output


def test_remove_silence_warns_when_transcript_source_not_on_timeline(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    empty_timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    project = FakeProject(timeline_fps="24")
    project.timelines.append(empty_timeline)
    project.current_timeline = empty_timeline
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(empty_timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "remove-silence", "--project", "docproj", "--subject", "haylee",
    ])

    assert result.exit_code != 0
    assert "timeline-status" in result.output
