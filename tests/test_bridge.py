"""The bridge, end to end, over real HTTP.

This is not a mocked test. It starts the *actual* server from
``resolve_scripts/Theodore Bridge.py`` on a real loopback socket, points the
*actual* ``bridge_client`` at it, and drives it against ``fake_resolve.py``
standing in for Resolve. Everything between the two halves -- the socket,
HTTP/1.1 keep-alive, token auth, JSON framing, the handle table, the codec,
the worker thread -- is the real code path that will run on the user's Mac.

What cannot be covered here is Resolve itself: whether it really injects
``resolve`` into a Scripts-menu script, and whether a daemon thread started
by such a script survives after the script returns. Those need real
hardware, which is what ``scripts/live_smoke_test.py`` is for.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

from fake_resolve import (
    FakeAssemblyTimeline, FakeMediaPool, FakeProject, FakeResolve,
)
from theodore.resolve import bridge_client, bridge_protocol as proto

BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "resolve_scripts" / "Theodore Bridge.py"


def _load_bridge_module():
    """Import the Scripts-menu script as a module.

    It lives outside the package, with a space in its filename, because
    Resolve uses the filename as the menu label. Importing it is safe: its
    ``main()`` is behind an ``if __name__ == "__main__"`` guard precisely so
    it can be tested.
    """
    spec = importlib.util.spec_from_file_location("theodore_bridge_script", BRIDGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge_module = _load_bridge_module()


def _build_fake_resolve():
    project = FakeProject()
    timeline = FakeAssemblyTimeline("Interview v1")
    project.media_pool = FakeMediaPool(project)
    project.current_timeline = timeline
    return FakeResolve(project), project, timeline


@pytest.fixture()
def live_bridge(tmp_path, monkeypatch):
    """A real bridge server plus a real client wired to it."""
    # Keep the handshake out of the developer's real ~/.theodore.
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "bridge-home"))

    resolve_obj, project, timeline = _build_fake_resolve()
    server, port, state, handshake_path = bridge_module.start_bridge(resolve_obj, {})

    handshake = json.loads(Path(handshake_path).read_text())
    client = bridge_client.BridgeClient(handshake, socket_timeout=15.0)
    try:
        yield client, resolve_obj, project, timeline, state, handshake
    finally:
        client.close()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------

def test_handshake_is_written_with_a_port_and_token_and_is_owner_only(live_bridge):
    _client, _resolve, _project, _timeline, _state, handshake = live_bridge
    path = proto.handshake_path()

    assert handshake["port"] > 0
    assert len(handshake["token"]) == 64  # token_hex(32)
    assert handshake["host"] == "127.0.0.1"
    assert handshake["protocol"] == proto.PROTOCOL_VERSION
    # The token is full control of the user's Resolve -- it must never be
    # readable by other users on the machine.
    assert path.stat().st_mode & 0o077 == 0


def test_read_handshake_rejects_a_future_protocol_version(tmp_path, monkeypatch):
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path))
    path = proto.handshake_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "protocol": proto.PROTOCOL_VERSION + 99, "port": 1234, "token": "x",
    }))
    # Better to report no bridge (and fall back) than to speak a protocol we
    # don't understand to a newer bridge.
    assert bridge_client.read_handshake() is None


def test_read_handshake_returns_none_when_no_bridge_ever_ran(tmp_path, monkeypatch):
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "nothing-here"))
    assert bridge_client.read_handshake() is None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_reports_product_version_and_edition(live_bridge):
    client, *_ = live_bridge
    health = client.health()

    assert health["protocol"] == proto.PROTOCOL_VERSION
    assert health["product"] == "DaVinci Resolve Studio"
    assert health["version"] == "20.3.1 Build 5"
    assert health["studio"] is True


def test_health_answers_while_a_long_call_is_still_running(live_bridge):
    """Health must not queue behind a stuck call -- that is exactly when
    you need it to tell you what's going on."""
    client, resolve_obj, project, _timeline, _state, handshake = live_bridge

    release = threading.Event()

    def slow_get_name():
        release.wait(10)
        return "Test Project"

    project.GetName = slow_get_name

    # Separate client: one client serializes its own socket by design, so
    # this models two front doors (CLI and panel) hitting the bridge at once.
    other = bridge_client.BridgeClient(handshake, socket_timeout=15.0)
    slow_result = {}

    def run_slow():
        try:
            pm = other.root().GetProjectManager()
            slow_result["name"] = pm.GetCurrentProject().GetName()
        except Exception as exc:  # noqa: BLE001
            slow_result["error"] = exc

    thread = threading.Thread(target=run_slow)
    thread.start()
    try:
        time.sleep(0.3)  # let the slow call reach the worker
        health = client.health()
        assert health["product"] == "DaVinci Resolve Studio"
    finally:
        release.set()
        thread.join(15)
        other.close()

    assert slow_result.get("name") == "Test Project"


# ---------------------------------------------------------------------------
# The object graph
# ---------------------------------------------------------------------------

def test_the_full_object_chain_round_trips_as_proxies(live_bridge):
    client, *_ = live_bridge

    resolve_proxy = client.root()
    pm = resolve_proxy.GetProjectManager()
    project = pm.GetCurrentProject()
    timeline = project.GetCurrentTimeline()

    assert isinstance(pm, bridge_client.ResolveProxy)
    assert isinstance(project, bridge_client.ResolveProxy)
    assert isinstance(timeline, bridge_client.ResolveProxy)
    # Plain values come back as themselves, not as handles.
    assert project.GetName() == "Test Project"
    assert timeline.GetName() == "Interview v1"
    assert timeline.GetStartFrame() == 86400
    assert timeline.GetSetting("timelineFrameRate") == "24"


def test_a_list_of_objects_comes_back_as_a_list_of_proxies(live_bridge):
    client, _resolve, _project, timeline, _state, _hs = live_bridge
    from fake_resolve import FakeMediaPoolItem, FakeTimelineItem

    item = FakeTimelineItem(FakeMediaPoolItem(), start=86400, duration=240)
    timeline.tracks["video"][1] = [item]

    proxy_timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    items = proxy_timeline.GetItemListInTrack("video", 1)

    assert isinstance(items, list) and len(items) == 1
    assert isinstance(items[0], bridge_client.ResolveProxy)
    # And the proxy in the list is really usable, not just well-shaped.
    assert items[0].GetStart() == 86400
    assert items[0].GetDuration() == 240


def test_marker_dicts_keep_their_integer_keys(live_bridge):
    """The one that would corrupt real output if it regressed.

    Resolve returns ``{frame: {...}}`` keyed by int, and
    ``resolve/markers.py`` does ``rel_frame in existing`` / ``existing[rel_frame]``
    with an int. Plain JSON stringifies keys, so every ``in`` test would
    quietly answer False and marker de-duplication would silently stop
    working -- no error, just wrong markers.
    """
    client, _resolve, _project, timeline, _state, _hs = live_bridge
    timeline.markers[1200] = {"color": "Blue", "customData": '{"theodore": true}'}
    timeline.markers[2400] = {"color": "Red", "customData": ""}

    proxy_timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    markers = proxy_timeline.GetMarkers()

    assert set(markers) == {1200, 2400}
    assert all(isinstance(k, int) for k in markers)
    assert 1200 in markers                      # the exact check markers.py makes
    assert markers[1200]["color"] == "Blue"


def test_booleans_survive_as_booleans_not_integers(live_bridge):
    """bool is an int subclass, so a careless codec turns True into 1 --
    and Theodore checks Resolve's return values with `is False` / truthiness."""
    client, _resolve, _project, timeline, _state, _hs = live_bridge
    timeline.add_marker_returns = False

    proxy_timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    result = proxy_timeline.AddMarker(100, "Blue", "n", "note", 1, "data")

    assert result is False


def test_a_proxy_passed_as_an_argument_resolves_to_the_real_object(live_bridge):
    """`project.SetCurrentTimeline(timeline)` has to work: the argument is
    itself a Resolve object living on the other side of the wire."""
    client, _resolve, project, _timeline, _state, _hs = live_bridge

    proxy_project = client.root().GetProjectManager().GetCurrentProject()
    duplicate = proxy_project.GetCurrentTimeline().DuplicateTimeline("Interview v2")
    assert isinstance(duplicate, bridge_client.ResolveProxy)

    assert proxy_project.SetCurrentTimeline(duplicate) is not False
    # The *real* fake object changed, so the reference really was unwrapped
    # server-side rather than passed as opaque JSON.
    assert project.current_timeline.GetName() == "Interview v2"


# ---------------------------------------------------------------------------
# Truthful attribute probing
# ---------------------------------------------------------------------------

def test_a_missing_method_raises_attributeerror_like_a_real_object(live_bridge):
    """builder.py's `getattr(obj, name, None) is None` probe must keep
    working -- it distinguishes "this Resolve build can't do it" from
    "the call succeeded and returned nothing"."""
    client, *_ = live_bridge
    timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()

    with pytest.raises(AttributeError):
        timeline.NoSuchResolveMethod

    assert getattr(timeline, "NoSuchResolveMethod", None) is None
    assert getattr(timeline, "GetName", None) is not None


def test_probing_is_cached_per_resolve_class(live_bridge):
    client, *_ = live_bridge
    timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()

    before = client.health()["calls_served"]
    for _ in range(5):
        assert getattr(timeline, "GetName", None) is not None
    after = client.health()["calls_served"]

    # Five probes of the same class/method must not be five round trips.
    assert after - before <= 1


def test_private_attributes_are_never_forwarded(live_bridge):
    """A loopback listener that could reach dunder internals would be a far
    bigger surface than the Resolve API it's meant to expose."""
    client, *_ = live_bridge
    timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()

    # The proxy refuses client-side...
    with pytest.raises(AttributeError):
        timeline._secret

    # ...and the server refuses it too, for anything bypassing the proxy.
    for forbidden in ("_hidden", "__class__", "__dict__"):
        with pytest.raises(bridge_client.BridgeCallError, match="private"):
            client._request({"op": "call", "ref": 0, "method": forbidden, "args": []})


# ---------------------------------------------------------------------------
# Auth and failure modes
# ---------------------------------------------------------------------------

def test_a_wrong_token_is_rejected(live_bridge):
    _client, _resolve, _project, _timeline, _state, handshake = live_bridge
    impostor = bridge_client.BridgeClient(
        {**handshake, "token": "0" * 64}, socket_timeout=10.0,
    )
    try:
        with pytest.raises(bridge_client.BridgeUnavailable, match="rejected our token"):
            impostor.health()
    finally:
        impostor.close()


def test_a_failing_resolve_call_is_reported_not_swallowed(live_bridge):
    client, _resolve, project, _timeline, _state, _hs = live_bridge

    def boom():
        raise ValueError("Resolve said no")

    project.GetName = boom
    proxy_project = client.root().GetProjectManager().GetCurrentProject()

    with pytest.raises(bridge_client.BridgeCallError, match="Resolve said no"):
        proxy_project.GetName()


def test_a_dead_bridge_reports_clearly_rather_than_hanging(live_bridge):
    client, _resolve, _project, _timeline, _state, handshake = live_bridge
    # Point a client at a port with nothing listening -- what a quit Resolve
    # looks like when a stale handshake file is left behind.
    orphan = bridge_client.BridgeClient(
        {**handshake, "port": 1}, socket_timeout=3.0,
    )
    try:
        with pytest.raises(bridge_client.BridgeUnavailable, match="Theodore Bridge"):
            orphan.health()
    finally:
        orphan.close()


def test_an_unknown_handle_is_reported_as_such(live_bridge):
    client, *_ = live_bridge
    with pytest.raises(bridge_client.BridgeCallError, match="Unknown object handle"):
        client.call(999999, "GetName", (), {})


def test_release_frees_handles_and_the_root_is_never_released(live_bridge):
    client, *_ = live_bridge
    timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    assert timeline.GetName() == "Interview v1"

    before = client.health()["handles"]
    client.release([timeline._ref])
    assert client.health()["handles"] == before - 1

    client.release([proto.ROOT_REF])
    # The resolve object itself must survive, or the whole session dies.
    assert client.root().GetProjectManager() is not None


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

def test_the_socket_has_nagle_disabled(live_bridge):
    """Regression guard for a measured 88x slowdown.

    Without TCP_NODELAY, Nagle's algorithm on these small JSON bodies meets
    the peer's delayed ACK and every call costs ~40ms of pure waiting --
    measured at 44.1 ms/call before this, 0.5 ms/call after. A single
    timeline operation makes hundreds of calls, so this is the difference
    between the bridge feeling instant and feeling broken. Asserting the
    socket option rather than a wall-clock time keeps it reliable in CI.
    """
    import socket as _socket

    client, *_ = live_bridge
    client.health()  # force the connection open

    sock = client._conn.sock
    assert sock.getsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY) != 0


def test_many_calls_reuse_one_connection(live_bridge):
    """Keep-alive, not a fresh socket per call."""
    client, *_ = live_bridge
    timeline = client.root().GetProjectManager().GetCurrentProject().GetCurrentTimeline()

    first_socket = client._conn.sock
    for _ in range(25):
        assert timeline.GetName() == "Interview v1"
    assert client._conn.sock is first_socket


# ---------------------------------------------------------------------------
# The single-worker-thread guarantee
# ---------------------------------------------------------------------------

def test_every_resolve_call_is_serialized_onto_one_thread(live_bridge):
    """The reason the bridge can be shared at all.

    Resolve's API is not thread-safe, so concurrent clients must not produce
    concurrent API calls. This asserts both that no two calls overlap, and
    that they all run on the same single worker thread.
    """
    client, _resolve, project, _timeline, _state, handshake = live_bridge

    in_flight = 0
    max_in_flight = 0
    threads_seen = set()
    guard = threading.Lock()

    def observed_get_name():
        nonlocal in_flight, max_in_flight
        with guard:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            threads_seen.add(threading.current_thread().name)
        time.sleep(0.02)
        with guard:
            in_flight -= 1
        return "Test Project"

    project.GetName = observed_get_name

    clients = [bridge_client.BridgeClient(handshake, socket_timeout=20.0) for _ in range(6)]
    errors = []

    def hammer(c):
        try:
            proj = c.root().GetProjectManager().GetCurrentProject()
            for _ in range(5):
                assert proj.GetName() == "Test Project"
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(40)
    for c in clients:
        c.close()

    assert errors == []
    assert max_in_flight == 1, f"{max_in_flight} Resolve calls overlapped"
    assert threads_seen == {"theodore-resolve-api"}, threads_seen


def test_a_hung_resolve_call_times_out_instead_of_blocking_forever(tmp_path, monkeypatch):
    """Resolve API calls can block indefinitely when its IPC is wedged. A
    client waiting on silence forever is the worst failure mode available."""
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "home"))
    resolve_obj, project, _timeline = _build_fake_resolve()

    never_returns = threading.Event()
    project.GetName = lambda: never_returns.wait(60) or "never"

    server, _port, _state, handshake_path = bridge_module.start_bridge(
        resolve_obj, {"callTimeoutSeconds": 0.5},
    )
    handshake = json.loads(Path(handshake_path).read_text())
    client = bridge_client.BridgeClient(handshake, socket_timeout=20.0)
    try:
        proxy_project = client.root().GetProjectManager().GetCurrentProject()
        with pytest.raises(bridge_client.BridgeCallError, match="did not answer"):
            proxy_project.GetName()
    finally:
        never_returns.set()
        client.close()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Real Theodore code over the bridge
# ---------------------------------------------------------------------------

def test_markers_module_writes_through_the_bridge_unchanged(live_bridge):
    """The payoff: a real Theodore module, not written for the bridge,
    driving a proxy with no idea it isn't a Resolve object."""
    client, _resolve, _project, timeline, _state, _hs = live_bridge
    from theodore.resolve import markers as resolve_markers
    from theodore.resolve.connection import ResolveHandles

    pm = client.root().GetProjectManager()
    project = pm.GetCurrentProject()
    # The same ResolveHandles every CLI command builds -- just filled with
    # proxies instead of PyRemoteObjects.
    handles = ResolveHandles(
        resolve=client.root(), project_manager=pm,
        project=project, timeline=project.GetCurrentTimeline(),
    )

    plans = [
        resolve_markers.MarkerPlan(
            absolute_frame=86400 + 240, color="Blue", name="q01",
            note="A strong answer", duration_frames=1,
            custom_data={"theodore": True, "segment_id": "haylee.q01"},
            segment_id="haylee.q01",
        ),
    ]
    result = resolve_markers.write_markers(handles, plans)

    assert result["written"] == 1
    # Landed in the real underlying fake, at the right *relative* frame --
    # so the int-keyed GetMarkers() read and the AddMarker write both
    # survived the round trip.
    assert 240 in timeline.markers
    assert timeline.markers[240]["color"] == "Blue"


def test_script_runs_when_resolve_injects_the_global_without_main_name(tmp_path, monkeypatch):
    """Regression: Resolve doesn't promise __name__ == "__main__", so the
    script must start when it sees the injected `resolve` global instead.
    Before this, clicking the menu item did nothing at all."""
    import runpy

    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("THEODORE_BRIDGE_NO_BLOCK", "1")
    resolve_obj, _p, _t = _build_fake_resolve()
    runpy.run_path(str(BRIDGE_SCRIPT), run_name="not_main",
                        init_globals={"resolve": resolve_obj,
                                      "blockMainThread": False})
    assert (tmp_path / "h" / "bridge.json").exists()


def test_importing_the_script_without_resolve_has_no_side_effects(tmp_path, monkeypatch):
    import runpy

    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "h2"))
    runpy.run_path(str(BRIDGE_SCRIPT), run_name="not_main")
    assert not (tmp_path / "h2" / "bridge.json").exists()


def test_script_runs_when_exec_d_without___file__(tmp_path, monkeypatch):
    """The Console fallback: exec(open(path).read()) has no __file__."""
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "h3"))
    monkeypatch.setenv("THEODORE_BRIDGE_NO_BLOCK", "1")
    resolve_obj, _p, _t = _build_fake_resolve()
    namespace = {"resolve": resolve_obj, "__name__": "console"}
    exec(compile(BRIDGE_SCRIPT.read_text(), "bridge", "exec"), namespace)
    assert (tmp_path / "h3" / "bridge.json").exists()


def test_script_blocks_by_default_so_resolve_cannot_kill_the_listener(tmp_path, monkeypatch):
    """Measured on real Resolve 21: the listener dies when the script
    returns. main() must therefore hold open by default."""
    monkeypatch.setenv("THEODORE_BRIDGE_HOME", str(tmp_path / "h4"))
    monkeypatch.delenv("THEODORE_BRIDGE_NO_BLOCK", raising=False)
    resolve_obj, _p, _t = _build_fake_resolve()
    served = []

    class _Srv:
        def serve_forever(self):
            served.append(True)

    real_start = bridge_module.start_bridge
    def fake_start(r, c=None):
        server, port, state, hs = real_start(r, c)
        server.shutdown(); server.server_close()
        return _Srv(), port, state, hs
    monkeypatch.setattr(bridge_module, "start_bridge", fake_start)
    monkeypatch.setattr(bridge_module, "_show_result", lambda *a, **kw: None)
    monkeypatch.setattr(bridge_module, "resolve", resolve_obj, raising=False)
    bridge_module.main()
    assert served == [True]
