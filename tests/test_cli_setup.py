from click.testing import CliRunner

from theodore.cli import cli


def _run(monkeypatch, tmp_path, input_text, which_map=None):
    monkeypatch.setattr("theodore.config.PROJECT_ROOT", tmp_path)
    if which_map is not None:
        monkeypatch.setattr("shutil.which", lambda name: which_map.get(name))
    runner = CliRunner()
    return runner.invoke(cli, ["setup"], input=input_text)


def test_setup_creates_env_from_example_when_missing(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\nDEEPGRAM_API_KEY=\n")
    result = _run(monkeypatch, tmp_path, "sk-ant-fake\ndg-fake\n", which_map={"ffmpeg": "/x", "ffprobe": "/x"})
    assert result.exit_code == 0
    assert f"Created {tmp_path / '.env'}" in result.output
    env_text = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-fake" in env_text
    assert "DEEPGRAM_API_KEY=dg-fake" in env_text


def test_setup_falls_back_to_default_template_without_example(monkeypatch, tmp_path):
    result = _run(monkeypatch, tmp_path, "sk-ant-fake\ndg-fake\n", which_map={"ffmpeg": "/x", "ffprobe": "/x"})
    assert result.exit_code == 0
    assert (tmp_path / ".env").exists()
    env_text = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-fake" in env_text
    assert "DEEPGRAM_API_KEY=dg-fake" in env_text


def test_setup_never_overwrites_an_already_set_key(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=already-here\nDEEPGRAM_API_KEY=also-here\n")
    result = _run(monkeypatch, tmp_path, "", which_map={"ffmpeg": "/x", "ffprobe": "/x"})
    assert result.exit_code == 0
    assert "Anthropic API key: already set." in result.output
    assert "Deepgram API key: already set." in result.output
    # No prompt output for either key, and the file is untouched.
    assert "Paste your Anthropic" not in result.output
    env_text = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=already-here" in env_text
    assert "DEEPGRAM_API_KEY=also-here" in env_text


def test_setup_blank_input_skips_a_key_without_crashing(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\nDEEPGRAM_API_KEY=\n")
    result = _run(monkeypatch, tmp_path, "\n\n", which_map={"ffmpeg": "/x", "ffprobe": "/x"})
    assert result.exit_code == 0
    assert "Skipped" in result.output
    assert "Not fully set up yet" in result.output


def test_setup_reports_missing_ffmpeg(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=x\nDEEPGRAM_API_KEY=x\n")
    result = _run(monkeypatch, tmp_path, "", which_map={})
    assert result.exit_code == 0
    assert "ffmpeg on PATH:  NO" in result.output
    assert "Not fully set up yet" in result.output


def test_setup_reports_ready_when_everything_is_present(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=x\nDEEPGRAM_API_KEY=x\n")
    result = _run(monkeypatch, tmp_path, "", which_map={"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"})
    assert result.exit_code == 0
    assert "Everything's set." in result.output


def test_setup_never_echoes_the_key_back(monkeypatch, tmp_path):
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\nDEEPGRAM_API_KEY=\n")
    secret = "sk-ant-super-secret-value"
    result = _run(monkeypatch, tmp_path, f"{secret}\ndg-fake\n", which_map={"ffmpeg": "/x", "ffprobe": "/x"})
    assert result.exit_code == 0
    # hide_input=True means click never prints the typed value to stdout.
    assert secret not in result.output
