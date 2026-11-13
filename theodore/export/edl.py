"""EDL marker fallback: written when Resolve isn't running, so analysis
work is never lost waiting on the app to be open.

Uses the classic CMX3600 "* LOC:" locator-comment convention, which Resolve
(and most other NLEs) read back in as timeline markers on EDL import.
"""
from __future__ import annotations

import logging
from pathlib import Path

from theodore.resolve import timecode as tc
from theodore.resolve.markers import MarkerPlan

logger = logging.getLogger("theodore.export.edl")

_EDL_COLOR_MAP = {"Blue": "BLUE", "Green": "GREEN", "Yellow": "YELLOW", "Cyan": "CYAN"}


def write_edl(plans: list[MarkerPlan], fps, title: str, out_path: Path) -> Path:
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for i, plan in enumerate(sorted(plans, key=lambda p: p.absolute_frame), start=1):
        start_tc = tc.frames_to_timecode(plan.absolute_frame, fps)
        end_tc = tc.frames_to_timecode(plan.absolute_frame + max(plan.duration_frames, 1), fps)
        idx = f"{i:03d}"
        lines.append(f"{idx}  001      V     C        {start_tc} {end_tc} {start_tc} {end_tc}")
        color = _EDL_COLOR_MAP.get(plan.color, "BLUE")
        note = plan.note.replace("\n", " / ")
        lines.append((f"* LOC: {start_tc} {color}  {plan.name} -- {note}")[:500])
        lines.append("")

    out_path.write_text("\n".join(lines))
    logger.info("Wrote EDL marker fallback to %s (%d markers)", out_path, len(plans))
    return out_path
