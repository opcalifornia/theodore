from click.testing import CliRunner
from fake_resolve import FakeAssemblyTimeline, FakeMediaPoolItem, FakeTimelineItem

from theodore import registry
from theodore.cli import cli
from theodore.ingest import audio as ingest_audio
from theodore.ingest import media as ingest_media


class FakeHandles:
    def __init__(self, timeline):
        self.timeline = timeline


def _fake_probe(path):
    return ingest_media.MediaInfo(
        path=str(path), fps="0/1", duration_seconds=10.0,
        start_timecode="00:00:00:00", has_video=False, has_audio=True,
    )


def _fake_extract_wav(path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}.wav"
    out.write_bytes(b"")
    return out


def test_ingest_from_timeline_pulls_only_audio_track_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(ingest_media, "probe", _fake_probe)
    monkeypatch.setattr(ingest_audio, "extract_wav", _fake_extract_wav)

    timeline = FakeAssemblyTimeline("Interview Sync v1")
    cam = FakeMediaPoolItem(path=str(tmp_path / "cam_a.r3d"))
    lav = FakeMediaPoolItem(path=str(tmp_path / "DJI_17.WAV"))
    (tmp_path / "DJI_17.WAV").write_bytes(b"")
    timeline.tracks["video"][1].append(FakeTimelineItem(cam, start=0, duration=100))
    timeline.tracks["audio"][1].append(FakeTimelineItem(lav, start=0, duration=100))
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline))

    runner = CliRunner()
    result = runner.invoke(
        cli, ["ingest", "--from-timeline", "--project", "docproj", "--subject", "haylee"]
    )

    assert result.exit_code == 0, result.output
    assert "DJI_17.WAV" in result.output
    assert "cam_a.r3d" not in result.output
    reg = registry.load_project(tmp_path / "data" / "docproj", project_name="docproj")
    assert reg["subjects"]["haylee"]["source_files"] == ["DJI_17.WAV"]


def test_ingest_from_timeline_and_source_path_together_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    source = tmp_path / "footage.wav"
    source.write_bytes(b"")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["ingest", str(source), "--from-timeline", "--project", "docproj", "--subject", "haylee"]
    )

    assert result.exit_code != 0
    assert "not both" in result.output


def test_ingest_without_source_or_from_timeline_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code != 0
    assert "--from-timeline" in result.output


def test_ingest_from_timeline_with_no_audio_on_timeline_names_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    timeline = FakeAssemblyTimeline("Edit")
    cam = FakeMediaPoolItem(path="/media/cam_a.mov")
    timeline.tracks["video"][1].append(FakeTimelineItem(cam, start=0, duration=100))
    monkeypatch.setattr("theodore.cli._connect_or_die", lambda: FakeHandles(timeline))

    runner = CliRunner()
    result = runner.invoke(
        cli, ["ingest", "--from-timeline", "--project", "docproj", "--subject", "haylee"]
    )

    assert result.exit_code != 0
    assert "Auto Sync Audio" in result.output


def test_run_requires_source_or_from_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code != 0
    assert "--from-timeline" in result.output


def test_run_rejects_source_and_from_timeline_together(tmp_path, monkeypatch):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")
    source = tmp_path / "footage.wav"
    source.write_bytes(b"")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["run", str(source), "--from-timeline", "--project", "docproj", "--subject", "haylee"]
    )

    assert result.exit_code != 0
    assert "not both" in result.output
