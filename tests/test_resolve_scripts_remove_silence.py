"""Verifies resolve_scripts/Remove Silence.py's own glue code -- reading
config, discovering the project from a fake `resolve` global (the way
Resolve itself injects it), building the subprocess call, and surfacing
the result -- without needing real Resolve. The command it shells out to
(`theodore remove-silence`) has its own full test coverage elsewhere;
this only tests that THIS script wires it up correctly.
"""
import runpy
import stat
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "resolve_scripts" / "Remove Silence.py"


class FakeResolveProject:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class FakeProjectManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class FakeResolve:
    def __init__(self, project=None):
        self._pm = FakeProjectManager(project)

    def GetProjectManager(self):
        return self._pm


def _stub_theodore(tmp_path, script_body: str) -> Path:
    stub = tmp_path / "theodore"
    stub.write_text(f"#!/bin/sh\n{script_body}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _write_config(tmp_path, executable: Path, **extra):
    import json
    config = {"theodoreExecutable": str(executable), "theodoreDataDir": str(tmp_path / "data"), **extra}
    (SCRIPT_PATH.parent / "theodore-scripts-config.json").write_text(json.dumps(config))


def _cleanup_config():
    cfg = SCRIPT_PATH.parent / "theodore-scripts-config.json"
    if cfg.exists():
        cfg.unlink()


def test_runs_theodore_remove_silence_with_the_current_project_name(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")  # skip the Mac dialog branch
    stub = _stub_theodore(tmp_path, 'echo "ARGS: $@"; echo "PROJECT_ENV: $THEODORE_DATA_DIR"')
    _write_config(tmp_path, stub)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "ARGS: remove-silence --project docproj" in out
    assert f"PROJECT_ENV: {tmp_path / 'data'}" in out


def test_project_name_override_is_used_instead_of_resolves_project_name(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "ARGS: $@"')
    _write_config(tmp_path, stub, theodoreProject="my-real-project")
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("Some Resolve Project"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "--project my-real-project" in out


def test_no_project_open_shows_a_clear_message_not_a_crash(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, "echo should-not-run")
    _write_config(tmp_path, stub)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(project=None)})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "No project open" in out
    assert "should-not-run" not in out


def test_missing_config_shows_setup_instructions_not_a_traceback(capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    _cleanup_config()  # ensure no leftover config from a previous run
    runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})

    out = capsys.readouterr().out
    assert "Setup needed" in out
    assert "theodore-scripts-config.json" in out


def test_theodore_failure_is_reported_not_swallowed(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "boom: no audio on timeline" >&2; exit 1')
    _write_config(tmp_path, stub)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "Remove Silence failed" in out
    assert "boom: no audio on timeline" in out


def test_silence_threshold_and_aggressive_flags_pass_through(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "ARGS: $@"')
    _write_config(tmp_path, stub, silenceThreshold=2.5, aggressive=True)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "--silence-threshold 2.5" in out
    assert "--aggressive" in out
