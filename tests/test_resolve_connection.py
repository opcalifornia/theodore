"""connect() picks a route, and says something useful when both fail.

The routes themselves are covered elsewhere (tests/test_bridge.py drives a
real bridge over real HTTP). This is about the choosing: preference order,
honouring the pin, and -- most importantly -- that a total failure produces
an error a non-technical editor can act on rather than a stack trace.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_resolve import FakeAssemblyTimeline, FakeMediaPool, FakeProject, FakeResolve
from theodore.resolve import bridge_client, connection


@pytest.fixture(autouse=True)
def isolated_bridge_home(tmp_path, monkeypatch):
    """Never read the developer's real ~/.theodore during tests."""
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "bridge-home"))
    monkeypatch.delenv(connection.TRANSPORT_ENV, raising=False)


def _fake_handles_source():
    project = FakeProject()
    project.media_pool = FakeMediaPool(project)
    project.current_timeline = FakeAssemblyTimeline("Interview v1")
    return FakeResolve(project), project


def _install_fake_bridge(monkeypatch, resolve_obj, health=None):
    """Make the bridge route succeed, without a real socket."""
    class _Client:
        def __init__(self):
            self.closed = False

        def health(self):
            return health or {
                "product": "DaVinci Resolve Studio",
                "version": "20.3.1",
                "studio": True,
            }

        def root(self):
            return resolve_obj

        def close(self):
            self.closed = True

    client = _Client()
    monkeypatch.setattr(bridge_client, "connect_bridge", lambda *a, **k: client)
    return client


# ---------------------------------------------------------------------------
# Route preference
# ---------------------------------------------------------------------------

def test_the_bridge_is_preferred_and_external_scripting_is_never_touched(monkeypatch):
    resolve_obj, _project = _fake_handles_source()
    _install_fake_bridge(monkeypatch, resolve_obj)

    def _must_not_be_called():
        raise AssertionError("external scripting must not be attempted when the bridge works")

    monkeypatch.setattr(connection, "_import_scripting_module", _must_not_be_called)

    handles = connection.connect()

    assert handles.via == connection.BRIDGE
    assert handles.project.GetName() == "Test Project"
    assert handles.timeline.GetName() == "Interview v1"
    assert handles.is_studio is True
    assert handles.version == "20.3.1"


def test_external_scripting_is_used_when_no_bridge_is_running(monkeypatch):
    resolve_obj, _project = _fake_handles_source()

    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("no bridge")),
    )

    class _DvrScript:
        @staticmethod
        def scriptapp(_name):
            return resolve_obj

    monkeypatch.setattr(connection, "_import_scripting_module", lambda: _DvrScript)

    handles = connection.connect()

    assert handles.via == connection.EXTERNAL
    assert handles.project.GetName() == "Test Project"
    assert handles.is_studio is True  # read off GetProductName()


def test_transport_can_be_pinned_to_the_bridge(monkeypatch):
    monkeypatch.setenv(connection.TRANSPORT_ENV, "bridge")
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("no bridge")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(AssertionError("must not fall back when pinned")),
    )

    with pytest.raises(connection.ResolveConnectionError, match="no bridge"):
        connection.connect()


def test_transport_can_be_pinned_to_external(monkeypatch):
    resolve_obj, _project = _fake_handles_source()
    monkeypatch.setenv(connection.TRANSPORT_ENV, "external")
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not try the bridge when pinned")),
    )

    class _DvrScript:
        @staticmethod
        def scriptapp(_name):
            return resolve_obj

    monkeypatch.setattr(connection, "_import_scripting_module", lambda: _DvrScript)

    assert connection.connect().via == connection.EXTERNAL


def test_a_nonsense_transport_value_falls_back_to_auto(monkeypatch):
    resolve_obj, _project = _fake_handles_source()
    monkeypatch.setenv(connection.TRANSPORT_ENV, "carrier-pigeon")
    _install_fake_bridge(monkeypatch, resolve_obj)
    assert connection.connect().via == connection.BRIDGE


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------

def test_a_live_bridge_whose_call_fails_does_not_silently_fall_back(monkeypatch):
    """A bridge that answers but errors is a real problem with this Resolve.
    Falling back would hide it behind a misleading external-scripting error."""
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeCallError("GetProjectManager blew up")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(AssertionError("must not fall back from a live bridge")),
    )

    with pytest.raises(connection.ResolveConnectionError, match="blew up"):
        connection.connect()


def test_when_both_routes_fail_the_error_leads_with_the_bridge(monkeypatch):
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(
            bridge_client.BridgeUnavailable("Resolve's Theodore bridge isn't running.")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(connection.ResolveConnectionError("no DaVinciResolveScript")),
    )

    with pytest.raises(connection.ResolveConnectionError) as excinfo:
        connection.connect()

    message = str(excinfo.value)
    # The bridge is the route that works on any edition with no preference
    # fiddling, so it must be the one the user reads first.
    assert message.index("bridge isn't running") < message.index("no DaVinciResolveScript")
    assert "Workspace -> Scripts" in message


def test_no_project_open_is_reported_specifically(monkeypatch):
    resolve_obj, _project = _fake_handles_source()
    resolve_obj.project_manager.project = None
    _install_fake_bridge(monkeypatch, resolve_obj)

    with pytest.raises(connection.ResolveConnectionError, match="no project is open"):
        connection.connect()


def test_no_timeline_open_is_reported_specifically(monkeypatch):
    resolve_obj, project = _fake_handles_source()
    project.current_timeline = None
    _install_fake_bridge(monkeypatch, resolve_obj)

    with pytest.raises(connection.ResolveConnectionError, match="no current\ntimeline|no current timeline"):
        connection.connect()


def test_free_edition_external_failure_points_at_the_bridge(monkeypatch):
    """The most likely real-world failure: free edition, no bridge started."""
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("not running")),
    )

    class _DvrScript:
        @staticmethod
        def scriptapp(_name):
            return None  # what the free edition does

    monkeypatch.setattr(connection, "_import_scripting_module", lambda: _DvrScript)

    with pytest.raises(connection.ResolveConnectionError) as excinfo:
        connection.connect()

    message = str(excinfo.value)
    assert "External scripting" in message       # names the preference to check
    assert "free edition" in message             # names the edition limit
    assert "Theodore Bridge" in message          # and the way around both


# ---------------------------------------------------------------------------
# probe() -- for status displays
# ---------------------------------------------------------------------------

def test_probe_reports_a_connection_without_raising(monkeypatch):
    resolve_obj, _project = _fake_handles_source()
    _install_fake_bridge(monkeypatch, resolve_obj)

    status = connection.probe()

    assert status["connected"] is True
    assert status["via"] == connection.BRIDGE
    assert status["project"] == "Test Project"
    assert status["timeline"] == "Interview v1"
    assert status["studio"] is True
    assert status["error"] is None


def test_probe_reports_failure_without_raising(monkeypatch):
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("not running")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(connection.ResolveConnectionError("nope")),
    )

    status = connection.probe()

    assert status["connected"] is False
    assert status["error"]                 # explains what to do
    assert status["bridge_running"] is False
    assert status["project"] is None


def test_probe_notices_a_handshake_file_exists(monkeypatch, tmp_path):
    from theodore.resolve import bridge_protocol as proto

    path = proto.handshake_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "protocol": proto.PROTOCOL_VERSION, "port": 59999, "token": "t" * 64,
    }))
    monkeypatch.setattr(
        bridge_client, "connect_bridge",
        lambda *a, **k: (_ for _ in ()).throw(bridge_client.BridgeUnavailable("stale")),
    )
    monkeypatch.setattr(
        connection, "_import_scripting_module",
        lambda: (_ for _ in ()).throw(connection.ResolveConnectionError("nope")),
    )

    status = connection.probe()

    # Distinguishes "never started the bridge" from "bridge died" -- which
    # are different instructions for the user.
    assert status["bridge_running"] is True
    assert status["connected"] is False


# ---------------------------------------------------------------------------
# ResolveHandles stays backward compatible
# ---------------------------------------------------------------------------

def test_handles_still_build_from_the_four_original_fields():
    # Eight existing test modules construct handles this way; adding bridge
    # metadata must not have broken any of them.
    handles = connection.ResolveHandles(
        resolve=None, project_manager=None, project=None, timeline=None,
    )
    assert handles.via is None
    assert handles.client is None
    assert handles.info == {}
    assert handles.is_studio is False
    assert handles.product is None
