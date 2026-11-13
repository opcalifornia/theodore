"""Theodore -- Cut Timeline.

A DaVinci Resolve Scripts-menu entry point (Workspace -> Scripts -> Theodore
-> Cut Timeline), the same mechanism as this folder's `Remove Silence.py`
and `Caption Timeline.py` -- see `Remove Silence.py`'s docstring for how
and why. This one runs `theodore cut-timeline` for whichever subject's
audio is on the open timeline: keeps only the answers Theodore detected,
cuts interviewer questions, gaps between segments, and dead air/filler
inside a kept answer, all in one pass.

Unlike the other two scripts, this one is a genuine two-step flow, not a
single fire-and-forget: `theodore cut-timeline` previews by default (see
its own --help) rather than applying immediately, because it removes far
more than dead air alone -- an entire wrong preview applied unseen is a
much bigger mistake to walk back than a missed silence. So this script:

  1. Runs the PREVIEW pass and shows you the real numbers (segments kept/
     excluded, how much interviewer speech and dead air would go, in
     seconds) in a native macOS confirm dialog, with Cancel/Apply Cut
     buttons -- entirely inside Resolve, no panel, no terminal.
  2. Only if you click "Apply Cut" does it run the pass again with
     --apply, which is what actually builds the trimmed duplicate
     timeline. Clicking Cancel (or the dialog failing for any reason)
     never applies anything -- "couldn't ask" must never silently mean
     "yes".

This script intentionally does NOT expose --exclude (cutting a SPECIFIC
extra segment, like a weak retake) -- that varies every run and doesn't
belong baked into a static config file. Use the Electron panel's Cut
Timeline row, or the CLI directly, when you need that.

Setup: identical to Remove Silence.py -- same config file
(theodore-scripts-config.json, shared by every script in this folder),
same install location. See resolve_scripts/README.md.

Tested the same way the other two scripts are
(tests/test_resolve_scripts_cut_timeline.py, via a fake `resolve` global
and a stubbed `osascript`) -- same "what's verified vs. what needs a
first real run" caveat: Resolve's own script discovery and global
injection, and the real osascript dialog, have not run against the real
app.
"""
import json
import os
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


def _confirm(preview_text: str) -> bool:
    """Mac-only interactive confirm dialog with Cancel/Apply Cut buttons.
    Returns False -- never applies -- on any other platform, or if
    anything about showing the dialog itself goes wrong: "couldn't ask"
    must never silently mean "yes" for an operation this size."""
    if sys.platform != "darwin":
        print("[Theodore] Cut Timeline preview (no confirm dialog outside macOS):\n" + preview_text)
        print("[Theodore] Not applying automatically. Re-run theodore cut-timeline --apply yourself if this looks right.")
        return False
    escaped = preview_text.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display dialog "{escaped}" with title "Theodore -- Cut Timeline" '
        'buttons {"Cancel", "Apply Cut"} default button "Cancel" cancel button "Cancel"'
    )
    try:
        completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and "Apply Cut" in (completed.stdout or "")


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
    base_cmd = [config["theodoreExecutable"], "cut-timeline", "--project", theodore_project]
    if config.get("silenceThreshold") is not None:
        base_cmd += ["--silence-threshold", str(config["silenceThreshold"])]
    if config.get("aggressive"):
        base_cmd.append("--aggressive")

    env = {**os.environ, "THEODORE_DATA_DIR": config["theodoreDataDir"]}

    try:
        preview = subprocess.run(base_cmd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        _show_result("Could not run theodore", f"{exc}\n\nCheck theodoreExecutable in {CONFIG_PATH.name}.")
        return

    preview_output = ((preview.stdout or "") + (preview.stderr or "")).strip()
    if preview.returncode != 0:
        _show_result("Cut Timeline preview failed", preview_output or f"exit code {preview.returncode}")
        return

    if not _confirm(preview_output or "Nothing to preview."):
        _show_result("Cut Timeline", "Preview shown, not applied:\n\n" + preview_output)
        return

    try:
        applied = subprocess.run(base_cmd + ["--apply"], env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        _show_result("Could not run theodore", f"{exc}\n\nCheck theodoreExecutable in {CONFIG_PATH.name}.")
        return

    output = ((applied.stdout or "") + (applied.stderr or "")).strip()
    if applied.returncode != 0:
        _show_result("Cut Timeline failed", output or f"exit code {applied.returncode}")
    else:
        _show_result("Cut Timeline", output or "Done.")


main()
