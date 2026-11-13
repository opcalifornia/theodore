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

One plan can span several subjects. ``build_plan()`` measures every id
against a single transcript; ``build_multi_subject_plan()`` measures each id
against its OWN subject's transcript, keyed by the id's prefix
("haylee.q03" -> "haylee"), and tags each clip with the subject it came
from. Both share ``_clip_for()``, so the two can never disagree about where
a cut falls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    # Which subject's media this clip pulls from, for a plan that spans more
    # than one. None means "the single transcript the caller passed to
    # build_plan()" -- the only possibility before cross-subject builds
    # existed, and still what every single-subject build produces, so
    # builder.py reads None as "use the one source" and needs no special
    # case for the ordinary one-interview assembly.
    subject: Optional[str] = None

    @property
    def duration_frames(self) -> int:
        return self.source_out_frame - self.source_in_frame


@dataclass(frozen=True)
class SubjectSources:
    """One subject's already-loaded transcript / analysis / trims.

    A cross-subject assembly is an ordered list of segment ids that each
    resolve against a DIFFERENT one of these: their own utterances, their
    own fps and start_timecode, their own trim proposals, their own source
    media. Bundling the three together keeps that association explicit
    rather than relying on three parallel dicts staying in step.
    """

    transcript: dict
    analysis: dict
    trims: dict = field(default_factory=dict)


def subject_of_segment(segment_id: str) -> str:
    """The subject an immutable id belongs to: ``"haylee.q03" -> "haylee"``.

    A pre-registry id with no prefix (``"s001"``) returns itself, which
    simply won't match any registered subject -- callers that need to tell
    "unprefixed" from "prefixed" must test for the ``"."`` themselves rather
    than reading anything into this return value.
    """
    return str(segment_id).split(".", 1)[0]


class _SourceIndex:
    """One subject's sources, pre-indexed for the plan math below.

    Built once per subject per plan rather than once per segment -- a
    cross-subject plan otherwise re-walks every subject's whole utterance
    list for every clip it places.
    """

    __slots__ = ("fps", "source_start_frame", "segments_by_id", "utterances_by_id",
                 "selects_by_id", "trims")

    def __init__(self, sources: SubjectSources):
        transcript, analysis = sources.transcript, sources.analysis
        self.trims = sources.trims or {}
        self.fps = transcript["fps"]
        self.source_start_frame = tc.timecode_to_frames(transcript["start_timecode"], self.fps)
        self.segments_by_id = {s["id"]: s for s in analysis["segments"]}
        self.utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
        self.selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}


def _clip_for(
    seg_id: str,
    index: _SourceIndex,
    handle_frames: int,
    cursor: int,
    subject: Optional[str],
) -> Optional[AssemblyClip]:
    """One clip's in/out math, against ONE subject's sources.

    The single place this arithmetic lives: :func:`build_plan` and
    :func:`build_multi_subject_plan` both come through here, so a
    cross-subject assembly cannot drift from a single-subject one.

    Returns None for a segment this subject's analysis/transcript can't
    resolve -- the caller skips it, exactly as a single-subject plan always
    has.
    """
    seg = index.segments_by_id.get(seg_id)
    if seg is None:
        return None

    fps = index.fps
    source_start_frame = index.source_start_frame
    trim = index.trims.get(seg_id)
    sel = index.selects_by_id.get(seg_id)

    if trim:
        in_seconds, out_seconds = trim["trimmed_start"], trim["trimmed_end"]
        bound_in_seconds, bound_out_seconds = trim["original_start"], trim["original_end"]
    else:
        start_u = index.utterances_by_id.get((sel or {}).get("clean_start_utterance") or seg["answer_start_utterance"])
        end_u = index.utterances_by_id.get((sel or {}).get("clean_end_utterance") or seg["answer_end_utterance"])
        if start_u is None or end_u is None:
            return None
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
    return AssemblyClip(
        segment_id=seg_id,
        source_in_frame=in_frame,
        source_out_frame=out_frame,
        timeline_in_frame=cursor,
        timeline_out_frame=cursor + duration,
        subject=subject,
    )


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

    Every id in `order` resolves against the ONE transcript/analysis/trims
    given, whatever subject prefix it carries -- which is what keeps a
    pre-registry project's unprefixed "s001" ids working. For an order that
    spans subjects, use :func:`build_multi_subject_plan` instead; it is the
    only function that reads a segment id's prefix.
    """
    excluded = excluded or set()
    index = _SourceIndex(SubjectSources(transcript, analysis, trims or {}))

    clips: list[AssemblyClip] = []
    cursor = 0
    for seg_id in order:
        if seg_id in excluded:
            continue
        clip = _clip_for(seg_id, index, handle_frames, cursor, None)
        if clip is None:
            continue
        clips.append(clip)
        cursor = clip.timeline_out_frame

    return clips


def build_multi_subject_plan(
    sources: dict,
    order: list[str],
    *,
    handle_frames: int = config.DEFAULT_HANDLE_FRAMES,
    excluded: Optional[set] = None,
) -> list[AssemblyClip]:
    """The cross-subject cut list: an order like
    ``["haylee.q03", "marcus.q04", "haylee.q05"]`` where each id resolves
    against ITS OWN subject's sources.

    `sources` maps subject id -> :class:`SubjectSources`. Each id's subject
    comes from its prefix (:func:`subject_of_segment`), and every clip is
    tagged with it so :func:`assembly.builder.build_timeline` knows which
    media pool item and which frame origin that clip belongs to.

    Frame math is identical to :func:`build_plan`'s -- both call
    :func:`_clip_for` -- so the only difference between a one-subject and a
    two-subject assembly is *which* transcript each id is measured against.

    Nothing here checks that the subjects share a frame rate: that is a
    property of the timeline being built, so it is enforced in
    :func:`assembly.builder.common_source_fps` where it cannot be bypassed
    by a caller that skips this function.

    Raises ValueError for an id whose subject isn't in `sources` -- a caller
    bug, since the CLI resolves and validates every referenced subject
    before planning. Silently dropping the clip would build a short
    assembly and call it a success.
    """
    excluded = excluded or set()
    indexes: dict = {}

    clips: list[AssemblyClip] = []
    cursor = 0
    for seg_id in order:
        if seg_id in excluded:
            continue
        subject = subject_of_segment(seg_id)
        if subject not in indexes:
            subject_sources = sources.get(subject)
            if subject_sources is None:
                raise ValueError(
                    f"Segment {seg_id!r} belongs to subject {subject!r}, which has no "
                    f"sources in this plan. Known subjects: {', '.join(sorted(sources)) or '(none)'}."
                )
            indexes[subject] = _SourceIndex(subject_sources)
        clip = _clip_for(seg_id, indexes[subject], handle_frames, cursor, subject)
        if clip is None:
            continue
        clips.append(clip)
        cursor = clip.timeline_out_frame

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
