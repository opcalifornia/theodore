import json

from click.testing import CliRunner
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeTimelineItem

from theodore import registry
from theodore.cli import cli


def _setup_subject_with_transcript(tmp_path, monkeypatch, transcript):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")
    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    subj_dir = registry.subject_dir(project_dir, "haylee")
    subj_dir.mkdir(parents=True, exist_ok=True)
    (subj_dir / "transcript.json").write_text(json.dumps(transcript))
    return subj_dir


class FakeHandles:
    def __init__(self, timeline):
        self.timeline = timeline


def test_timeline_status_reports_matches_against_current_timeline(monkeypatch, tmp_path):
    _setup_subject_with_transcript(tmp_path, monkeypatch, {
        "source_file": "/media/DJI_17.WAV",
        "sources": [{"path": "/media/DJI_17.WAV"}, {"path": "/media/DJI_18.WAV"}],
        "utterances": [],
    })

    timeline = FakeAssemblyTimeline("Interview Sync v1")
    lav = FakeMediaPoolItem(path="/media/DJI_17.WAV", name="DJI_17.WAV")
    timeline.tracks["audio"][1].append(FakeTimelineItem(lav, start=1000, duration=500))
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline))

    runner = CliRunner()
    result = runner.invoke(cli, ["timeline-status", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "Interview Sync v1" in result.output
    assert "DJI_17.WAV" in result.output
    assert "1 matching clip" in result.output


def test_timeline_status_falls_back_to_source_file_for_old_transcripts(monkeypatch, tmp_path):
    # A transcript.json saved before "sources" existed.
    _setup_subject_with_transcript(tmp_path, monkeypatch, {
        "source_file": "/media/only.wav",
        "utterances": [],
    })

    timeline = FakeAssemblyTimeline("Edit")
    clip = FakeMediaPoolItem(path="/media/only.wav")
    timeline.tracks["audio"][1].append(FakeTimelineItem(clip, start=0, duration=100))
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline))

    runner = CliRunner()
    result = runner.invoke(cli, ["timeline-status", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "1 matching clip" in result.output


def test_timeline_status_requires_a_transcript_first(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    from theodore import config
    project_dir = config.project_dir("docproj")
    project = registry.load_project(project_dir, project_name="docproj")
    registry.register_subject(project, "haylee")
    registry.save_project(project_dir, project)

    runner = CliRunner()
    result = runner.invoke(cli, ["timeline-status", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code != 0
    assert "run `theodore transcribe`" in result.output
