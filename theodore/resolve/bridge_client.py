"""Client half of the Theodore <-> Resolve bridge.

Talks to the listener that ``resolve_scripts/Theodore Bridge.py`` runs inside
DaVinci Resolve, and hands back objects that behave like Resolve's own API
objects -- so every module Theodore already has (``assembly/builder.py``,
``resolve/markers.py``, ``resolve/multicam.py``) works over the bridge
without a line changed.

That interchangeability is not wishful thinking: the entire codebase is
already exercised against ``tests/fake_resolve.py``, a plain-Python
stand-in. Nothing downstream ever imports a Resolve type or checks one, it
only calls methods. A remote proxy is simply another stand-in.

Two details earn their complexity:

**Truthful attribute probing.** Theodore asks whether an optional method
exists with ``getattr(obj, name, None)`` and treats ``None`` as "this
Resolve build doesn't have it". ``assembly/builder.py:_read_track_items``
leans on telling that apart from "the call worked and the track is empty"
when it verifies that an append really landed. A proxy whose
``__getattr__`` handed back a callable for any name at all would silently
collapse those two cases, so instead the proxy asks the server to run the
identical check and caches the answer per Resolve class.

**Connection reuse.** The bridge speaks HTTP/1.1 keep-alive and a single
timeline operation can make hundreds of calls, so one socket is held open
rather than reconnecting per call. A dropped socket (Resolve restarted,
bridge stopped) is retried once and then reported as a clear, actionable
error rather than a stack trace.
"""
from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
from typing import Optional

from theodore.resolve import bridge_protocol as proto

logger = logging.getLogger(__name__)

#: Generous, because a single call can legitimately be slow (importing media,
#: duplicating a long timeline). The server applies its own per-call timeout;
#: this one only guards against a socket that never answers at all.
DEFAULT_SOCKET_TIMEOUT = 180.0

#: The bridge answers ``health`` from state captured at startup, without
#: touching Resolve, so a live bridge replies at once. Waiting the full
#: DEFAULT_SOCKET_TIMEOUT (twice, with the retry) for a frozen one left the
#: panel chat sitting on "..." for six minutes with no explanation.
HEALTH_TIMEOUT = 5.0


class BridgeUnavailable(RuntimeError):
    """No live bridge to talk to (never started, or Resolve has since quit)."""


class BridgeCallError(RuntimeError):
    """The bridge was reached, but the Resolve call itself failed."""


def read_handshake() -> Optional[dict]:
    """The bridge's published address, or None if it was never started.

    A handshake file alone doesn't prove a live bridge -- Resolve may have
    quit without cleaning up -- so callers must still health-check.
    """
    path = proto.handshake_path()
    try:
        raw = path.read_text()
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Bridge handshake at %s is not valid JSON; ignoring it.", path)
        return None
    if data.get("protocol") != proto.PROTOCOL_VERSION:
        logger.warning(
            "Bridge handshake at %s speaks protocol %r, this Theodore speaks %r. "
            "Restart the bridge from Resolve's Workspace -> Scripts menu.",
            path, data.get("protocol"), proto.PROTOCOL_VERSION,
        )
        return None
    if not data.get("port") or not data.get("token"):
        return None
    return data


class BridgeClient:
    """One keep-alive HTTP connection to the in-Resolve listener."""

    def __init__(self, handshake: dict, socket_timeout: float = DEFAULT_SOCKET_TIMEOUT):
        self._host = handshake.get("host") or "127.0.0.1"
        self._port = int(handshake["port"])
        self._token = handshake["token"]
        self._timeout = socket_timeout
        self.handshake = handshake
        # The bridge serializes Resolve calls onto one worker thread anyway,
        # but the socket itself is not safe to interleave, so requests are
        # serialized here too.
        self._lock = threading.Lock()
        self._conn: Optional[http.client.HTTPConnection] = None
        #: (resolve_class_name, method_name) -> bool
        self._probe_cache: dict = {}

    # -- transport ------------------------------------------------------
    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            conn = http.client.HTTPConnection(
                self._host, self._port, timeout=self._timeout,
            )
            # Connect eagerly so TCP_NODELAY can be set before any request.
            # Without it, Nagle's algorithm plus the server's delayed ACK
            # adds ~40ms to every single call -- and one timeline operation
            # makes hundreds, so it is the difference between the bridge
            # feeling instant and feeling broken.
            conn.connect()
            try:
                conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                logger.debug("Could not set TCP_NODELAY on the bridge socket.")
            self._conn = conn
        return self._conn

    def _drop_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 -- already discarding it
                pass
            self._conn = None

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        # One retry: a keep-alive socket idle since the last operation may
        # have been closed at the other end, which is not a real failure.
        for attempt in (1, 2):
            try:
                conn = self._connection()
                conn.request("POST", "/", body=body, headers=headers)
                response = conn.getresponse()
                raw = response.read()
            except (http.client.HTTPException, OSError, socket.timeout) as exc:
                self._drop_connection()
                if attempt == 1:
                    continue
                raise BridgeUnavailable(
                    "Lost the connection to Resolve's Theodore bridge "
                    f"({self._host}:{self._port}): {exc}\n\n"
                    "Resolve was probably closed or restarted. Re-run "
                    "Workspace -> Scripts -> Theodore -> Theodore Bridge."
                ) from exc

            if response.status == 401:
                self._drop_connection()
                raise BridgeUnavailable(
                    "Resolve's Theodore bridge rejected our token. A newer bridge "
                    "is running than the one we read -- re-run Theodore, or restart "
                    "the bridge from Workspace -> Scripts."
                )
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise BridgeCallError(
                    f"Bridge returned a non-JSON response ({response.status})."
                ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _request(self, payload: dict):
        # Serialized: a keep-alive HTTP connection cannot have two
        # request/response pairs interleaved on it.
        with self._lock:
            result = self._post(payload)
        if result.get("ok"):
            return result.get("value")
        error = result.get("error") or "unknown bridge error"
        if result.get("traceback"):
            logger.debug("Bridge-side traceback:\n%s", result["traceback"])
        raise BridgeCallError(error)

    # -- api ------------------------------------------------------------
    def health(self) -> dict:
        return self._request({"op": "health"})

    def call(self, ref: int, method: str, args: tuple, kwargs: dict):
        encoded_args = [self._encode(a) for a in args]
        encoded_kwargs = {k: self._encode(v) for k, v in (kwargs or {}).items()}
        value = self._request({
            "op": "call", "ref": ref, "method": method,
            "args": encoded_args, "kwargs": encoded_kwargs,
        })
        return proto.decode_value(value, self._wrap_ref)

    def has_method(self, ref: int, type_name: Optional[str], method: str) -> bool:
        """Whether this object really exposes ``method``.

        Cached by Resolve class name, so a given class is probed once per
        method per session rather than once per object.
        """
        cache_key = (type_name, method)
        if type_name is not None and cache_key in self._probe_cache:
            return self._probe_cache[cache_key]
        answers = proto.decode_value(
            self._request({"op": "probe", "ref": ref, "names": [method]}),
            self._wrap_ref,
        )
        present = bool(answers.get(method))
        if type_name is not None:
            self._probe_cache[cache_key] = present
        return present

    def release(self, refs) -> None:
        try:
            self._request({"op": "release", "refs": list(refs)})
        except (BridgeUnavailable, BridgeCallError):
            # Releasing is an optimisation; the whole table dies with the
            # bridge regardless, so never let it break a caller.
            logger.debug("Bridge release failed; ignoring.", exc_info=True)

    def close(self) -> None:
        self._drop_connection()

    # -- codec glue -----------------------------------------------------
    def _encode(self, value):
        """Encode an argument. A proxy becomes the reference it stands for,
        so ``project.SetCurrentTimeline(timeline)`` works."""
        if isinstance(value, ResolveProxy):
            return {proto.REF_KEY: value._ref, proto.TYPE_KEY: value._type}
        if isinstance(value, (list, tuple)):
            return [self._encode(v) for v in value]
        if isinstance(value, dict):
            return {proto.MAP_KEY: [
                [self._encode(k), self._encode(v)] for k, v in value.items()
            ]}
        return value

    def _wrap_ref(self, ref: int, type_name: Optional[str]):
        return ResolveProxy(self, ref, type_name)

    def root(self) -> "ResolveProxy":
        """The ``resolve`` object itself."""
        return ResolveProxy(self, proto.ROOT_REF, "Resolve")


class ResolveProxy:
    """Stands in for one Resolve API object.

    Attribute access returns a callable that forwards over the bridge.
    Unknown attributes raise ``AttributeError`` exactly as the real object
    would, which is what keeps ``getattr(obj, name, None)`` probing honest.
    """

    __slots__ = ("_client", "_ref", "_type")

    def __init__(self, client: BridgeClient, ref: int, type_name: Optional[str] = None):
        self._client = client
        self._ref = ref
        self._type = type_name

    def __getattr__(self, name: str):
        # __slots__ means real attributes never reach here, but dunder and
        # private lookups (copy, pickle, inspect) must not become API calls.
        if name.startswith("_"):
            raise AttributeError(name)
        if not self._client.has_method(self._ref, self._type, name):
            raise AttributeError(
                f"{self._type or 'Resolve object'} has no attribute {name!r}"
            )
        ref, client = self._ref, self._client

        def _invoke(*args, **kwargs):
            return client.call(ref, name, args, kwargs)

        _invoke.__name__ = name
        return _invoke

    def __repr__(self) -> str:
        return f"<ResolveProxy {self._type or '?'} ref={self._ref}>"

    def __eq__(self, other) -> bool:
        return isinstance(other, ResolveProxy) and other._ref == self._ref

    def __hash__(self) -> int:
        return hash(("ResolveProxy", self._ref))


def connect_bridge(socket_timeout: float = DEFAULT_SOCKET_TIMEOUT) -> BridgeClient:
    """Attach to a running bridge, proving it is alive before returning.

    Raises ``BridgeUnavailable`` if there is no bridge, which is the signal
    for ``connection.py`` to fall back to external scripting.
    """
    handshake = read_handshake()
    if handshake is None:
        raise BridgeUnavailable(
            "Resolve's Theodore bridge isn't running.\n\n"
            "In Resolve: Workspace -> Scripts -> Theodore -> Theodore Bridge. "
            "That connects Theodore to Resolve for the whole session -- you only "
            "do it once each time you open Resolve."
        )
    client = BridgeClient(handshake, socket_timeout=min(socket_timeout, HEALTH_TIMEOUT))
    try:
        client.health()  # a stale handshake file looks identical until we ask
    except BridgeCallError as exc:
        client.close()
        raise BridgeUnavailable(f"Bridge is running but unhealthy: {exc}") from exc
    except BridgeUnavailable as exc:
        client.close()
        if isinstance(exc.__cause__, socket.timeout):
            raise BridgeUnavailable(
                "Resolve's Theodore bridge is running but not answering -- it is "
                "frozen, usually after Resolve reloaded the project.\n\n"
                "Restart it: in Resolve, Workspace -> Scripts -> Theodore -> "
                "Theodore Bridge."
            ) from exc
        raise
    # Healthy: reconnect with the generous timeout real calls need.
    client._drop_connection()
    client._timeout = socket_timeout
    return client
