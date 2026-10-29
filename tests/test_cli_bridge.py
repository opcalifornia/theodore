"""`theodore install-bridge` and `theodore bridge-status`.

install-bridge exists because hand-installing the bridge script is the step
most likely to fail silently: the Scripts folder is buried, differs per OS,
and Resolve only scans it at launch -- so a file in the wrong place is
indistinguishable from a file Resolve is ignoring.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from theodore.cli import BRIDGE_SCRIPT_NAME, cli
from theodore.resolve import bridge_client, bridge_protocol as proto, connection


# ---------------------------------------------------------------------------
# install-bridge
# ---------------------------------------------------------------------------

def test_install_bridge_copies_the_real_script(tmp_path):
    dest = tmp_path / "Scripts" / "Theodore"
    result = CliRunner().invoke(cli, ["install-bridge", "--dest", str(dest)])

    assert result.exit_code == 0, result.output
    installed = dest / BRIDGE_SCRIPT_NAME
    assert installed.exists()
    # The real thing, not a stub: it must contain the server entry point.
    assert "def start_bridge(" in installed.read_text()


def test_install_bridge_tells_you_to_fully_quit_resolve(tmp_path):
    """The single most common reason a correctly-installed script doesn't
    appear -- Resolve builds the Scripts menu only at startup."""
    result = CliRunner().invoke(
        cli, ["install-bridge", "--dest", str(tmp_path / "s")],
    )
    assert result.exit_code == 0
    assert "FULLY quit" in result.output
    assert "Workspace -> Scripts -> Theodore -> Theodore Bridge" in result.output


def test_install_bridge_dry_run_changes_nothing(tmp_path):
    dest = tmp_path / "Scripts" / "Theodore"
    result = CliRunner().invoke(
        cli, ["install-bridge", "--dest", str(dest), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    assert not dest.exists()


def test_install_bridge_names_both_paths_before_acting(tmp_path):
    dest = tmp_path / "Scripts" / "Theodore"
    result = CliRunner().invoke(
        cli, ["install-bridge", "--dest", str(dest), "--dry-run"],
    )
    assert BRIDGE_SCRIPT_NAME in result.output
    assert str(dest) in result.output


def test_install_bridge_overwrites_an_older_copy(tmp_path):
    dest = tmp_path / "Scripts" / "Theodore"
    dest.mkdir(parents=True)
    (dest / BRIDGE_SCRIPT_NAME).write_text("# stale old version\n")

    result = CliRunner().invoke(cli, ["install-bridge", "--dest", str(dest)])

    assert result.exit_code == 0, result.output
    assert "stale old version" not in (dest / BRIDGE_SCRIPT_NAME).read_text()


def test_install_bridge_autodetects_a_destination_when_none_is_given(tmp_path, monkeypatch):
    from theodore.resolve import paths as resolve_paths

    fake_scripts = tmp_path / "Fusion" / "Scripts" / "Utility"
    fake_scripts.mkdir(parents=True)
    monkeypatch.setattr(resolve_paths, "scripts_dirs", lambda system=None: [fake_scripts])

    result = CliRunner().invoke(cli, ["install-bridge"])

    assert result.exit_code == 0, result.output
    assert (fake_scripts / "Theodore" / BRIDGE_SCRIPT_NAME).exists()


def test_install_bridge_explains_an_unknown_platform(monkeypatch):
    from theodore.resolve import paths as resolve_paths

    monkeypatch.setattr(resolve_paths, "scripts_dirs", lambda system=None: [])
    result = CliRunner().invoke(cli, ["install-bridge"])

    assert result.exit_code != 0
    assert "--dest" in result.output  # tells you how to proceed anyway


# ---------------------------------------------------------------------------
# bridge-status
# ---------------------------------------------------------------------------

def test_bridge_status_reports_no_bridge_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "none"))
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("not running")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(connection.ResolveConnectionError("no module")),
    )

    result = CliRunner().invoke(cli, ["bridge-status"])

    # Diagnostics must never exit nonzero -- the whole point is to report.
    assert result.exit_code == 0, result.output
    assert "NOT CONNECTED" in result.output
    assert "has not been started" in result.output


def test_bridge_status_reports_a_live_bridge(tmp_path, monkeypatch):
    from fake_resolve import FakeAssemblyTimeline, FakeMediaPool, FakeProject, FakeResolve

    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "home"))
    path = proto.handshake_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "protocol": proto.PROTOCOL_VERSION, "port": 54321, "token": "t" * 64, "pid": 4242,
    }))

    project = FakeProject()
    project.media_pool = FakeMediaPool(project)
    project.current_timeline = FakeAssemblyTimeline("Interview v1")
    resolve_obj = FakeResolve(project)

    class _Client:
        def health(self):
            return {"product": "DaVinci Resolve Studio", "version": "20.3.1", "studio": True}

        def root(self):
            return resolve_obj

        def close(self):
            pass

    monkeypatch.setattr(bridge_client, "connect_bridge", lambda *a, **k: _Client())

    result = CliRunner().invoke(cli, ["bridge-status"])

    assert result.exit_code == 0, result.output
    assert "CONNECTED via the in-Resolve bridge" in result.output
    assert "127.0.0.1:54321" in result.output
    assert "Resolve pid 4242" in result.output
    assert "Studio" in result.output
    assert "Interview v1" in result.output


def test_bridge_status_nudges_toward_the_bridge_when_on_the_fallback(tmp_path, monkeypatch):
    from fake_resolve import FakeAssemblyTimeline, FakeMediaPool, FakeProject, FakeResolve

    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "none"))
    project = FakeProject()
    project.media_pool = FakeMediaPool(project)
    project.current_timeline = FakeAssemblyTimeline("Interview v1")
    resolve_obj = FakeResolve(project)

    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("not running")),
    )

    class _DvrScript:
        @staticmethod
        def scriptapp(_name):
            return resolve_obj

    monkeypatch.setattr(connection, "_import_scripting_module", lambda: _DvrScript)

    result = CliRunner().invoke(cli, ["bridge-status"])

    assert result.exit_code == 0, result.output
    assert "CONNECTED via external scripting" in result.output
    assert "theodore install-bridge" in result.output


# ---------------------------------------------------------------------------
# install-panel -- Resolve runs a COPY of resolve_panel/, so a pull alone
# changes nothing until the copy is refreshed.
# ---------------------------------------------------------------------------

def _installed_panel(tmp_path):
    dest = tmp_path / "Plugins" / "Theodore"
    (dest / "node_modules" / "electron").mkdir(parents=True)
    (dest / "node_modules" / "electron" / "marker").write_text("keep me")
    (dest / "theodore-panel-config.json").write_text('{"theodoreExecutable": "/mine"}')
    (dest / "main.js").write_text("// old panel")
    return dest


def test_install_panel_refreshes_code_but_keeps_config_and_node_modules(tmp_path):
    dest = _installed_panel(tmp_path)
    result = CliRunner().invoke(cli, ["install-panel", "--dest", str(dest)])

    assert result.exit_code == 0, result.output
    assert "old panel" not in (dest / "main.js").read_text()
    assert "startAgentChat" in (dest / "main.js").read_text() or (dest / "theodore_bridge.js").exists()
    assert (dest / "theodore-panel-config.json").read_text() == '{"theodoreExecutable": "/mine"}'
    assert (dest / "node_modules" / "electron" / "marker").read_text() == "keep me"
    assert not (dest / "test").exists()


def test_install_panel_dry_run_changes_nothing(tmp_path):
    dest = _installed_panel(tmp_path)
    result = CliRunner().invoke(cli, ["install-panel", "--dest", str(dest), "--dry-run"])
    assert result.exit_code == 0 and "dry run" in result.output
    assert (dest / "main.js").read_text() == "// old panel"


def test_install_panel_without_an_install_says_how_to_proceed(monkeypatch):
    from theodore import cli as cli_module
    monkeypatch.setattr(cli_module, "_panel_install_candidates", lambda: [])
    result = CliRunner().invoke(cli, ["install-panel"])
    assert result.exit_code != 0 and "--dest" in result.output
