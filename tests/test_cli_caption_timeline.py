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
            "id": "u001", "speaker": "0", "start": 10.0, "end": 12.0,
            "source_index": 0, "source_start": 10.0, "source_end": 12.0,
            "text": "First answer here.",
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
        {"id": "haylee.q01", "answer_start_utterance": "u001", "answer_end_utterance": "u001",
         "question_text": "Tell me.", "question_label": "Q1", "answer_summary": "..."},
    ],
    "selects": [
        {"segment_id": "haylee.q01", "strength": 0.8,
         "clean_start_utterance": "u001", "clean_end_utterance": "u001",
         "best_line": "First answer here.", "issues": []},
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
    item = FakeTimelineItem(lav, start=0, duration=480, source_start=0)  # 20s @ 24fps
    timeline.tracks["audio"][1].append(item)
    project = FakeProject(timeline_fps="24")
    project.timelines.append(timeline)
    project.current_timeline = timeline
    return timeline, project


def test_caption_timeline_writes_srt_and_vtt(tmp_path, monkeypatch):
    subj_dir = _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "caption-timeline", "--project", "docproj", "--subject", "haylee",
    ])

    assert result.exit_code == 0, result.output
    assert "Built 1 caption cue" in result.output
    assert (subj_dir / "captions.srt").exists()
    assert (subj_dir / "captions.vtt").exists()
    assert "First answer here." in (subj_dir / "captions.srt").read_text()


def test_caption_timeline_auto_detects_subject(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["caption-timeline", "--project", "docproj"])

    assert result.exit_code == 0, result.output
    assert "Auto-detected subject 'haylee'" in result.output


def test_caption_timeline_warns_when_source_not_on_timeline(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    empty_timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    project = FakeProject(timeline_fps="24")
    project.timelines.append(empty_timeline)
    project.current_timeline = empty_timeline
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(empty_timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["caption-timeline", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "WARNING" in result.output
    assert "Built 0 caption cue" in result.output


def test_caption_timeline_import_to_resolve_needs_srt(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "caption-timeline", "--project", "docproj", "--subject", "haylee",
        "--no-srt", "--import-to-resolve",
    ])

    assert result.exit_code != 0
    assert "needs the SRT" in result.output


def test_caption_timeline_import_to_resolve_reports_success(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_with_lav_clip()
    timeline.ImportIntoTimeline = lambda path, opts: True
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "caption-timeline", "--project", "docproj", "--subject", "haylee", "--import-to-resolve",
    ])

    assert result.exit_code == 0, result.output
    assert "Imported the subtitle track" in result.output
