"""Theodore -- Remove Silence.

A DaVinci Resolve Scripts-menu entry point (Workspace -> Scripts -> Theodore
-> Remove Silence), NOT a Workflow Integration Plugin: this runs as a plain
Python script Resolve itself invokes, with `resolve` already provided as a
global -- no separate Electron window, no manual connection step, no click
to open a panel first. As close to "a real Resolve menu item" as any
third-party tool gets (this is the same mechanism FireCut and every other
Resolve script-menu tool use).

What it does: figures out the current project and (from what's actually on
the open timeline) which Theodore subject it is, then runs
`theodore remove-silence` for it -- the exact same tested code path the CLI
and the Electron panel's "Remove Silence" button both use. This script adds
NO new editing logic of its own; it only discovers what to run and shows
the result, so the risky part (actually cutting the timeline) is the one
piece of this whole project that already has full test coverage.

Setup
-----
1. Copy `theodore-scripts-config.example.json` (next to this file) to
   `theodore-scripts-config.json` and fill in `theodoreExecutable` (your
   theodore install's venv, e.g. /path/to/theodore/.venv/bin/theodore) and
   `theodoreDataDir` (the same data/ directory your CLI runs against).
   Gitignored on purpose -- these are local paths, not something to commit.
2. Copy this file (and its config) into DaVinci Resolve's Scripts folder,
   under a `Theodore` subfolder so it gets its own menu group:
   - Mac (all users):   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/
   - Mac (this user):   ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/
   - Windows:           %PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility\\Theodore\\
   - Linux:              /opt/resolve/Fusion/Scripts/Utility/Theodore/ (or the per-user equivalent)
   "Utility" makes it available on every page; use "Edit" instead if you
   only ever want it on the Edit page.
3. Restart Resolve. With a project and a timeline open, run it from
   Workspace -> Scripts -> Theodore -> Remove Silence.

Still genuinely unverified -- this repo's sandbox has no real Resolve
installed. Everything this script calls into (`theodore remove-silence`,
the dead-air-to-timeline-frame mapping, the trim engine) has full test
coverage against a fake Resolve API standing in for the real one, and this
script's own glue -- reading config, discovering the project from a fake
`resolve` global shaped the way Resolve's own docs describe, building the
subprocess call, surfacing a failure instead of a raw traceback -- is
ALSO covered (tests/test_resolve_scripts_remove_silence.py, run via
runpy with a fake `resolve` injected the same way Resolve itself injects
it). What's genuinely unverified is Resolve-the-application itself: that
it really does inject `resolve` this way, and that it really does list a
script placed here under Workspace -> Scripts. Report back what happens
the first time you run it for real.
"""
import json
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "theodore-scripts-config.json"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"No config at {CONFIG_PATH}. Copy theodore-scripts-config.example.json to "
            "theodore-scripts-config.json next to this script and fill in your paths."
        )
    return json.loads(CONFIG_PATH.read_text())


def _show_result(title: str, message: str) -> None:
    """Always prints to Resolve's own Console (Workspace -> Console) --
    that alone is enough to confirm the script ran. Also pops a native
    dialog on Mac, where this whole workflow is actually being used, so
    the result is visible without having to go looking for the console."""
    print(f"[Theodore] {title}\n{message}")
    if sys.platform == "darwin":
        escaped = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display dialog "{escaped}" with title "Theodore -- {title}" buttons {{"OK"}} default button "OK"'
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass  # The console line above already has the result either way.


def main() -> None:
    try:
        config = _load_config()
    except RuntimeError as exc:
        _show_result("Setup needed", str(exc))
        return

    try:
        project_manager = resolve.GetProjectManager()  # noqa: F821 -- injected by Resolve
        project = project_manager.GetCurrentProject() if project_manager else None
    except NameError:
        _show_result("Not running inside Resolve", "This script must be run from Resolve's Workspace -> Scripts menu.")
        return

    if project is None:
        _show_result("No project open", "Open a DaVinci Resolve project first.")
        return

    theodore_project = config.get("theodoreProject") or project.GetName()

    cmd = [config["theodoreExecutable"], "remove-silence", "--project", theodore_project]
    if config.get("silenceThreshold") is not None:
        cmd += ["--silence-threshold", str(config["silenceThreshold"])]
    if config.get("aggressive"):
        cmd.append("--aggressive")

    env = {**__import__("os").environ, "THEODORE_DATA_DIR": config["theodoreDataDir"]}
    try:
        completed = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        _show_result("Could not run theodore", f"{exc}\n\nCheck theodoreExecutable in {CONFIG_PATH.name}.")
        return

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        _show_result("Remove Silence failed", output.strip() or f"exit code {completed.returncode}")
    else:
        _show_result("Remove Silence", output.strip() or "Done.")


main()
