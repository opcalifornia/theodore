"""Theodore -- Start Bridge.

Run this once per Resolve session from **Workspace -> Scripts -> Theodore ->
Theodore Bridge**. It is the front door: after this, Theodore talks to
Resolve instantly, from anywhere, with no per-action reconnect.

What this is, and why it's shaped this way
------------------------------------------
Resolve's *external* scripting API (``DaVinciResolveScript.scriptapp()``) is
Studio-only -- since Resolve 19.1 the free edition refuses outside
connections outright. But a script launched from Workspace -> Scripts is
handed a live ``resolve`` object on *any* edition, inside Resolve's own
process.

Theodore's older Scripts-menu entries (Remove Silence, Cut Timeline,
Caption Timeline) threw that away: they used the injected ``resolve`` only
to read the project *name*, then shelled out to the ``theodore`` CLI, which
re-entered through the gated external door -- paying every cost of external
scripting (Studio-only, env vars, the External-scripting preference, a fresh
process handshake on every single click) for none of its benefit.

This script keeps the live object instead and re-exports it over a
loopback-only HTTP listener. Theodore connects to the listener. Nothing
outside Resolve ever imports ``DaVinciResolveScript`` again.

Three things make it safe to leave running:

1. **One worker thread owns every Resolve API call.** Resolve's API is not
   thread-safe, and the bridge is used concurrently (CLI, the Electron
   panel, future front doors). Every call is therefore queued onto a single
   dedicated thread and executed in arrival order, so no two API calls are
   ever in flight at once no matter how many clients are connected.
2. **It never blocks Resolve's UI.** The listener and the worker are daemon
   threads and ``main()`` returns immediately, so Resolve's own script
   runner is released the moment the bridge is up. (If a Resolve build ever
   tears down the interpreter when a script returns and kills the threads
   with it, set ``"blockMainThread": true`` in the config as a fallback --
   the bridge then holds the script open instead. Resolve's UI stays
   responsive either way, but that menu item won't "finish".)
3. **Loopback only, with a per-launch token.** This listener can drive the
   whole Resolve API, so it binds ``127.0.0.1`` exclusively, mints a fresh
   random token every launch, requires it on every request, and writes it to
   a handshake file only this user can read.

Dependency-free on purpose
--------------------------
This file imports nothing but the standard library and never imports
``theodore``. It runs inside Resolve's embedded Python interpreter, which
has none of Theodore's dependencies and must never be asked for them. The
wire protocol is intentionally duplicated from
``theodore/resolve/bridge_protocol.py`` rather than imported, and
``tests/test_bridge_parity.py`` fails the build if the two copies ever
disagree.

Setup
-----
1. Copy this file into Resolve's Scripts folder, in a ``Theodore``
   subfolder so it gets its own menu group:
   - Mac (all users): /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/
   - Mac (this user): ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/
   - Windows:         %PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility\\Theodore\\
   - Linux:           /opt/resolve/Fusion/Scripts/Utility/Theodore/
2. Fully quit Resolve (Cmd+Q -- it only scans the Scripts folder at launch)
   and reopen it.
3. Open a project, then run Workspace -> Scripts -> Theodore ->
   Theodore Bridge. A dialog confirms the port it came up on.

Config (optional) -- ``theodore-scripts-config.json`` next to this file:
``bridgePort`` (default 0 = let the OS pick a free port),
``blockMainThread`` (default false, see above),
``callTimeoutSeconds`` (default 120).
"""
import hmac
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Wire protocol. Mirrors theodore/resolve/bridge_protocol.py; kept in
# --- sync by tests/test_bridge_parity.py.
PROTOCOL_VERSION = 1
REF_KEY = "__ref__"
TYPE_KEY = "__type__"
MAP_KEY = "__map__"
ROOT_REF = 0

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "theodore-scripts-config.json")

DEFAULT_CALL_TIMEOUT = 120.0

#: Refuse to grow the handle table without bound. A client that never
#: releases is a bug; failing loudly beats leaking until Resolve is starved.
MAX_HANDLES = 100000


def _load_config():
    try:
        with open(CONFIG_PATH, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # No config is the normal case -- every setting has a default.
        return {}


def _bridge_home():
    override = os.environ.get("THEODORE_BRIDGE_HOME")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".theodore")


def _handshake_path():
    return os.path.join(_bridge_home(), "bridge.json")


# ---------------------------------------------------------------------------
# Handle table
# ---------------------------------------------------------------------------

class HandleTable:
    """Maps int ids to live Resolve objects.

    Holds a **strong reference** to everything it hands out. That is load
    bearing: Resolve returns a fresh wrapper object per call, and without a
    strong reference one could be garbage collected while the client still
    holds its id -- after which ``id()`` may be recycled by an unrelated
    object and a later call would land on the wrong thing.
    """

    def __init__(self, root):
        self._lock = threading.Lock()
        self._by_id = {ROOT_REF: root}
        self._id_by_identity = {id(root): ROOT_REF}
        self._next_id = 1

    def register(self, obj):
        with self._lock:
            existing = self._id_by_identity.get(id(obj))
            if existing is not None and self._by_id.get(existing) is obj:
                return existing
            if len(self._by_id) >= MAX_HANDLES:
                raise RuntimeError(
                    "Theodore bridge handle table is full (%d objects). A client "
                    "is not releasing handles; restart the bridge." % MAX_HANDLES
                )
            handle = self._next_id
            self._next_id += 1
            self._by_id[handle] = obj
            self._id_by_identity[id(obj)] = handle
            return handle

    def lookup(self, handle):
        with self._lock:
            try:
                return self._by_id[handle]
            except KeyError:
                raise KeyError(
                    "Unknown object handle %r. It was probably released, or "
                    "belongs to an earlier bridge session." % (handle,)
                )

    def release(self, handles):
        with self._lock:
            for handle in handles:
                if handle == ROOT_REF:
                    continue  # the resolve object itself is never released
                obj = self._by_id.pop(handle, None)
                if obj is not None:
                    self._id_by_identity.pop(id(obj), None)

    def count(self):
        with self._lock:
            return len(self._by_id)


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------

def encode_value(value, register_ref):
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(v, register_ref) for v in value]
    if isinstance(value, dict):
        # Pair list rather than a JSON object: Resolve hands back dicts keyed
        # by int (Timeline.GetMarkers() is {frame: {...}}) and Theodore's
        # marker writer indexes them with ints. Ordinary JSON would stringify
        # every key and break that silently.
        return {MAP_KEY: [
            [encode_value(k, register_ref), encode_value(v, register_ref)]
            for k, v in value.items()
        ]}
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return {REF_KEY: register_ref(value), TYPE_KEY: type(value).__name__}


def decode_value(value, resolve_ref):
    if isinstance(value, list):
        return [decode_value(v, resolve_ref) for v in value]
    if isinstance(value, dict):
        if REF_KEY in value:
            return resolve_ref(value[REF_KEY], value.get(TYPE_KEY))
        if MAP_KEY in value:
            return dict(
                (decode_value(k, resolve_ref), decode_value(v, resolve_ref))
                for k, v in value[MAP_KEY]
            )
        return dict((k, decode_value(v, resolve_ref)) for k, v in value.items())
    return value


# ---------------------------------------------------------------------------
# The single-threaded API worker
# ---------------------------------------------------------------------------

class ApiWorker:
    """Serializes every Resolve API call onto one dedicated thread.

    Resolve's API is not thread-safe and the bridge is deliberately
    multi-client, so this is the whole reason the bridge can be shared. The
    HTTP server stays multi-threaded (so one slow call can't stop the socket
    being read), but the actual API calls queue up here and run strictly one
    at a time, in arrival order.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="theodore-resolve-api", daemon=True,
        )
        self._thread.start()

    def _run(self):
        while True:
            job = self._queue.get()
            if job is None:
                return
            fn, box, done = job
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 -- relayed to the client
                box["error"] = exc
                box["traceback"] = traceback.format_exc()
            finally:
                done.set()

    def submit(self, fn, timeout):
        """Run ``fn`` on the worker thread and return its value.

        Raises ``TimeoutError`` rather than hanging forever: Resolve API
        calls can block indefinitely when its internal IPC is wedged, and a
        client waiting on a dead socket with no feedback is the worst
        failure mode of the lot.
        """
        box = {}
        done = threading.Event()
        self._queue.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError(
                "Resolve did not answer within %.0fs. It may be busy "
                "(rendering, loading media) or its scripting IPC may be "
                "wedged -- try again, and restart Resolve if it persists." % timeout
            )
        if "error" in box:
            raise box["error"]
        return box["value"]

    def queue_depth(self):
        return self._queue.qsize()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class BridgeState:
    def __init__(self, resolve_obj, token, call_timeout):
        self.resolve = resolve_obj
        self.handles = HandleTable(resolve_obj)
        self.worker = ApiWorker()
        self.token = token
        self.call_timeout = call_timeout
        self.calls_served = 0
        # Captured once at startup so a health check never needs the worker
        # (and so health still answers while a long call is in flight).
        self.product_name = self._safe(lambda: resolve_obj.GetProductName())
        self.version_string = self._safe(lambda: resolve_obj.GetVersionString())

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 -- informational only
            return None

    def is_studio(self):
        name = (self.product_name or "")
        return "studio" in name.lower()


def _make_handler(state):

    def lookup_ref(handle, type_name=None):
        """Codec adapter: ``decode_value`` passes the wire type name along,
        which the server has no use for -- it already holds the real object."""
        return state.handles.lookup(handle)

    class BridgeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "TheodoreBridge/1"
        #: Without this, Nagle's algorithm on the server's small JSON
        #: replies interacts with the client's delayed ACK and every call
        #: eats roughly 40ms of pure waiting. A single timeline operation
        #: makes hundreds of calls, so that is the difference between the
        #: bridge feeling instant and feeling broken.
        disable_nagle_algorithm = True

        def log_message(self, fmt, *args):
            # BaseHTTPRequestHandler logs every request to stderr, which in
            # Resolve means spamming its Console window. Silence by default.
            if os.environ.get("THEODORE_BRIDGE_VERBOSE"):
                sys.stderr.write("[Theodore bridge] " + (fmt % args) + "\n")

        # -- helpers ----------------------------------------------------
        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            # Constant-time: this token is the only thing standing between a
            # local process and full control of the user's Resolve.
            return hmac.compare_digest(header[len(prefix):], state.token)

        # -- routes -----------------------------------------------------
        def do_POST(self):  # noqa: N802 -- BaseHTTPRequestHandler's contract
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "bad or missing token"})
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError) as exc:
                self._send_json(400, {"ok": False, "error": "bad request body: %s" % exc})
                return

            try:
                self._send_json(200, {"ok": True, "value": self._dispatch(request)})
            except TimeoutError as exc:
                self._send_json(200, {"ok": False, "error": str(exc), "kind": "timeout"})
            except KeyError as exc:
                self._send_json(200, {"ok": False, "error": str(exc.args[0] if exc.args else exc),
                                      "kind": "unknown_handle"})
            except Exception as exc:  # noqa: BLE001 -- relayed, never fatal
                self._send_json(200, {
                    "ok": False,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "kind": "call_failed",
                    "traceback": traceback.format_exc(),
                })

        def _dispatch(self, request):
            op = request.get("op")
            if op == "health":
                return self._op_health()
            if op == "call":
                return self._op_call(request)
            if op == "probe":
                return self._op_probe(request)
            if op == "release":
                state.handles.release(request.get("refs") or [])
                return {"released": True}
            raise ValueError("unknown op %r" % (op,))

        def _op_health(self):
            # Deliberately answers from state captured at startup, so it
            # stays responsive (and useful for diagnosing a stuck call)
            # even while the worker is busy.
            return {
                "protocol": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "product": state.product_name,
                "version": state.version_string,
                "studio": state.is_studio(),
                "handles": state.handles.count(),
                "queue_depth": state.worker.queue_depth(),
                "calls_served": state.calls_served,
            }

        def _op_probe(self, request):
            """Does this object actually have this method?

            Answers with the *identical* check Theodore makes today against a
            real Resolve object -- ``getattr(obj, name, None) is not None``.
            Doing the probe here rather than assuming success client-side is
            what keeps ``builder.py``'s "couldn't read" vs. "read it, it's
            empty" distinction intact over the bridge.

            Runs on the worker thread like any other API touch: on a real
            PyRemoteObject even attribute lookup crosses Resolve's IPC.
            """
            target = state.handles.lookup(request.get("ref", ROOT_REF))
            names = request.get("names") or []

            def invoke():
                return {n: getattr(target, n, None) is not None for n in names}

            return {MAP_KEY: [[n, ok] for n, ok in
                              state.worker.submit(invoke, state.call_timeout).items()]}

        def _op_call(self, request):
            handle = request.get("ref", ROOT_REF)
            method_name = request.get("method")
            if not isinstance(method_name, str) or not method_name:
                raise ValueError("'method' must be a non-empty string")
            if method_name.startswith("_"):
                # Nothing in Resolve's API is underscore-prefixed; refusing
                # them keeps the listener from being a general-purpose
                # reach into this interpreter's internals.
                raise ValueError("refusing private attribute %r" % method_name)

            target = state.handles.lookup(handle)
            args = decode_value(request.get("args") or [], lookup_ref)
            kwargs = decode_value(request.get("kwargs") or {}, lookup_ref)

            def invoke():
                method = getattr(target, method_name, None)
                if method is None:
                    raise AttributeError(
                        "%s has no method %r" % (type(target).__name__, method_name)
                    )
                return method(*args, **kwargs)

            result = state.worker.submit(invoke, state.call_timeout)
            state.calls_served += 1
            return encode_value(result, state.handles.register)

    return BridgeHandler


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def write_handshake(port, token, state):
    """Publish where the bridge is, readable only by this user.

    Written last, once the listener is actually accepting connections, so a
    handshake file existing always means a bridge was really up.
    """
    home = _bridge_home()
    os.makedirs(home, exist_ok=True)
    path = _handshake_path()
    payload = {
        "protocol": PROTOCOL_VERSION,
        "host": "127.0.0.1",
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "product": state.product_name,
        "version": state.version_string,
        "studio": state.is_studio(),
    }
    # Create with 0600 from the outset rather than chmod-ing after: the
    # token must never be briefly world-readable on a shared machine.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        handle = os.fdopen(fd, "w")
    except Exception:
        os.close(fd)  # fdopen didn't take ownership, so we still own it
        raise
    with handle as fh:
        json.dump(payload, fh)
    try:
        os.chmod(path, 0o600)  # in case the file already existed, 0o600 above is ignored
    except OSError:
        pass
    return path


def start_bridge(resolve_obj, config=None):
    """Bring the bridge up. Returns ``(server, port, state, handshake_path)``.

    Split out from ``main()`` with no Resolve dependency of its own so the
    whole server can be exercised in tests against a fake ``resolve``.
    """
    config = config or {}
    token = secrets.token_hex(32)
    call_timeout = float(config.get("callTimeoutSeconds") or DEFAULT_CALL_TIMEOUT)
    state = BridgeState(resolve_obj, token, call_timeout)

    requested_port = int(config.get("bridgePort") or 0)
    # Port 0 lets the OS pick a free one, which is why the handshake file
    # exists -- no fixed port to collide with anything.
    server = ThreadingHTTPServer(("127.0.0.1", requested_port), _make_handler(state))
    server.daemon_threads = True
    port = server.server_address[1]

    threading.Thread(
        target=server.serve_forever, name="theodore-bridge-http", daemon=True,
    ).start()

    handshake = write_handshake(port, token, state)
    return server, port, state, handshake


def _show_result(title, message):
    print("[Theodore] %s\n%s" % (title, message))
    if sys.platform == "darwin":
        escaped = message.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'display dialog "%s" with title "Theodore -- %s" '
            'buttons {"OK"} default button "OK"' % (escaped, title)
        )
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass  # the console line above already carries the result


def main():
    try:
        resolve_obj = resolve  # noqa: F821 -- injected by Resolve
    except NameError:
        _show_result(
            "Not running inside Resolve",
            "This script must be run from Resolve's Workspace -> Scripts menu, "
            "which is what hands it a live connection.",
        )
        return

    config = _load_config()

    try:
        server, port, state, handshake = start_bridge(resolve_obj, config)
    except OSError as exc:
        _show_result(
            "Bridge could not start",
            "Could not open a local listener: %s\n\nIf a bridgePort is set in "
            "theodore-scripts-config.json, another process may be using it; "
            "remove it to let the OS pick a free port." % exc,
        )
        return

    edition = "Studio" if state.is_studio() else "free edition"
    _show_result(
        "Bridge running",
        "Theodore is connected to this Resolve.\n\n"
        "%s (%s)\n"
        "Listening on 127.0.0.1:%d\n"
        "Handshake: %s\n\n"
        "Leave this running -- Theodore will now reach Resolve instantly from "
        "the CLI and the panel, with no reconnect per action. It shuts down "
        "with Resolve." % (state.version_string or "Resolve", edition, port, handshake),
    )

    if config.get("blockMainThread"):
        # Fallback for any Resolve build that tears the interpreter down
        # when a script returns. Resolve's UI stays responsive, but this
        # menu item won't report as finished until Resolve quits.
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


# Resolve executes Scripts-menu files itself and does not promise
# __name__ == "__main__", so keying only on that left the script loading and
# doing nothing -- no dialog, no error. The reliable signal is the injected
# `resolve` global, which is exactly what main() needs anyway. Tests import
# this file with neither condition true, so importing stays side-effect free.
if __name__ == "__main__" or "resolve" in globals():
    main()
