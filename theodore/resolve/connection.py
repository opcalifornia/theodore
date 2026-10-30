"""Getting a usable handle on DaVinci Resolve, by whichever route works.

There are two ways to reach Resolve, and Theodore prefers them in this
order:

**1. The bridge (preferred).** A script run from Resolve's own
``Workspace -> Scripts`` menu is handed a live ``resolve`` object, on *any*
edition, inside Resolve's process. ``resolve_scripts/Theodore Bridge.py``
holds that object and re-exports it over a loopback listener;
``bridge_client`` connects to it. Connect once per Resolve session and every
action afterwards is an instant local call.

**2. External scripting (fallback).** The classic route:
``DaVinciResolveScript.scriptapp("Resolve")`` from outside Resolve.
Blackmagic gates this to Studio -- since Resolve 19.1 the free edition
refuses outside connections entirely -- and it needs environment variables
and the *External scripting using* preference set. It is kept because it
works with no setup step inside Resolve, which is genuinely more convenient
when it works.

Both routes produce the same ``ResolveHandles``, so nothing downstream knows
or cares which one it got. That is the whole point: ``assembly/builder.py``,
``resolve/markers.py`` and ``resolve/multicam.py`` only ever call methods on
these handles, and are already tested against a plain-Python stand-in
(``tests/fake_resolve.py``), so a remote proxy is just another stand-in.

Every failure mode gets a specific, actionable message rather than a stack
trace -- these are the errors a non-technical editor actually sees.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from theodore.resolve import bridge_client, paths

logger = logging.getLogger(__name__)

#: ``bridge`` | ``external`` | ``auto`` (default). Set
#: ``THEODORE_RESOLVE_TRANSPORT`` to pin one route, which is mostly useful
#: for proving which one a problem is actually in.
TRANSPORT_ENV = "THEODORE_RESOLVE_TRANSPORT"

BRIDGE = "bridge"
EXTERNAL = "external"


class ResolveConnectionError(RuntimeError):
    """Resolve can't be reached, or is reachable but not in a usable state."""


@dataclass
class ResolveHandles:
    resolve: object
    project_manager: object
    project: object
    timeline: object
    #: Which route produced these handles. Defaulted so the many test
    #: fixtures that build handles directly keep working untouched.
    via: Optional[str] = None
    #: The live bridge client, when ``via == BRIDGE``. Lets long-lived
    #: processes health-check without reconnecting.
    client: object = None
    #: What the bridge reported about this Resolve (version, edition).
    info: dict = field(default_factory=dict)

    @property
    def product(self) -> Optional[str]:
        return self.info.get("product")

    @property
    def version(self) -> Optional[str]:
        return self.info.get("version")

    @property
    def is_studio(self) -> bool:
        return bool(self.info.get("studio"))


def _requested_transport() -> str:
    value = (os.environ.get(TRANSPORT_ENV) or "auto").strip().lower()
    return value if value in (BRIDGE, EXTERNAL, "auto") else "auto"


# ---------------------------------------------------------------------------
# Route 1: the bridge
# ---------------------------------------------------------------------------

def _connect_via_bridge(require_project: bool = True, require_timeline: bool = True) -> ResolveHandles:
    client = bridge_client.connect_bridge()
    info = client.health()
    resolve_obj = client.root()

    project_manager = resolve_obj.GetProjectManager()
    if project_manager is None:
        raise ResolveConnectionError(
            "Reached Resolve through the bridge, but it returned no Project "
            "Manager. Restart Resolve, then re-run the bridge from "
            "Workspace -> Scripts -> Theodore -> Theodore Bridge."
        )

    project = project_manager.GetCurrentProject()
    if project is None and require_project:
        raise ResolveConnectionError(
            "Resolve is running and the bridge is connected, but no project is "
            "open. Open the project you want to work in, then try again."
        )

    timeline = project.GetCurrentTimeline() if project is not None else None
    if timeline is None and require_timeline:
        raise ResolveConnectionError(
            f"Project '{project.GetName()}' is open, but it has no current "
            "timeline. Open (or create) a timeline in the Edit page, then "
            "try again."
        )

    return ResolveHandles(
        resolve=resolve_obj,
        project_manager=project_manager,
        project=project,
        timeline=timeline,
        via=BRIDGE,
        client=client,
        info=info or {},
    )


# ---------------------------------------------------------------------------
# Route 2: external scripting
# ---------------------------------------------------------------------------

def _import_scripting_module():
    # Find the API ourselves first: a GUI-launched process (the Electron
    # panel, anything opened from the Dock) never sourced the shell profile
    # setup.sh writes to, so relying on the environment alone fails for a
    # large share of how Theodore actually gets started.
    detected = paths.ensure_importable()
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore
    except ImportError as exc:
        where = (
            f"\nLooked for the scripting API at: {detected.api_dir}"
            if detected.api_dir else
            "\nCould not find a DaVinci Resolve install in any standard location."
        )
        raise ResolveConnectionError(
            "Could not import DaVinciResolveScript, so Theodore cannot drive "
            "Resolve from outside it."
            + where +
            "\n\nThe better fix is the bridge, which needs none of this: in "
            "Resolve, run Workspace -> Scripts -> Theodore -> Theodore Bridge. "
            "It works on both Studio and the free edition.\n"
            f"(underlying error: {exc})"
        ) from exc
    return dvr_script


def _connect_via_external(require_project: bool = True, require_timeline: bool = True) -> ResolveHandles:
    dvr_script = _import_scripting_module()

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveConnectionError(
            "Resolve returned no connection over the external scripting API. "
            "That means one of:\n"
            "  1. Resolve isn't running -- start it and open a project.\n"
            "  2. External scripting is disabled -- in Resolve, open "
            "Preferences -> System -> General and set 'External scripting "
            "using' to 'Local'.\n"
            "  3. You're on the free edition, which refuses outside "
            "connections (Blackmagic gates this to Studio, and blocks it "
            "outright from Resolve 19.1).\n\n"
            "For (2) and (3) alike, use the bridge instead -- it runs inside "
            "Resolve, so neither limit applies: Workspace -> Scripts -> "
            "Theodore -> Theodore Bridge."
        )

    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise ResolveConnectionError(
            "Connected to Resolve but could not get the Project Manager. "
            "Try restarting Resolve."
        )

    project = project_manager.GetCurrentProject()
    if project is None and require_project:
        raise ResolveConnectionError(
            "Resolve is running, but no project is open. Open the project "
            "you want to write markers into, then try again."
        )

    timeline = project.GetCurrentTimeline() if project is not None else None
    if timeline is None and require_timeline:
        raise ResolveConnectionError(
            f"Project '{project.GetName()}' is open, but it has no current "
            "timeline. Open (or create) a timeline in the Edit page, then "
            "try again."
        )

    info = {}
    try:
        info = {
            "product": resolve.GetProductName(),
            "version": resolve.GetVersionString(),
        }
        info["studio"] = paths.is_studio(info.get("product"))
    except Exception:  # noqa: BLE001 -- informational only
        logger.debug("Could not read Resolve product/version.", exc_info=True)

    return ResolveHandles(
        resolve=resolve,
        project_manager=project_manager,
        project=project,
        timeline=timeline,
        via=EXTERNAL,
        info=info,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def connect(require_project: bool = True, require_timeline: bool = True) -> ResolveHandles:
    """Connect to a running Resolve, preferring the bridge.

    By default requires a project and timeline open. The agent passes False
    for both: it can open a project or create a timeline itself, so "nothing
    open yet" is a state to work from, not an error.
    Theodore never launches Resolve and never guesses which project to use.

    Re-reads the bridge handshake every call, so a Resolve that was restarted
    (with the bridge re-run in the new session) is picked up automatically
    with no stale state carried over.
    """
    transport = _requested_transport()

    if transport == EXTERNAL:
        return _connect_via_external(require_project, require_timeline)

    try:
        return _connect_via_bridge(require_project, require_timeline)
    except bridge_client.BridgeUnavailable as bridge_exc:
        if transport == BRIDGE:
            raise ResolveConnectionError(str(bridge_exc)) from bridge_exc
        logger.debug("Bridge unavailable, trying external scripting.", exc_info=True)
        bridge_reason = str(bridge_exc)
    except bridge_client.BridgeCallError as bridge_exc:
        # The bridge answered but the call failed -- that is a real problem
        # with this Resolve, not a reason to try the other route and report a
        # misleading error.
        raise ResolveConnectionError(
            f"Resolve's Theodore bridge is running but the call failed: {bridge_exc}"
        ) from bridge_exc

    try:
        return _connect_via_external(require_project, require_timeline)
    except ResolveConnectionError as external_exc:
        # Both routes failed. Lead with the bridge, since it is the one that
        # works regardless of edition and preference settings, and always
        # end on the one action that fixes the common case -- the fallback's
        # error could be anything, so the remedy can't be left to it.
        raise ResolveConnectionError(
            f"{bridge_reason}\n\n"
            "Falling back to external scripting also failed:\n"
            f"{external_exc}\n\n"
            "Start here: open Resolve, then run Workspace -> Scripts -> "
            "Theodore -> Theodore Bridge. That connects Theodore for the whole "
            "session and works on both Studio and the free edition."
        ) from external_exc


def probe() -> dict:
    """Describe the current connection without raising.

    For status displays (the Electron panel polls this) that want to show
    what's going on rather than handle an exception.
    """
    status = {
        "connected": False,
        "via": None,
        "project": None,
        "timeline": None,
        "product": None,
        "version": None,
        "studio": None,
        "bridge_running": bridge_client.read_handshake() is not None,
        "error": None,
    }
    try:
        handles = connect()
    except ResolveConnectionError as exc:
        status["error"] = str(exc)
        return status

    status.update({
        "connected": True,
        "via": handles.via,
        "product": handles.product,
        "version": handles.version,
        "studio": handles.is_studio,
    })
    try:
        status["project"] = handles.project.GetName()
        status["timeline"] = handles.timeline.GetName()
    except Exception as exc:  # noqa: BLE001 -- status must never raise
        status["error"] = f"connected, but reading names failed: {exc}"
    return status


def describe_timeline(handles: ResolveHandles) -> str:
    tl = handles.timeline
    fps = tl.GetSetting("timelineFrameRate")
    return f"'{tl.GetName()}' -- {fps} fps, start TC {tl.GetStartTimecode()}"
