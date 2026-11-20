#!/usr/bin/env python3
"""Verify the Theodore <-> Resolve bridge against a REAL DaVinci Resolve.

Everything in ``tests/`` runs the bridge end to end over real HTTP, but
against ``tests/fake_resolve.py`` -- because the development sandbox has no
Resolve in it. This script covers what only real hardware can:

  * Does Resolve really inject ``resolve`` into a Scripts-menu script?
  * Does the listener thread really survive after that script returns?
    (The one genuinely load-bearing unknown in the whole design.)
  * Does Resolve's own API behave the way the proxies assume -- in
    particular, are ``GetMarkers()`` keys really ints?
  * How fast is a bridge call in practice?

Safe by default: every check is read-only. ``--write-markers`` opts into the
only write, and even that happens on a *duplicate* timeline which it then
leaves for you to inspect -- it never touches your real edit.

Usage
-----
    # In Resolve first: Workspace -> Scripts -> Theodore -> Theodore Bridge
    python3 scripts/live_smoke_test.py
    python3 scripts/live_smoke_test.py --write-markers
    python3 scripts/live_smoke_test.py --compare-external
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback
from pathlib import Path

# Run from a clone without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from theodore.resolve import bridge_client, bridge_protocol as proto  # noqa: E402
from theodore.resolve import connection, paths  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_results: list = []


def check(name):
    """Decorator turning a function into a reported pass/fail check."""
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                detail = fn(*args, **kwargs)
            except _Skip as exc:
                _results.append((SKIP, name, str(exc)))
                print(f"  {SKIP}  {name}: {exc}")
                return None
            except Exception as exc:  # noqa: BLE001 -- this is the report
                _results.append((FAIL, name, f"{type(exc).__name__}: {exc}"))
                print(f"  {FAIL}  {name}: {type(exc).__name__}: {exc}")
                if "--traceback" in sys.argv:
                    traceback.print_exc()
                return None
            _results.append((PASS, name, detail or ""))
            print(f"  {PASS}  {name}" + (f": {detail}" if detail else ""))
            return detail
        return run
    return wrap


class _Skip(Exception):
    """Not applicable on this machine -- not a failure."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

@check("Resolve install located")
def check_install():
    found = paths.detect_scripting_paths()
    if found.complete:
        return f"scripting API at {found.api_dir}"
    # Not fatal: the bridge doesn't need the external API at all. This is
    # only informational, which is the whole point of the bridge.
    return "external scripting API not found (fine -- the bridge doesn't need it)"


@check("Scripts folder known, bridge script installed")
def check_script_installed():
    install_dir = paths.bridge_install_dir()
    if install_dir is None:
        raise RuntimeError("no known Scripts folder for this platform")
    script = install_dir / "Theodore Bridge.py"
    if not script.exists():
        raise RuntimeError(
            f"not installed at {script} -- run: theodore install-bridge"
        )
    return str(script)


@check("Handshake file present and owner-only")
def check_handshake():
    handshake = bridge_client.read_handshake()
    if handshake is None:
        raise RuntimeError(
            f"no handshake at {proto.handshake_path()} -- start the bridge from "
            "Workspace -> Scripts -> Theodore -> Theodore Bridge"
        )
    mode = proto.handshake_path().stat().st_mode
    if mode & 0o077:
        raise RuntimeError(f"handshake is group/world readable (mode {oct(mode)})")
    return f"port {handshake['port']}, Resolve pid {handshake.get('pid')}"


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------

@check("Bridge listener alive AFTER the menu script returned")
def check_bridge_alive():
    """The critical unknown.

    The bridge starts its listener on a daemon thread and returns
    immediately so Resolve's UI is never blocked. That relies on Resolve's
    embedded interpreter outliving the script. If this check passes, that
    assumption holds on your Resolve build. If it fails while the handshake
    exists, set ``"blockMainThread": true`` in theodore-scripts-config.json
    and re-run the bridge.
    """
    client = bridge_client.connect_bridge()
    health = client.health()
    client.close()
    return (
        f"{health.get('product')} {health.get('version')}, "
        f"studio={health.get('studio')}, handles={health.get('handles')}"
    )


@check("connect() prefers the bridge")
def check_connect_route():
    handles = connection.connect()
    if handles.via != connection.BRIDGE:
        raise RuntimeError(f"connected via {handles.via}, expected the bridge")
    return f"project '{handles.project.GetName()}', timeline '{handles.timeline.GetName()}'"


@check("Object graph proxies correctly")
def check_object_graph():
    handles = connection.connect()
    fps = handles.timeline.GetSetting("timelineFrameRate")
    start = handles.timeline.GetStartFrame()
    if not isinstance(start, int):
        raise RuntimeError(f"GetStartFrame() returned {type(start).__name__}, expected int")
    return f"fps={fps}, startFrame={start}"


@check("GetMarkers() keys really are ints")
def check_marker_key_types():
    """Theodore's marker writer does ``rel_frame in existing`` with an int.
    If Resolve ever returned string keys, de-duplication would silently
    stop working -- so assert it against the real API, not just the fake.
    """
    handles = connection.connect()
    markers = handles.timeline.GetMarkers()
    if not markers:
        raise _Skip("no markers on this timeline to check (add one and re-run)")
    bad = [k for k in markers if not isinstance(k, int)]
    if bad:
        raise RuntimeError(f"non-int marker keys: {bad[:5]}")
    return f"{len(markers)} markers, all int-keyed"


@check("Track items read back as usable proxies")
def check_track_items():
    handles = connection.connect()
    fn = getattr(handles.timeline, "GetItemListInTrack", None)
    if fn is None:
        raise _Skip("this Resolve build has no GetItemListInTrack")
    items = fn("video", 1) or []
    if not items:
        raise _Skip("video track 1 is empty")
    first = items[0]
    return f"{len(items)} items; first '{first.GetName()}' @ {first.GetStart()}"


@check("Optional-method probing is truthful")
def check_probing():
    """``getattr(obj, name, None)`` must still answer None for a method
    Resolve genuinely doesn't have -- builder.py relies on it."""
    handles = connection.connect()
    if getattr(handles.timeline, "GetName", None) is None:
        raise RuntimeError("GetName probed as missing, but it exists")
    if getattr(handles.timeline, "TheodoreNotARealMethod", None) is not None:
        raise RuntimeError("a nonexistent method probed as present")
    return "present/absent both reported correctly"


@check("Concurrent clients are serialized, not interleaved")
def check_serialization():
    """Resolve's API is not thread-safe; the bridge's single worker thread
    is what makes sharing it safe. Hammer it from several clients at once."""
    handshake = bridge_client.read_handshake()
    clients = [bridge_client.BridgeClient(handshake) for _ in range(4)]
    errors: list = []

    def hammer(client):
        try:
            timeline = (client.root().GetProjectManager()
                        .GetCurrentProject().GetCurrentTimeline())
            for _ in range(10):
                if not timeline.GetName():
                    raise RuntimeError("empty timeline name")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(c,)) for c in clients]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    elapsed = time.time() - started
    for c in clients:
        c.close()

    if errors:
        raise RuntimeError(f"{len(errors)} client(s) failed, first: {errors[0]}")
    return f"40 calls across 4 clients in {elapsed:.2f}s"


@check("Call latency")
def check_latency():
    handles = connection.connect()
    timeline = handles.timeline
    timeline.GetName()  # warm the probe cache so this measures the call itself
    started = time.time()
    rounds = 50
    for _ in range(rounds):
        timeline.GetName()
    per_call = (time.time() - started) / rounds * 1000
    return f"{per_call:.1f} ms/call over the bridge"


@check("Bad token is rejected")
def check_auth():
    handshake = bridge_client.read_handshake()
    impostor = bridge_client.BridgeClient({**handshake, "token": "0" * 64})
    try:
        impostor.health()
    except bridge_client.BridgeUnavailable:
        return "rejected as expected"
    finally:
        impostor.close()
    raise RuntimeError("a wrong token was ACCEPTED -- this is a security problem")


# ---------------------------------------------------------------------------
# Optional: writes
# ---------------------------------------------------------------------------

@check("Marker write on a duplicate timeline")
def check_marker_write():
    """The only write, and it lands on a duplicate -- never your real edit.

    Mirrors how Theodore's trim engine already works: duplicate first,
    change the copy. The duplicate is left behind on purpose so you can see
    the result; delete it yourself when you're done.
    """
    from theodore.resolve import markers as resolve_markers

    handles = connection.connect()
    name = f"Theodore smoke test {time.strftime('%H%M%S')}"
    duplicate = handles.timeline.DuplicateTimeline(name)
    if not duplicate:
        raise RuntimeError("DuplicateTimeline() returned nothing")

    start = duplicate.GetStartFrame()
    plans = [resolve_markers.MarkerPlan(
        absolute_frame=start + 24, color="Blue", name="Theodore smoke test",
        note="Written by scripts/live_smoke_test.py", duration_frames=1,
        custom_data={"theodore": True, "smoke_test": True},
        segment_id="smoke.test",
    )]
    scoped = connection.ResolveHandles(
        resolve=handles.resolve, project_manager=handles.project_manager,
        project=handles.project, timeline=duplicate,
    )
    result = resolve_markers.write_markers(scoped, plans)
    if result["written"] != 1:
        raise RuntimeError(f"expected 1 marker written, got {result}")

    read_back = duplicate.GetMarkers() or {}
    if 24 not in read_back:
        raise RuntimeError(f"marker not found at relative frame 24; got {sorted(read_back)[:5]}")
    return f"wrote and read back 1 marker on '{name}' (left for you to inspect)"


# ---------------------------------------------------------------------------
# Optional: compare with external scripting
# ---------------------------------------------------------------------------

@check("External scripting fallback still works")
def check_external():
    import os
    os.environ["THEODORE_RESOLVE_TRANSPORT"] = connection.EXTERNAL
    try:
        handles = connection.connect()
        timeline = handles.timeline
        timeline.GetName()
        started = time.time()
        for _ in range(50):
            timeline.GetName()
        per_call = (time.time() - started) / 50 * 1000
        return f"works; {per_call:.1f} ms/call externally"
    finally:
        os.environ.pop("THEODORE_RESOLVE_TRANSPORT", None)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-markers", action="store_true",
                        help="Also write a marker on a DUPLICATE timeline (never your edit).")
    parser.add_argument("--compare-external", action="store_true",
                        help="Also exercise the external-scripting fallback and time it.")
    parser.add_argument("--traceback", action="store_true", help="Print tracebacks on failure.")
    args = parser.parse_args()

    print("Theodore bridge -- live smoke test")
    print("=" * 60)
    print("\nEnvironment")
    check_install()
    check_script_installed()
    check_handshake()

    print("\nBridge")
    check_bridge_alive()
    check_connect_route()
    check_object_graph()
    check_marker_key_types()
    check_track_items()
    check_probing()

    print("\nConcurrency and performance")
    check_serialization()
    check_latency()

    print("\nSecurity")
    check_auth()

    if args.write_markers:
        print("\nWrites (duplicate timeline only)")
        check_marker_write()

    if args.compare_external:
        print("\nFallback route")
        check_external()

    print("\n" + "=" * 60)
    passed = sum(1 for status, _, _ in _results if status == PASS)
    failed = [r for r in _results if r[0] == FAIL]
    skipped = sum(1 for status, _, _ in _results if status == SKIP)
    print(f"{passed} passed, {len(failed)} failed, {skipped} skipped")

    if failed:
        print("\nFailures:")
        for _status, name, detail in failed:
            print(f"  - {name}: {detail}")
        print(
            "\nIf 'Bridge listener alive' is the failure but the handshake exists, "
            "your Resolve build tears down the interpreter when a script returns. "
            'Set "blockMainThread": true in theodore-scripts-config.json and re-run '
            "the bridge from the Scripts menu."
        )
        return 1

    print("\nThe bridge is working on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
