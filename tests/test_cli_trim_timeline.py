from click.testing import CliRunner
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeProject, FakeTimelineItem

from theodore.cli import cli


class FakeHandles:
    def __init__(self, timeline, project=None):
        self.timeline = timeline
        self.project = project


def _timeline_with_one_clip():
    original = FakeAssemblyTimeline("Editor's real cut", start_frame=86400)
    cam = FakeMediaPoolItem(path="/media/cam.mov", frames=100000, start=0)
    item = FakeTimelineItem(cam, start=86400, duration=1000)
    original.tracks["video"][1].append(item)
    project = FakeProject()
    project.timelines.append(original)
    project.current_timeline = original
    return original, project


def test_trim_timeline_removes_a_range_and_prints_the_summary(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["trim-timeline", "--cut", "86800:87000"])

    assert result.exit_code == 0, result.output
    assert "Editor's real cut" in result.output
    assert "200" in result.output
    assert "untouched" in result.output


def test_trim_timeline_rejects_a_malformed_cut(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["trim-timeline", "--cut", "not-a-range"])

    assert result.exit_code != 0
    assert "START:END" in result.output


def test_trim_timeline_rejects_non_integer_bounds(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["trim-timeline", "--cut", "abc:def"])

    assert result.exit_code != 0
    assert "START:END" in result.output


def test_trim_timeline_requires_at_least_one_cut(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["trim-timeline"])

    assert result.exit_code != 0


def test_trim_timeline_surfaces_builder_errors_as_click_exceptions(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    # A cut range covering the whole (only) clip removes everything.
    result = runner.invoke(cli, ["trim-timeline", "--cut", "86400:87400"])

    assert result.exit_code != 0
    assert "remove everything" in result.output


def test_trim_timeline_accepts_a_custom_name(monkeypatch):
    original, project = _timeline_with_one_clip()
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(original, project))

    runner = CliRunner()
    result = runner.invoke(cli, ["trim-timeline", "--cut", "86800:87000", "--name", "My Cut"])

    assert result.exit_code == 0, result.output
    assert "My Cut" in result.output
