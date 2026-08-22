"""Pure, Resolve-independent assembly-plan construction.

Turns an ordered list of segment ids (from assembly/ordering.py) plus trims
(from assembly/trim.py) into explicit source in/out and new-timeline in/out
FRAME numbers for every clip -- with configurable handles, clamped so a
handle never reaches past what was actually said (trims.json's
original_start/original_end).

This is deliberately factored out of assembly/builder.py: builder.py is the
only module that should be touching the Resolve API, and this frame math is
exactly the kind of thing that must be right before any API call happens.
Everything here is arithmetic, fully unit-testable without Resolve
installed. export/captions.py also depends on this to remap caption timing
from source time onto the new assembly timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from theodore import config
from theodore.resolve import timecode as tc


@dataclass
class AssemblyClip:
    segment_id: str
    source_in_frame: int  # absolute frame in the SOURCE media (its own embedded timecode)
    source_out_frame: int
    timeline_in_frame: int  # frame on the NEW assembly timeline, 0-based
    timeline_out_frame: int

    @property
    def duration_frames(self) -> int:
        return self.source_out_frame - self.source_in_frame


def build_plan(
    transcript: dict,
    analysis: dict,
    trims: dict,
    order: list[str],
    *,
    handle_frames: int = config.DEFAULT_HANDLE_FRAMES,
    excluded: Optional[set] = None,
) -> list[AssemblyClip]:
    """Builds the cut list for a new assembly timeline, back-to-back with no
    gaps, in `order`. Segments in `excluded` are dropped entirely (not
    included with zero duration) -- callers that need to know what was
    excluded should compare `order` against the returned plan's segment ids.
    """
    excluded = excluded or set()
    fps = transcript["fps"]
    source_start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)
    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}

    clips: list[AssemblyClip] = []
    cursor = 0
    for seg_id in order:
        if seg_id in excluded:
            continue
        seg = segments_by_id.get(seg_id)
        if seg is None:
            continue

        trim = trims.get(seg_id)
        sel = selects_by_id.get(seg_id)

        if trim:
            in_seconds, out_seconds = trim["trimmed_start"], trim["trimmed_end"]
            bound_in_seconds, bound_out_seconds = trim["original_start"], trim["original_end"]
        else:
            start_u = utterances_by_id.get((sel or {}).get("clean_start_utterance") or seg["answer_start_utterance"])
            end_u = utterances_by_id.get((sel or {}).get("clean_end_utterance") or seg["answer_end_utterance"])
            if start_u is None or end_u is None:
                continue
            in_seconds, out_seconds = start_u["start"], end_u["end"]
            bound_in_seconds, bound_out_seconds = in_seconds, out_seconds

        in_frame = source_start_frame + tc.seconds_to_frames(in_seconds, fps)
        out_frame = source_start_frame + tc.seconds_to_frames(out_seconds, fps)
        bound_in_frame = source_start_frame + tc.seconds_to_frames(bound_in_seconds, fps)
        bound_out_frame = source_start_frame + tc.seconds_to_frames(bound_out_seconds, fps)

        # Handles give trim room without ever extending past what the
        # transcript actually covers for this answer -- that bound (not
        # neighboring clips or the source file's edges) is what's enforced
        # here; a real NLE clip's own media bounds are still the ultimate
        # limit, and assembly/builder.py must not extend past those either.
        in_frame = max(bound_in_frame, in_frame - handle_frames)
        out_frame = min(bound_out_frame, out_frame + handle_frames)
        if out_frame <= in_frame:
            out_frame = in_frame + 1

        duration = out_frame - in_frame
        clips.append(AssemblyClip(
            segment_id=seg_id,
            source_in_frame=in_frame,
            source_out_frame=out_frame,
            timeline_in_frame=cursor,
            timeline_out_frame=cursor + duration,
        ))
        cursor += duration

    return clips


def total_runtime_frames(plan: list[AssemblyClip]) -> int:
    return plan[-1].timeline_out_frame if plan else 0


def clip_for_segment(plan: list[AssemblyClip], segment_id: str) -> Optional[AssemblyClip]:
    return next((c for c in plan if c.segment_id == segment_id), None)


def target_duration_order(
    order: list[str],
    plan: list[AssemblyClip],
    selects_by_id: dict,
    target_frames: int,
) -> tuple[list[str], list[str]]:
    """v2.0 Part 4 step 10 -- duration targeting. Drops the weakest-scoring
    segments (by Selects strength; an unscored segment counts as 0.0, since
    there's no evidence it's worth keeping over a scored one) from `order`
    until the plan's total runtime fits within `target_frames`, preserving
    the relative order of everything kept. Returns (kept_order,
    dropped_ids) -- dropped_ids sorted for a stable, readable report, not
    in the order they were dropped."""
    total = total_runtime_frames(plan)
    if target_frames <= 0 or total <= target_frames:
        return order, []

    duration_by_id = {c.segment_id: c.duration_frames for c in plan}

    def strength(sid: str) -> float:
        return selects_by_id.get(sid, {}).get("strength") or 0.0

    weakest_first = sorted(
        (sid for sid in order if sid in duration_by_id),
        key=lambda sid: (strength(sid), sid),
    )

    dropped = set()
    remaining = total
    for sid in weakest_first:
        if remaining <= target_frames:
            break
        dropped.add(sid)
        remaining -= duration_by_id[sid]

    kept_order = [sid for sid in order if sid not in dropped]
    return kept_order, sorted(dropped)
