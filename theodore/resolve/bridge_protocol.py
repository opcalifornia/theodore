"""Wire protocol for the Theodore <-> DaVinci Resolve bridge.

Why a bridge exists at all
--------------------------
Resolve's *external* scripting API (``DaVinciResolveScript.scriptapp()``) is
Studio-only -- since Resolve 19.1 the free edition refuses outside
connections entirely. But a script launched from **Workspace -> Scripts** is
handed a live ``resolve`` object on *any* edition, inside Resolve's own
process.

So the bridge inverts Theodore's old architecture. Instead of Theodore
reaching into Resolve from outside, a small script runs *inside* Resolve,
holds that live object, and re-exports it over a loopback HTTP listener.
Theodore then talks to the listener. Nothing outside Resolve ever imports
``DaVinciResolveScript``.

This module is the shared vocabulary for both ends. It is deliberately
**stdlib-only and dependency-free**: the in-Resolve half runs in Resolve's
embedded Python interpreter, which has none of Theodore's dependencies
installed and must never be asked to import them.

The object-handle model
-----------------------
Resolve's API is a graph of opaque objects, not data:
``resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()``.
None of those are JSON-serializable, and they have no stable identity we
could name.

So the server keeps a handle table and sends back a *reference* --
``{"__ref__": 7}`` -- for anything that isn't a plain value. The client wraps
each reference in a proxy whose method calls go back over the wire. The root
reference (``ROOT_REF``) is always the ``resolve`` object itself, so a client
needs no bootstrap call to get started.

References travel in both directions: a proxy passed as an argument (e.g.
``project.SetCurrentTimeline(timeline)``) is encoded back to its reference
and resolved to the real object server-side.

Why dicts get a special encoding
--------------------------------
JSON object keys are always strings, but Resolve hands back dicts keyed by
**int** -- ``Timeline.GetMarkers()`` is ``{frame: {...}}``. Theodore's marker
writer relies on that: ``resolve/markers.py`` does ``rel_frame in existing``
and ``existing[rel_frame]`` with an int ``rel_frame``. Round-tripping through
ordinary JSON would turn every key into a string, every ``in`` test would
quietly answer False, and marker de-duplication and frame-offsetting would
both silently stop working -- no error, just wrong output.

So dicts are encoded as an explicit key/value pair list (``MAP_KEY``) with
both sides recursively encoded, which preserves key types exactly. This is a
correctness requirement, not a nicety.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Bumped only on an incompatible wire change. The client refuses a
#: handshake whose version it doesn't recognise rather than guessing.
PROTOCOL_VERSION = 1

#: Marker key for an opaque Resolve object handle.
REF_KEY = "__ref__"

#: Server-side class name carried alongside every handle. Theodore probes
#: for optional Resolve methods with ``getattr(obj, name, None)`` and treats
#: None as "this build doesn't have it" -- ``assembly/builder.py`` relies on
#: telling that apart from "the call returned empty" when it verifies an
#: append. A proxy whose ``__getattr__`` always succeeded would quietly kill
#: that distinction, so the client answers probes for real; the type name is
#: what lets it cache the answer across every object of the same class
#: instead of a round trip per probe.
TYPE_KEY = "__type__"

#: Marker key for a dict, encoded as a pair list to preserve key types.
MAP_KEY = "__map__"

#: The handle id of the ``resolve`` object itself. Always present, never
#: released, so a fresh client can call straight away.
ROOT_REF = 0

#: Values that survive JSON unchanged. ``bool`` must precede ``int`` in any
#: isinstance chain built from this, since bool is a subclass of int.
PRIMITIVES = (type(None), bool, int, float, str)


def bridge_home() -> Path:
    """Directory holding the handshake file.

    Deliberately *not* inside the repo: the bridge runs inside Resolve,
    whose working directory is its own install location, and the client may
    be launched from anywhere. A fixed per-user path is the only thing both
    ends can agree on without configuration.
    """
    override = os.environ.get("THEODORE_BRIDGE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".theodore"


def handshake_path() -> Path:
    """The file the bridge writes on startup and the client reads to find it.

    Holds the port and the per-launch auth token, so the port never has to be
    fixed or guessed and a stale file is detectable.
    """
    return bridge_home() / "bridge.json"


def encode_value(value, register_ref):
    """Encode a value coming *out* of Resolve for the wire.

    ``register_ref`` is called with any object that isn't a plain value or
    container, and must return an int handle id for it.
    """
    # NOTE: bool before int -- bool is an int subclass.
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(v, register_ref) for v in value]
    if isinstance(value, dict):
        # Pair list, not a JSON object -- see the module docstring on why
        # int keys must survive.
        return {MAP_KEY: [
            [encode_value(k, register_ref), encode_value(v, register_ref)]
            for k, v in value.items()
        ]}
    if isinstance(value, (bytes, bytearray)):
        # Nothing in Resolve's API returns bytes today; decode rather than
        # fail if that ever changes.
        return value.decode("utf-8", "replace")
    return {REF_KEY: register_ref(value), TYPE_KEY: type(value).__name__}


def decode_value(value, resolve_ref):
    """Decode a wire value back into something usable on this side.

    ``resolve_ref`` is called as ``resolve_ref(handle_id, type_name)`` and
    must return whatever this end wants to represent that object: the real
    Resolve object on the server, a proxy on the client.
    """
    if isinstance(value, list):
        return [decode_value(v, resolve_ref) for v in value]
    if isinstance(value, dict):
        if REF_KEY in value:
            return resolve_ref(value[REF_KEY], value.get(TYPE_KEY))
        if MAP_KEY in value:
            return {
                decode_value(k, resolve_ref): decode_value(v, resolve_ref)
                for k, v in value[MAP_KEY]
            }
        # A bare JSON object isn't something either end produces; pass the
        # values through rather than silently dropping it.
        return {k: decode_value(v, resolve_ref) for k, v in value.items()}
    return value
