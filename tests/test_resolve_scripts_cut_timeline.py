"""Verifies resolve_scripts/Cut Timeline.py's own glue code, especially
the two-step preview-then-confirm flow -- see that script's module
docstring for why it's structured this way, and
test_resolve_scripts_remove_silence.py's docstring for why this is all
tested via fakes instead of real Resolve/osascript.
"""
import json
import runpy
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "resolve_scripts" / "Cut Timeline.py"


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


def _write_config(tmp_path, **extra):
    config = {"theodoreExecutable": "theodore", "theodoreDataDir": str(tmp_path / "data"), **extra}
    (SCRIPT_PATH.parent / "theodore-scripts-config.json").write_text(json.dumps(config))


def _cleanup_config():
    cfg = SCRIPT_PATH.parent / "theodore-scripts-config.json"
    if cfg.exists():
        cfg.unlink()


def _run(monkeypatch, tmp_path, fake_run, project=FakeResolveProject("docproj"), **config_extra):
    monkeypatch.setattr(subprocess, "run", fake_run)
    _write_config(tmp_path, **config_extra)
    try:
        runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(project)})
    finally:
        _cleanup_config()


def test_apply_confirmed_runs_preview_then_apply(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "osascript":
            return subprocess.CompletedProcess(cmd, 0, stdout="button returned:Apply Cut", stderr="")
        if "--apply" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Built 'docproj_trimmed'\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="2 segment(s) kept, 0 excluded.\n", stderr="")

    _run(monkeypatch, tmp_path, fake_run)

    out = capsys.readouterr().out
    assert "Built 'docproj_trimmed'" in out
    theodore_calls = [c for c in calls if c[0] != "osascript"]
    assert len(theodore_calls) == 2
    assert "--apply" not in theodore_calls[0]
    assert "--apply" in theodore_calls[1]


def test_cancel_never_applies(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "osascript":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="User canceled. (-128)")
        return subprocess.CompletedProcess(cmd, 0, stdout="2 segment(s) kept, 0 excluded.\n", stderr="")

    _run(monkeypatch, tmp_path, fake_run)

    out = capsys.readouterr().out
    assert "not applied" in out
    theodore_calls = [c for c in calls if c[0] != "osascript"]
    assert len(theodore_calls) == 1  # preview only


def test_non_mac_never_applies_automatically(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="2 segment(s) kept, 0 excluded.\n", stderr="")

    _run(monkeypatch, tmp_path, fake_run)

    assert len(calls) == 1  # only the preview call -- no osascript, no --apply
    assert "--apply" not in calls[0]


def test_preview_failure_is_shown_and_never_prompts(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "osascript":
            return subprocess.CompletedProcess(cmd, 0, stdout="button returned:OK", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no audio on timeline")

    _run(monkeypatch, tmp_path, fake_run)

    out = capsys.readouterr().out
    assert "preview failed" in out
    assert "no audio on timeline" in out
    # The only dialog shown is the plain OK failure notice -- never the
    # Cancel/Apply Cut confirm prompt, since there was nothing to confirm.
    osascript_calls = [c for c in calls if c[0] == "osascript"]
    assert len(osascript_calls) == 1
    assert "Apply Cut" not in osascript_calls[0][2]


def test_no_project_open_never_calls_theodore(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _run(monkeypatch, tmp_path, fake_run, project=None)

    out = capsys.readouterr().out
    assert "No project open" in out
    theodore_calls = [c for c in calls if c[0] != "osascript"]
    assert theodore_calls == []


def test_missing_config_shows_setup_instructions(capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    _cleanup_config()
    runpy.run_path(str(SCRIPT_PATH), init_globals={"resolve": FakeResolve(FakeResolveProject("docproj"))})

    out = capsys.readouterr().out
    assert "Setup needed" in out
    assert "theodore-scripts-config.json" in out


def test_project_name_override_is_used(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "osascript":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="User canceled. (-128)")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    _run(monkeypatch, tmp_path, fake_run,
         project=FakeResolveProject("Some Resolve Project"), theodoreProject="my-real-project")

    theodore_calls = [c for c in calls if c[0] != "osascript"]
    assert "--project" in theodore_calls[0] and "my-real-project" in theodore_calls[0]
