"""Checkpoints, undo, and the two confirmations.

Every mutating tool call is preceded (once per instruction) by a checkpoint:
save the project, then duplicate the current timeline under a recognisable
name. Nothing the agent does touches that duplicate, so "undo that" is just
switching back to it -- no history to replay, nothing to get wrong.

Source media and projects are never deleted by any tool here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

CHECKPOINT_PREFIX = "Theodore checkpoint"


class SafetyError(RuntimeError):
    pass


@dataclass
class Checkpoint:
    name: str
    label: str
    original_name: str
    timeline: object = None


@dataclass
class SafetyState:
    """Per-run state: which checkpoints exist, and whether this instruction
    has already been protected."""
    checkpoints: list = field(default_factory=list)
    protected_this_run: bool = False


def _stamp() -> str:
    return time.strftime("%H%M%S")


def create_checkpoint(handles, state: SafetyState, label: str) -> Checkpoint:
    """Save, then duplicate the current timeline. Returns the checkpoint.

    The duplicate is switched away from immediately: DuplicateTimeline may
    leave the copy current, and the agent must keep working on the real one.
    """
    pm, project, timeline = handles.project_manager, handles.project, handles.timeline
    save = getattr(pm, "SaveProject", None)
    if save is not None:
        try:
            save()
        except Exception as exc:  # noqa: BLE001 -- unsaved is worth a warning, not a stop
            raise SafetyError(f"Could not save the project before editing: {exc}") from exc

    original_name = timeline.GetName()
    name = f"{CHECKPOINT_PREFIX} {_stamp()} - {original_name}"
    dup = timeline.DuplicateTimeline(name)
    if not dup:
        raise SafetyError(
            f"Could not duplicate '{original_name}' as a checkpoint (Resolve returns "
            "nothing when the name is taken). Refusing to edit without one."
        )
    try:
        project.SetCurrentTimeline(timeline)
    except Exception as exc:  # noqa: BLE001
        raise SafetyError(f"Checkpoint made, but could not switch back to '{original_name}': {exc}") from exc

    cp = Checkpoint(name=name, label=label, original_name=original_name, timeline=dup)
    state.checkpoints.append(cp)
    state.protected_this_run = True
    return cp


def ensure_checkpoint(handles, state: SafetyState, label: str) -> Optional[Checkpoint]:
    """Checkpoint once per instruction, before the first mutation."""
    if state.protected_this_run:
        return None
    return create_checkpoint(handles, state, label)


def restore_last_checkpoint(handles, state: SafetyState) -> Checkpoint:
    """Switch to the most recent checkpoint. The edited timeline is left in
    place -- undo never deletes anything."""
    if not state.checkpoints:
        raise SafetyError("No checkpoint exists in this session to restore.")
    cp = state.checkpoints[-1]
    ok = handles.project.SetCurrentTimeline(cp.timeline)
    if ok is False:
        raise SafetyError(f"Resolve refused to switch to checkpoint '{cp.name}'.")
    return cp


Confirm = Callable[[str], bool]
