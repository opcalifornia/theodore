"""Theodore -- Caption Timeline.

A DaVinci Resolve Scripts-menu entry point (Workspace -> Scripts -> Theodore
-> Caption Timeline), the same mechanism as this folder's `Remove Silence.py`
-- see that file's docstring for how and why. This one runs
`theodore caption-timeline --import-to-resolve` for whichever subject's
audio is on the open timeline: captions are timed against the real timeline
exactly as it is (an editor's own synced cut, or a duplicate Remove Silence
just produced), not a fresh Theodore-built assembly, and get imported
straight onto it.

Setup: identical to Remove Silence.py -- same config file
(theodore-scripts-config.json, shared by both scripts since they live in
the same folder), same install location. See resolve_scripts/README.md.

Tested the same way Remove Silence.py is
(tests/test_resolve_scripts_caption_timeline.py, via a fake `resolve`
global) -- same "what's verified vs. what needs a first real run" caveat
applies: Resolve-the-application's own script discovery and global
injection have not been run against the real app.
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

    cmd = [config["theodoreExecutable"], "caption-timeline", "--project", theodore_project, "--import-to-resolve"]
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
        _show_result("Caption Timeline failed", output.strip() or f"exit code {completed.returncode}")
    else:
        _show_result("Caption Timeline", output.strip() or "Done.")


main()
