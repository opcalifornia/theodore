"""DaVinci Resolve Studio scripting API connection, with the guard rails the
spec calls out explicitly:

  - The scripting bridge (DaVinciResolveScript) is Studio-only -- the free
    edition doesn't expose it. There is no free-edition fallback here.
  - Resolve must be running with a project open. scriptapp("Resolve")
    returns None otherwise.
  - Resolve's Preferences -> System -> General -> External scripting using
    setting must allow it.

Every failure mode gets a specific, human-readable message instead of a
stack trace -- these are the errors a non-technical editor will actually see.
"""
from __future__ import annotations

from dataclasses import dataclass


class ResolveConnectionError(RuntimeError):
    """Resolve can't be reached, or is reachable but not in a usable state."""


@dataclass
class ResolveHandles:
    resolve: object
    project_manager: object
    project: object
    timeline: object


def _import_scripting_module():
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore
    except ImportError as exc:
        raise ResolveConnectionError(
            "Could not import DaVinciResolveScript. This usually means the "
            "RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH environment "
            "variables aren't set, or DaVinci Resolve Studio isn't installed. "
            "Run ./setup.sh, open a NEW terminal, and try again.\n"
            f"(underlying error: {exc})"
        ) from exc
    return dvr_script


def connect() -> ResolveHandles:
    """Connect to a running DaVinci Resolve Studio instance.

    Requires Resolve to already be running with a project and a timeline
    open -- Theodore never launches Resolve itself and never guesses which
    project/timeline to use.
    """
    dvr_script = _import_scripting_module()

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveConnectionError(
            "Resolve returned no connection. This means one of:\n"
            "  1. DaVinci Resolve is not running -- start it and open a project.\n"
            "  2. External scripting is disabled -- in Resolve, check "
            "Preferences -> System -> General -> 'External scripting using', "
            "and set it to 'Local' (or 'Network' if scripting from another "
            "machine).\n"
            "  3. You're running the free edition of Resolve. Theodore "
            "requires DaVinci Resolve STUDIO -- the external scripting API "
            "is not available in the free edition."
        )

    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        raise ResolveConnectionError(
            "Connected to Resolve but could not get the Project Manager. "
            "Try restarting Resolve."
        )

    project = project_manager.GetCurrentProject()
    if project is None:
        raise ResolveConnectionError(
            "Resolve is running, but no project is open. Open the project "
            "you want to write markers into, then try again."
        )

    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveConnectionError(
            f"Project '{project.GetName()}' is open, but it has no current "
            "timeline. Open (or create) a timeline in the Edit page, then "
            "try again."
        )

    return ResolveHandles(
        resolve=resolve,
        project_manager=project_manager,
        project=project,
        timeline=timeline,
    )


def describe_timeline(handles: ResolveHandles) -> str:
    tl = handles.timeline
    fps = tl.GetSetting("timelineFrameRate")
    return f"'{tl.GetName()}' -- {fps} fps, start TC {tl.GetStartTimecode()}"
