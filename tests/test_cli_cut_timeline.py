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
        {"id": "u001", "speaker": "1", "start": 0.0, "end": 8.0,
         "source_index": 0, "source_start": 0.0, "source_end": 8.0},
        {"id": "u002", "speaker": "0", "start": 10.0, "end": 20.0,
         "source_index": 0, "source_start": 10.0, "source_end": 20.0},
        {"id": "u003", "speaker": "1", "start": 22.0, "end": 25.0,
         "source_index": 0, "source_start": 22.0, "source_end": 25.0},
        {"id": "u004", "speaker": "0", "start": 27.0, "end": 37.0,
         "source_index": 0, "source_start": 27.0, "source_end": 37.0},
    ],
}

ANALYSIS = {
    "segments": [
        {"id": "haylee.q01", "answer_start_utterance": "u002", "answer_end_utterance": "u002",
         "question_text": "Q1?", "question_label": "Q1", "answer_summary": "..."},
        {"id": "haylee.q02", "answer_start_utterance": "u004", "answer_end_utterance": "u004",
         "question_text": "Q2?", "question_label": "Q2", "answer_summary": "..."},
    ],
    "selects": [
        {"segment_id": "haylee.q01", "strength": 0.7,
         "clean_start_utterance": "u002", "clean_end_utterance": "u002", "issues": []},
        {"segment_id": "haylee.q02", "strength": 0.4,
         "clean_start_utterance": "u004", "clean_end_utterance": "u004", "issues": []},
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


def _timeline_covering_the_whole_interview():
    timeline = FakeAssemblyTimeline("Edit", start_frame=0, fps_str="24")
    lav = FakeMediaPoolItem(path="/media/lav.wav", frames=100000, start=0)
    item = FakeTimelineItem(lav, start=0, duration=37 * 24, source_start=0)
    timeline.tracks["audio"][1].append(item)
    project = FakeProject(timeline_fps="24")
    project.timelines.append(timeline)
    project.current_timeline = timeline
    return timeline, project


def test_cut_timeline_defaults_to_preview_only(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_covering_the_whole_interview()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["cut-timeline", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "2 segment(s) kept, 0 excluded" in result.output
    assert "preview only" in result.output
    # Nothing built: the original timeline is untouched.
    assert len(timeline.tracks["audio"][1]) == 1


def test_cut_timeline_apply_builds_the_cut(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_covering_the_whole_interview()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["cut-timeline", "--project", "docproj", "--subject", "haylee", "--apply"])

    assert result.exit_code == 0, result.output
    assert "Built '" in result.output
    assert "untouched" in result.output
    # Original still untouched.
    assert len(timeline.tracks["audio"][1]) == 1
    assert timeline.tracks["audio"][1][0].GetDuration() == 37 * 24


def test_cut_timeline_exclude_removes_a_named_segment(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_covering_the_whole_interview()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "cut-timeline", "--project", "docproj", "--subject", "haylee", "--exclude", "haylee.q02",
    ])

    assert result.exit_code == 0, result.output
    assert "1 segment(s) kept, 1 excluded" in result.output
    assert "excluded: haylee.q02" in result.output


def test_cut_timeline_exclude_rejects_unknown_id(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_covering_the_whole_interview()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "cut-timeline", "--project", "docproj", "--subject", "haylee", "--exclude", "not.a.real.id",
    ])

    assert result.exit_code != 0
    assert "not in this subject's analysis" in result.output


def test_cut_timeline_auto_detects_subject(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    timeline, project = _timeline_covering_the_whole_interview()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["cut-timeline", "--project", "docproj"])

    assert result.exit_code == 0, result.output
    assert "Auto-detected subject 'haylee'" in result.output


def test_cut_timeline_nothing_to_cut_when_source_not_on_timeline(tmp_path, monkeypatch):
    _setup_subject(tmp_path, monkeypatch)
    empty_timeline = FakeAssemblyTimeline("Empty", start_frame=0, fps_str="24")
    project = FakeProject(timeline_fps="24")
    project.timelines.append(empty_timeline)
    project.current_timeline = empty_timeline
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(empty_timeline, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["cut-timeline", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "Nothing to cut" in result.output
