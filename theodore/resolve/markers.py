"""Writes Theodore's segment analysis onto a Resolve timeline as markers.

Marker positions are computed in two deliberately separate steps -- this is
the split the spec calls out as the single easiest way to get this stage
wrong (offsetting every marker by an hour on any timeline starting at
01:00:00:00):

  1. plan_markers() -- pure, Resolve-independent -- computes each marker's
     ABSOLUTE frame position: the frame it lands on if the source clip's own
     embedded timecode is preserved on the timeline (the normal documentary
     workflow -- sync-sound dailies dropped onto a timeline that keeps their
     source TC).
  2. write_markers() -- talks to Resolve -- subtracts timeline.GetStartFrame()
     from that absolute frame to get the relative frameId AddMarker() expects.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from theodore.resolve import timecode as tc
from theodore.resolve.connection import ResolveHandles

logger = logging.getLogger("theodore.resolve.markers")

MARKER_SOURCE_TAG = "theodore"

COLOR_STANDARD = "Blue"
COLOR_STRONG = "Green"
COLOR_WEAK = "Yellow"
COLOR_VOLUNTEERED = "Cyan"

STRONG_THRESHOLD = 0.75
WEAK_THRESHOLD = 0.4


class MarkerWriteError(RuntimeError):
    pass


@dataclass
class MarkerPlan:
    absolute_frame: int
    color: str
    name: str
    note: str
    duration_frames: int
    custom_data: dict
    segment_id: str


def choose_color(strength: Optional[float], is_volunteered: bool) -> str:
    if is_volunteered:
        return COLOR_VOLUNTEERED
    if strength is None:
        return COLOR_STANDARD
    if strength >= STRONG_THRESHOLD:
        return COLOR_STRONG
    if strength <= WEAK_THRESHOLD:
        return COLOR_WEAK
    return COLOR_STANDARD


def build_note(segment: dict, select: Optional[dict], theme_labels: list[str]) -> str:
    lines = []
    lines.append(f"Q: {segment['question_text']}" if segment.get("question_text") else "(volunteered, unprompted)")
    if segment.get("answer_summary"):
        lines.append(f"A: {segment['answer_summary']}")
    if select and select.get("best_line"):
        lines.append(f'Best line: "{select["best_line"]}"')
    if select and select.get("issues"):
        lines.append("Issues: " + "; ".join(select["issues"]))
    if theme_labels:
        lines.append("Themes: " + ", ".join(theme_labels))
    return "\n".join(lines)


def plan_markers(transcript: dict, analysis: dict) -> list[MarkerPlan]:
    """Build the platform-agnostic marker plan from analyze/ output. Pure
    function -- this is what --dry-run prints and what the EDL fallback
    consumes too, so it must never touch the Resolve API."""
    fps = transcript["fps"]
    source_start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)

    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})

    plans = []
    for seg in analysis["segments"]:
        start_u = utterances_by_id.get(seg["answer_start_utterance"])
        end_u = utterances_by_id.get(seg["answer_end_utterance"])
        if start_u is None or end_u is None:
            logger.warning("Segment %s references an utterance id not in the transcript, skipping", seg["id"])
            continue

        answer_start_frame = source_start_frame + tc.seconds_to_frames(start_u["start"], fps)
        answer_end_frame = source_start_frame + tc.seconds_to_frames(end_u["end"], fps)
        duration = max(1, answer_end_frame - answer_start_frame)

        sel = selects_by_id.get(seg["id"])
        strength = sel.get("strength") if sel else None
        is_volunteered = seg.get("question_text") is None
        theme_ids = assignments.get(seg["id"], [])
        theme_labels = [themes_by_id[t]["label"] for t in theme_ids if t in themes_by_id]

        clean_start_frame = clean_end_frame = None
        if sel:
            clean_start_u = utterances_by_id.get(sel.get("clean_start_utterance"))
            clean_end_u = utterances_by_id.get(sel.get("clean_end_utterance"))
            if clean_start_u:
                clean_start_frame = source_start_frame + tc.seconds_to_frames(clean_start_u["start"], fps)
            if clean_end_u:
                clean_end_frame = source_start_frame + tc.seconds_to_frames(clean_end_u["end"], fps)

        custom_data = {
            "source": MARKER_SOURCE_TAG,
            "segment_id": seg["id"],
            "strength": strength,
            "clean_start_frame": clean_start_frame,
            "clean_end_frame": clean_end_frame,
            "theme_ids": theme_ids,
        }

        plans.append(MarkerPlan(
            absolute_frame=answer_start_frame,
            color=choose_color(strength, is_volunteered),
            name=(seg.get("question_label") or "Untitled segment")[:255],
            note=build_note(seg, sel, theme_labels),
            duration_frames=duration,
            custom_data=custom_data,
            segment_id=seg["id"],
        ))

    plans.sort(key=lambda p: p.absolute_frame)
    return plans


def check_fps_match(timeline_fps, source_fps) -> bool:
    """True if the timeline's frame rate matches the source media's. Callers
    must warn loudly (never silently convert) when this is False."""
    return tc.parse_fps(timeline_fps) == tc.parse_fps(source_fps)


def _theodore_wrote(existing_marker: dict) -> bool:
    raw = existing_marker.get("customData") or ""
    try:
        return json.loads(raw).get("source") == MARKER_SOURCE_TAG
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


def write_markers(handles: ResolveHandles, plans: list[MarkerPlan], overwrite: bool = False) -> dict:
    """Writes `plans` onto handles.timeline.

    Handles the cases the spec calls out explicitly: a marker already at
    that frame gets offset by one frame unless --overwrite and the existing
    marker was written by Theodore (identified via customData), in which
    case it's replaced in place. A segment landing before the timeline's
    start frame is skipped with a warning rather than written at a
    nonsensical negative position.
    """
    timeline = handles.timeline
    start_frame = timeline.GetStartFrame()
    if start_frame is None:
        raise MarkerWriteError("timeline.GetStartFrame() returned None -- is a timeline open?")

    existing = timeline.GetMarkers() or {}

    written = replaced = offset_count = skipped = 0

    for plan in plans:
        rel_frame = plan.absolute_frame - start_frame
        if rel_frame < 0:
            logger.warning(
                "Segment %s lands before the timeline start (relative frame %d) -- "
                "skipping. Check that the source media's embedded start timecode "
                "matches how the clip was placed on this timeline.",
                plan.segment_id, rel_frame,
            )
            skipped += 1
            continue

        replaced_this_one = False
        while rel_frame in existing:
            marker = existing[rel_frame]
            if overwrite and _theodore_wrote(marker):
                timeline.DeleteMarkerAtFrame(rel_frame)
                del existing[rel_frame]
                replaced_this_one = True
                break
            rel_frame += 1
            offset_count += 1

        ok = timeline.AddMarker(
            rel_frame, plan.color, plan.name, plan.note,
            plan.duration_frames, json.dumps(plan.custom_data),
        )
        if not ok:
            logger.error("Resolve rejected the marker for segment %s at relative frame %d", plan.segment_id, rel_frame)
            continue

        existing[rel_frame] = {"color": plan.color, "customData": json.dumps(plan.custom_data)}
        if replaced_this_one:
            replaced += 1
        else:
            written += 1

    return {"written": written, "replaced": replaced, "offset": offset_count, "skipped": skipped, "total": len(plans)}


def format_dry_run(plans: list[MarkerPlan], fps) -> str:
    lines = []
    for plan in plans:
        tc_str = tc.frames_to_timecode(plan.absolute_frame, fps)
        lines.append(f"[{plan.color:<6}] {tc_str}  +{plan.duration_frames:>5}f  {plan.name}")
        for note_line in plan.note.splitlines():
            lines.append(f"           {note_line}")
        lines.append("")
    return "\n".join(lines)
