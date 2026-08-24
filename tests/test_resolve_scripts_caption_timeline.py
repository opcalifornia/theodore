"""Verifies resolve_scripts/Caption Timeline.py's own glue code, the same
way test_resolve_scripts_remove_silence.py verifies its sibling script --
see that file's module docstring for why this is tested via a fake
`resolve` global instead of real Resolve.
"""
import json
import runpy
import stat
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "resolve_scripts" / "Caption Timeline.py"


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
    config = {"theodoreExecutable": str(executable), "theodoreDataDir": str(tmp_path / "data"), **extra}
    (SCRIPT_PATH.parent / "theodore-scripts-config.json").write_text(json.dumps(config))


def _cleanup_config():
    cfg = SCRIPT_PATH.parent / "theodore-scripts-config.json"
    if cfg.exists():
        cfg.unlink()


def test_runs_theodore_caption_timeline_with_import_flag(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "ARGS: $@"')
    _write_config(tmp_path, stub)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "ARGS: caption-timeline --project docproj --import-to-resolve" in out


def test_project_name_override_is_used(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "ARGS: $@"')
    _write_config(tmp_path, stub, theodoreProject="my-real-project")
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("Some Resolve Project"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "--project my-real-project" in out


def test_no_project_open_shows_a_clear_message(tmp_path, capsys, monkeypatch):
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


def test_missing_config_shows_setup_instructions(capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    _cleanup_config()
    runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})

    out = capsys.readouterr().out
    assert "Setup needed" in out
    assert "theodore-scripts-config.json" in out


def test_theodore_failure_is_reported(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    stub = _stub_theodore(tmp_path, 'echo "no matching clip on timeline" >&2; exit 1')
    _write_config(tmp_path, stub)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})
    finally:
        _cleanup_config()

    out = capsys.readouterr().out
    assert "Caption Timeline failed" in out
    assert "no matching clip on timeline" in out
