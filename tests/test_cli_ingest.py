from click.testing import CliRunner

from theodore.cli import cli
from theodore.ingest import audio as ingest_audio
from theodore.ingest import media as ingest_media


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _fake_extract_wav(path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}.wav"
    out.write_bytes(b"")
    return out


def test_ingest_skips_one_unreadable_file_and_keeps_the_rest(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    source = tmp_path / "footage"
    _touch(source / "good_a.wav")
    _touch(source / "good_b.wav")
    _touch(source / "bad.r3d")

    def fake_probe(path):
        if path.name == "bad.r3d":
            raise ingest_media.MediaProbeError(
                f"ffprobe failed on {path}: Invalid data found when processing input"
            )
        return ingest_media.MediaInfo(
            path=str(path), fps="0/1", duration_seconds=10.0,
            start_timecode="00:00:00:00", has_video=False, has_audio=True,
        )

    monkeypatch.setattr(ingest_media, "probe", fake_probe)
    monkeypatch.setattr(ingest_audio, "extract_wav", _fake_extract_wav)

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "SKIPPED -- ffprobe failed on" in result.output
    assert "Ingested 2 of 3 file(s) for subject 'haylee'" in result.output
    assert "1 file(s) skipped" in result.output
    assert "bad.r3d: ffprobe failed on" in result.output


def test_ingest_registers_only_the_files_that_actually_succeeded(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    source = tmp_path / "footage"
    _touch(source / "good.wav")
    _touch(source / "bad.r3d")

    def fake_probe(path):
        if path.name == "bad.r3d":
            raise ingest_media.MediaProbeError("nope")
        return ingest_media.MediaInfo(
            path=str(path), fps="0/1", duration_seconds=10.0,
            start_timecode="00:00:00:00", has_video=False, has_audio=True,
        )

    monkeypatch.setattr(ingest_media, "probe", fake_probe)
    monkeypatch.setattr(ingest_audio, "extract_wav", _fake_extract_wav)

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    from theodore import registry
    reg = registry.load_project(tmp_path / "data" / "docproj", project_name="docproj")
    assert reg["subjects"]["haylee"]["source_files"] == ["good.wav"]


def test_ingest_raises_when_every_file_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    source = tmp_path / "footage"
    _touch(source / "bad.r3d")

    monkeypatch.setattr(
        ingest_media, "probe",
        lambda path: (_ for _ in ()).throw(ingest_media.MediaProbeError("nope")),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code != 0
    assert "could be ingested" in result.output


def test_ingest_all_succeeding_prints_no_skipped_section(monkeypatch, tmp_path):
    monkeypatch.setattr("theodore.config.DATA_ROOT", tmp_path / "data")

    source = tmp_path / "footage"
    _touch(source / "good.wav")

    monkeypatch.setattr(
        ingest_media, "probe",
        lambda path: ingest_media.MediaInfo(
            path=str(path), fps="0/1", duration_seconds=10.0,
            start_timecode="00:00:00:00", has_video=False, has_audio=True,
        ),
    )
    monkeypatch.setattr(ingest_audio, "extract_wav", _fake_extract_wav)

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(source), "--project", "docproj", "--subject", "haylee"])

    assert result.exit_code == 0, result.output
    assert "Ingested 1 of 1 file(s)" in result.output
    assert "skipped" not in result.output.lower()
