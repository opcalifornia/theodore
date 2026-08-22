"""Constructs a NEW DaVinci Resolve timeline from an assembly plan.

The hard safety rule this module is built around: **Theodore never modifies
an existing timeline.** Every build creates a new one. Before a single clip
is appended, the build verifies that the timeline Resolve considers
"current" is the one it just created -- because
``MediaPool.AppendToTimeline()`` appends to the *current* timeline, and
getting that wrong would append an entire assembly into the editor's real
cut.

This module knows nothing about where a plan came from. It takes an ordered
``list[AssemblyClip]`` and builds exactly that, so the same code serves an
ordering-pass assembly and a rebuild from a persisted edit list:

  - v1.5 rough assembly: ``ordering.compute_order()`` -> ``plan.build_plan()``
    -> here, with ``mode`` naming the ordering strategy and ``rationale``
    carrying the narrative pass's explanation.
  - v2 conversational editing: a command mutates a persisted edit list, and
    a fresh timeline is rebuilt from it. ``mode`` is then just a label
    (``"v003"``), ``rationale`` is None, and ``name`` is passed explicitly
    (``THEODORE_<project>_v003``). Nothing here requires the plan to have
    come from an ordering pass, and nothing re-derives the order.

Timeline naming is a parameter, not a policy: pass ``name=`` for full
control, or ``project``/``subject``/``mode`` to get the default
``THEODORE_<project>_<subject>_<mode>_<timestamp>`` construction from
:func:`timeline_name`.

Structure mirrors ``resolve/markers.py`` deliberately:

  1. :func:`describe_build` / :func:`plan_edit_markers` are pure. They touch
     no Resolve API at all, so ``--dry-run`` is genuinely inert and every
     piece of arithmetic is unit-testable without Resolve installed.
  2. :func:`build_timeline` is the only function that talks to the live API.

All in/out frame math already happened in :mod:`theodore.assembly.plan`;
this module never recomputes it. Its two remaining frame-math jobs are
*coordinate conversions*, and both are the same class of bug markers.py
exists to prevent:

  - ``AssemblyClip.source_in_frame`` is an ABSOLUTE frame in the source
    media's own embedded timecode space (plan.py adds
    ``timecode_to_frames(transcript["start_timecode"])``). A media pool
    item's in/out points are numbered from the START OF THE MEDIA, so for
    footage with a non-zero start timecode (01:00:00:00 dailies, the normal
    documentary case) that origin has to come back off again before it is
    handed to ``AppendToTimeline``. See :func:`media_item_frame_origin` and
    the ``frame_origin`` parameter.
  - ``AssemblyClip.timeline_in_frame`` is 0-based from the start of the new
    assembly, and ``Timeline.AddMarker()`` also wants a frame relative to
    the timeline's start, so the two normally coincide. "Normally" is not
    "always": the build reads back the first appended item and checks it
    really does begin at ``timeline.GetStartFrame()`` before trusting that.

Every Resolve call's return value is checked -- ``False``, ``None`` and an
empty list all mean "failed" in this API and none of them raise on their
own. Anything that could leave a partially-built or empty timeline while
reporting success raises :class:`BuilderError` instead.

``theodore untrim``
-------------------
There is no separate un-trimming code path. ``plan.build_plan()`` falls back
to the segment's un-trimmed clean range whenever a segment has no entry in
``trims``, so an untrimmed rebuild is just a normal build of the plan
returned by :func:`build_untrimmed_plan` (i.e. ``build_plan(..., {}, ...)``).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

from theodore import config
from theodore.assembly import plan as assembly_plan
from theodore.assembly.plan import AssemblyClip
from theodore.resolve import markers as resolve_markers
from theodore.resolve import timecode as tc
from theodore.resolve.connection import ResolveHandles

logger = logging.getLogger("theodore.assembly.builder")

TIMELINE_NAME_PREFIX = "THEODORE"
VIDEO_TRACK = "video"
AUDIO_TRACK = "audio"

# How to convert AssemblyClip.source_in/out_frame (absolute, in the source
# media's embedded timecode space) into the in/out point AppendToTimeline
# wants. See media_item_frame_origin() for why "auto" is the default.
FRAME_ORIGIN_MODES = ("auto", "media", "absolute")

# Resolve reports timelineFrameRate as a string, and some versions hand back
# a decimal expansion ("23.976023976023978") rather than a canonical label.
# Comparing those exactly would raise a false alarm on every NTSC project,
# so frame-rate comparison here is tolerant to well under the 24-vs-23.976
# gap (0.024) it actually needs to catch.
FPS_MATCH_TOLERANCE = Fraction(1, 1000)

# Guard against a cyclic or pathological media pool folder structure.
_MAX_FOLDERS_WALKED = 5000

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")


class BuilderError(RuntimeError):
    """A Resolve call reported failure, or what landed on the timeline does
    not match what was planned. Mirrors markers.MarkerWriteError: raised
    rather than logged, because a wrong assembly must never be reported as a
    successful one."""


@dataclass
class EditMarker:
    """One marker to write at an edit point on the new assembly timeline.

    ``timeline_frame`` is 0-based from the start of the assembly (exactly
    ``AssemblyClip.timeline_in_frame``); converting that to the frame id
    ``AddMarker()`` wants is :func:`write_edit_markers`'s job.
    """

    timeline_frame: int
    color: str
    name: str
    note: str
    duration_frames: int
    custom_data: dict
    segment_id: str


@dataclass
class BuildResult:
    """What a build actually did. ``timeline`` is the live Resolve Timeline
    object for the new timeline (None for a build that failed before
    creating it)."""

    timeline_name: str
    timeline: object = None
    clips_planned: int = 0
    clips_appended: int = 0
    markers_written: int = 0
    markers_failed: int = 0
    runtime_frames: int = 0
    fps: str = ""
    imported_paths: list = field(default_factory=list)
    reused_paths: list = field(default_factory=list)
    multicam_clips_used: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def warn(self, message: str, *args) -> None:
        """Record a non-fatal problem AND log it at WARNING. Anything in
        here is something the editor needs to be told about in the CLI's
        output, not just buried in theodore.log."""
        formatted = message % args if args else message
        self.warnings.append(formatted)
        logger.warning(formatted)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def sanitize_name_part(value: str) -> str:
    """Collapse anything that isn't a letter or digit into an underscore, so
    a project or subject name with spaces/slashes can't produce a timeline
    name Resolve refuses or truncates oddly."""
    cleaned = _NAME_SAFE_RE.sub("_", str(value)).strip("_")
    return cleaned or "unnamed"


def timeline_name(project: str, subject: str, mode: str, *, timestamp: Optional[str] = None) -> str:
    """``THEODORE_<project>_<subject>_<mode>_<YYYYmmdd_HHMMSS>``.

    Only a default. Callers that name timelines on their own scheme (v2's
    per-edit-list ``THEODORE_<project>_v003``) pass ``name=``
    to :func:`build_timeline` instead and never touch this.

    The timestamp is what keeps repeated builds from colliding with each
    other (Resolve refuses to create a second timeline with an existing
    name), and what makes it obvious in the media pool which build is which.
    """
    stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    return "_".join([
        TIMELINE_NAME_PREFIX,
        sanitize_name_part(project),
        sanitize_name_part(subject),
        sanitize_name_part(mode),
        stamp,
    ])


# --------------------------------------------------------------------------
# Pure planning: markers + dry-run description
# --------------------------------------------------------------------------

def _trim_note(trim: Optional[dict]) -> Optional[str]:
    """One-line human summary of what trimming removed from this segment, or
    None if nothing was removed."""
    if not trim:
        return None
    parts = []
    head = (trim.get("trimmed_start") or 0.0) - (trim.get("original_start") or 0.0)
    tail = (trim.get("original_end") or 0.0) - (trim.get("trimmed_end") or 0.0)
    if head > 0.001:
        parts.append(f"-{head:.1f}s head")
    if tail > 0.001:
        parts.append(f"-{tail:.1f}s tail")
    cuts = trim.get("cuts") or []
    if cuts:
        parts.append(f"{len(cuts)} interior cut{'s' if len(cuts) != 1 else ''}")
    return "Trim: " + ", ".join(parts) if parts else None


def plan_edit_markers(
    plan: list[AssemblyClip],
    transcript: dict,
    analysis: dict,
    *,
    mode: str = "chronological",
    trims: Optional[dict] = None,
    rationale: Optional[str] = None,
) -> list[EditMarker]:
    """One marker per edit point on the new assembly timeline. Pure -- no
    Resolve API is touched, so this is fully unit-testable and is also what
    ``--dry-run`` reports on.

    Colors come from :func:`resolve.markers.choose_color` and the customData
    blob keeps markers.py's established shape (``source``, ``segment_id``,
    ``strength``, ``clean_start_frame``, ``clean_end_frame``, ``theme_ids``
    -- with the same meanings, so a tool reading Theodore markers can read
    both kinds) plus assembly-specific keys describing where the clip came
    from and where it landed.

    ``rationale`` (narrative mode only) is prepended to the FIRST marker's
    note and recorded in its customData, so the story logic is readable by
    clicking one marker rather than opening a script console.
    """
    trims = trims or {}
    fps = transcript["fps"]
    source_start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)

    utterances_by_id = {u["id"]: u for u in transcript.get("utterances", [])}
    segments_by_id = {s["id"]: s for s in analysis.get("segments", [])}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})

    markers: list[EditMarker] = []
    for index, clip in enumerate(plan):
        seg = segments_by_id.get(clip.segment_id)
        sel = selects_by_id.get(clip.segment_id)
        strength = sel.get("strength") if sel else None
        theme_ids = assignments.get(clip.segment_id, [])
        theme_labels = [themes_by_id[t]["label"] for t in theme_ids if t in themes_by_id]

        clean_start_frame = clean_end_frame = None
        if sel:
            start_u = utterances_by_id.get(sel.get("clean_start_utterance"))
            end_u = utterances_by_id.get(sel.get("clean_end_utterance"))
            if start_u:
                clean_start_frame = source_start_frame + tc.seconds_to_frames(start_u["start"], fps)
            if end_u:
                clean_end_frame = source_start_frame + tc.seconds_to_frames(end_u["end"], fps)

        if seg is None:
            # plan.py only emits clips for segments that exist in `analysis`,
            # so this means the caller passed mismatched inputs. Marker the
            # edit anyway rather than leaving a silent gap in the assembly.
            logger.warning(
                "Segment %s is in the assembly plan but not in analysis -- writing a bare marker",
                clip.segment_id,
            )
            name = clip.segment_id
            note = f"Segment {clip.segment_id} (not found in analysis.json)"
            color = resolve_markers.COLOR_STANDARD
        else:
            name = (seg.get("question_label") or clip.segment_id)
            note = resolve_markers.build_note(seg, sel, theme_labels)
            color = resolve_markers.choose_color(strength, seg.get("question_text") is None)

        trim_line = _trim_note(trims.get(clip.segment_id))
        if trim_line:
            note = f"{note}\n{trim_line}" if note else trim_line

        markers.append(EditMarker(
            timeline_frame=clip.timeline_in_frame,
            color=color,
            name=f"{index + 1:02d}. {name}"[:255],
            note=note,
            duration_frames=max(1, clip.duration_frames),
            custom_data={
                "source": resolve_markers.MARKER_SOURCE_TAG,
                "segment_id": clip.segment_id,
                "strength": strength,
                "clean_start_frame": clean_start_frame,
                "clean_end_frame": clean_end_frame,
                "theme_ids": theme_ids,
                # Assembly-specific: which build this came from and how the
                # source range maps onto the new timeline.
                "assembly_mode": mode,
                "order_index": index,
                "source_in_frame": clip.source_in_frame,
                "source_out_frame": clip.source_out_frame,
                "timeline_in_frame": clip.timeline_in_frame,
                "timeline_out_frame": clip.timeline_out_frame,
            },
            segment_id=clip.segment_id,
        ))

    if rationale and markers:
        first = markers[0]
        first.note = f"Assembly rationale ({mode}):\n{rationale.strip()}\n\n{first.note}".strip()
        first.custom_data["rationale"] = rationale.strip()

    return markers


def describe_build(
    plan: list[AssemblyClip],
    *,
    transcript: dict,
    name: Optional[str] = None,
    project: Optional[str] = None,
    subject: Optional[str] = None,
    mode: str = "custom",
    analysis: Optional[dict] = None,
    rationale: Optional[str] = None,
    handle_frames: Optional[int] = None,
) -> str:
    """The ``--dry-run`` report: what :func:`build_timeline` *would* do.

    Touches nothing -- no Resolve connection is needed or made. Takes the
    same keyword arguments as :func:`build_timeline` (minus the live-API
    ones), so a caller can build one argument dict and use it for both.
    """
    fps = transcript["fps"]
    intended_name = name or timeline_name(
        project or "?", subject or "?", mode, timestamp="<timestamp>",
    )
    runtime = assembly_plan.total_runtime_frames(plan)
    analysis = analysis or {"segments": []}
    labels = {s["id"]: (s.get("question_label") or s["id"]) for s in analysis.get("segments", [])}

    lines = [
        "DRY RUN -- nothing has been written to Resolve.",
        "",
        f"  New timeline:  {intended_name}",
        f"  Order mode:    {mode}",
        f"  Source file:   {transcript.get('source_file', '(unknown)')}",
        f"  Frame rate:    {fps}",
    ]
    if handle_frames is not None:
        lines.append(f"  Handles:       {handle_frames} frames each side")
    lines.append(f"  Clips:         {len(plan)}")
    lines.append(f"  Total runtime: {tc.frames_to_timecode(runtime, fps)} ({runtime} frames)")
    lines.append("")

    if not plan:
        lines.append("  (nothing to build -- the plan is empty)")
        return "\n".join(lines)

    lines.append("   #  segment          source in     source out    timeline in     dur  label")
    for index, clip in enumerate(plan, start=1):
        lines.append(
            f"  {index:>2}  {clip.segment_id:<15}  "
            f"{tc.frames_to_timecode(clip.source_in_frame, fps)}  "
            f"{tc.frames_to_timecode(clip.source_out_frame, fps)}  "
            f"{tc.frames_to_timecode(clip.timeline_in_frame, fps)}  "
            f"{clip.duration_frames:>6}  "
            f"{labels.get(clip.segment_id, clip.segment_id)}"
        )

    if rationale:
        lines.append("")
        lines.append(f"  Rationale ({mode}):")
        for line in rationale.strip().splitlines():
            lines.append(f"    {line}")

    return "\n".join(lines)


def build_untrimmed_plan(
    transcript: dict,
    analysis: dict,
    order: list,
    *,
    handle_frames: int = config.DEFAULT_HANDLE_FRAMES,
    excluded: Optional[set] = None,
) -> list[AssemblyClip]:
    """The plan for ``theodore untrim``: every segment at its full clean
    length, ignoring trims.json entirely.

    There is deliberately no un-trimming logic here. ``plan.build_plan()``
    already falls back to the segment's un-trimmed clean utterance range for
    any segment with no trims entry, so passing an empty trims dict rebuilds
    the assembly at full length. Hand the result to :func:`build_timeline`
    exactly like any other plan; it creates a new timeline, so the trimmed
    build it is "undoing" is left untouched on disk and in Resolve.
    """
    return assembly_plan.build_plan(
        transcript, analysis, {}, order, handle_frames=handle_frames, excluded=excluded,
    )


# --------------------------------------------------------------------------
# Resolve API helpers -- every one of these tolerates a missing method or a
# falsy return rather than assuming the call worked.
# --------------------------------------------------------------------------

def _as_list(value) -> list:
    """Normalize a Resolve list-ish return. Some calls return a list, some
    return a 1-indexed dict, and all of them return False/None on failure."""
    if not value:
        return []
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _call(obj, method: str, *args):
    """Call an optional Resolve method. Returns None if the object doesn't
    have it or the call raises -- callers must treat None as "unknown", not
    as "empty"."""
    fn = getattr(obj, method, None)
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- the bridge raises assorted native errors
        logger.exception("Resolve call %s(%r) raised", method, args)
        return None


def _clip_property(item, name: str):
    """One MediaPoolItem clip property, or None. Handles both the
    ``GetClipProperty(name) -> value`` and ``GetClipProperty() -> dict``
    forms, since which one a given version/property returns is not something
    this project can verify without a live Resolve."""
    value = _call(item, "GetClipProperty", name)
    if isinstance(value, dict):
        value = value.get(name)
    if _blank(value):
        props = _call(item, "GetClipProperty")
        if isinstance(props, dict):
            value = props.get(name)
    return None if _blank(value) else value


def _blank(value) -> bool:
    """"Nothing here" for a Resolve property: None, empty string, or False.
    Deliberately NOT plain falsiness -- an integer 0 is a legitimate value
    for a clip's 'Start' frame and must survive."""
    return value is None or value is False or value == ""


def _item_file_path(item) -> Optional[str]:
    for prop in ("File Path", "File Name"):
        value = _clip_property(item, prop)
        if value:
            return str(value)
    return None


def _item_name(item) -> Optional[str]:
    value = _clip_property(item, "Clip Name")
    if value:
        return str(value)
    value = _call(item, "GetName")
    return str(value) if value else None


def _same_file(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    pa, pb = Path(str(a)), Path(str(b))
    if pa == pb:
        return True
    try:
        if pa.resolve() == pb.resolve():
            return True
    except OSError:
        pass
    return False


def iter_media_pool_items(media_pool) -> list:
    """Every clip in the media pool, walking the root folder's whole subtree.

    Theodore's ``ingest`` stage never imports anything into Resolve (it only
    extracts audio with ffmpeg for transcription), so the source file may or
    may not already be in the pool, and if it is, the editor could have
    filed it in any bin.
    """
    root = _call(media_pool, "GetRootFolder")
    if not root:
        logger.warning("MediaPool.GetRootFolder() returned nothing -- treating the media pool as empty")
        return []

    items: list = []
    stack = [root]
    walked = 0
    while stack and walked < _MAX_FOLDERS_WALKED:
        folder = stack.pop()
        walked += 1
        items.extend(_as_list(_call(folder, "GetClipList")))
        stack.extend(_as_list(_call(folder, "GetSubFolderList")))
    if walked >= _MAX_FOLDERS_WALKED:
        logger.warning("Stopped walking the media pool after %d folders", _MAX_FOLDERS_WALKED)
    return items


def find_media_pool_item(media_pool, path: str):
    """The MediaPoolItem for `path`, or None.

    Two passes: full-path match first, then a filename-only match as a
    fallback (with a warning), which covers a pool whose clips were relinked
    from a different mount point than the one ingest recorded.
    """
    items = iter_media_pool_items(media_pool)
    for item in items:
        if _same_file(_item_file_path(item), path):
            return item

    target_name = Path(str(path)).name
    for item in items:
        found = _item_file_path(item)
        if found and Path(found).name == target_name:
            logger.warning(
                "Media pool clip %r matched %r by FILENAME only (different directory). "
                "Using it -- verify it really is the same footage.", found, path,
            )
            return item
    return None


def find_media_pool_item_by_name(media_pool, name: str):
    """The MediaPoolItem whose clip name is `name` (used to locate a
    multicam clip, which has no file path of its own), or None."""
    for item in iter_media_pool_items(media_pool):
        if _item_name(item) == name:
            return item
    return None


def find_or_import_media(media_pool, path: str, result: Optional[BuildResult] = None):
    """Find `path` in the media pool, importing it if it isn't there yet.

    Raises BuilderError if the file is missing from disk or Resolve refuses
    the import -- ``ImportMedia`` returns an empty list rather than raising
    when it fails, which would otherwise show up much later as an empty
    timeline.
    """
    existing = find_media_pool_item(media_pool, path)
    if existing is not None:
        logger.info("Found %s already in the media pool", path)
        if result is not None:
            result.reused_paths.append(str(path))
        return existing

    if not Path(str(path)).exists():
        raise BuilderError(
            f"Source media not found on disk: {path}\n"
            "Theodore's transcript records the path the footage was ingested from. "
            "If the drive isn't mounted (or the media moved), reconnect it -- or "
            "import the clip into Resolve's media pool yourself and re-run."
        )

    imported = _as_list(_call(media_pool, "ImportMedia", [str(path)]))
    if not imported:
        raise BuilderError(
            f"Resolve refused to import {path} into the media pool "
            "(MediaPool.ImportMedia returned nothing). Check that the file is a "
            "format this Resolve version can read, and that it isn't already "
            "open exclusively by another application."
        )
    logger.info("Imported %s into the media pool", path)
    if result is not None:
        result.imported_paths.append(str(path))
    return imported[0]


def media_item_frame_origin(item, transcript_start_frame: int, fps) -> tuple:
    """The absolute-timecode frame that corresponds to in-point 0 of `item`.

    This is the assembly-side counterpart of markers.py subtracting
    ``timeline.GetStartFrame()``. ``AssemblyClip.source_in_frame`` counts
    from 00:00:00:00 in the source's embedded timecode; a media pool item's
    in/out points count from the start of the media. For 01:00:00:00
    dailies those differ by exactly one hour, and passing the absolute number
    straight through would ask for an in-point an hour past the end of the
    clip.

    Read from the clip's own ``Start TC`` / ``Start`` properties when
    Resolve exposes them, falling back to the transcript's
    ``start_timecode`` -- which is the very value plan.py added, so the
    fallback is exact whenever ingest read the media's timecode correctly.

    Returns ``(origin_frame, source_description)``.
    """
    start_tc = _clip_property(item, "Start TC")
    if start_tc:
        try:
            origin = tc.timecode_to_frames(str(start_tc), fps)
        except tc.TimecodeError:
            logger.warning("Media pool item reported an unparseable 'Start TC' %r", start_tc)
        else:
            try:
                in_point = int(_clip_property(item, "Start") or 0)
            except (TypeError, ValueError):
                in_point = 0
            return origin - in_point, f"clip 'Start TC' {start_tc}"
    return transcript_start_frame, "transcript start_timecode"


def _fps_matches(a, b) -> bool:
    """Frame-rate equality, tolerant of a decimal expansion standing in for
    a canonical NTSC label. Never tolerant enough to confuse 24 with
    23.976."""
    try:
        pa, pb = tc.parse_fps(a), tc.parse_fps(b)
    except tc.TimecodeError:
        return False
    return abs(pa - pb) < FPS_MATCH_TOLERANCE


def _check_frame_rate(resolve_project, source_fps, result: BuildResult, allow_mismatch: bool) -> None:
    """A new timeline inherits the project's frame rate. If that differs
    from the source media's, every frame number computed by plan.py lands
    somewhere else on the timeline -- so this refuses to build by default
    rather than producing a plausible-looking, silently wrong assembly."""
    timeline_fps = _call(resolve_project, "GetSetting", "timelineFrameRate")
    if not timeline_fps:
        result.warn(
            "Could not read the project's timelineFrameRate setting -- proceeding, but "
            "confirm the project's frame rate is %s before trusting this assembly.",
            source_fps,
        )
        return
    if _fps_matches(timeline_fps, source_fps):
        return
    message = (
        f"Project timeline frame rate is {timeline_fps} but the source media is {source_fps}. "
        "Every in/out point in this assembly is computed in source frames; building at a "
        "different rate would place every cut in the wrong spot."
    )
    if allow_mismatch:
        result.warn("%s Building anyway because allow_fps_mismatch=True.", message)
        return
    raise BuilderError(
        message + "\nFix the project's frame rate (File -> Project Settings -> Master "
        "Settings -> Timeline frame rate) and re-run, or pass allow_fps_mismatch=True "
        "if you genuinely intend to conform."
    )


def _read_track_items(timeline, track_type: str, index: int = 1) -> Optional[list]:
    """Items on one timeline track. None means "couldn't read" (missing
    method / the call raised); an empty list means "read it, it's empty" --
    the two must not be confused when verifying an append."""
    fn = getattr(timeline, "GetItemListInTrack", None)
    if fn is None:
        return None
    try:
        items = fn(track_type, index)
    except Exception:  # noqa: BLE001
        logger.exception("Timeline.GetItemListInTrack(%r, %d) raised", track_type, index)
        return None
    if items is None:
        return None
    return _as_list(items)


def _appended_items(timeline) -> tuple:
    """The clips that actually landed, and which track they landed on.

    Video track 1 first; if that is empty, audio track 1 -- an audio-only
    interview (Theodore accepts .wav/.mp3 sources) produces no video items
    at all, and treating that as a failed append would be wrong.
    """
    video = _read_track_items(timeline, VIDEO_TRACK, 1)
    if video:
        return video, VIDEO_TRACK
    audio = _read_track_items(timeline, AUDIO_TRACK, 1)
    if audio:
        return audio, AUDIO_TRACK
    if video is None and audio is None:
        return None, None
    return [], VIDEO_TRACK if video is not None else AUDIO_TRACK


def _verify_appended(timeline, plan: list[AssemblyClip], name: str, result: BuildResult) -> list:
    """Read the new timeline back and confirm it holds exactly the planned
    clips. AppendToTimeline can partially succeed, and a silently short or
    empty timeline reported as a success is the failure mode this whole
    module is defending against."""
    items, track = _appended_items(timeline)
    if items is None:
        result.warn(
            "Could not read '%s' back (Timeline.GetItemListInTrack unavailable) -- "
            "the clip count could NOT be verified. Check the timeline by eye.", name,
        )
        return []

    if len(items) != len(plan):
        raise BuilderError(
            f"Timeline '{name}' was created but holds {len(items)} clip(s) on "
            f"{track} track 1 where the plan has {len(plan)}. The append only "
            "partially succeeded -- delete that timeline and re-run. Common causes: "
            "an in/out point past the end of the source media, or offline media."
        )

    for item, clip in zip(items, plan):
        duration = _call(item, "GetDuration")
        if duration is None:
            continue
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            continue
        delta = duration - clip.duration_frames
        if abs(delta) > 1:
            raise BuilderError(
                f"Timeline '{name}': clip for segment {clip.segment_id} is {duration} "
                f"frames but the plan asked for {clip.duration_frames}. Resolve clamped "
                "the requested in/out range, which usually means the frame-origin "
                "assumption is wrong for this media (try frame_origin='absolute') or "
                "the range runs past the end of the clip. Delete the timeline and re-run."
            )
        if delta:
            result.warn(
                "Clip for segment %s came out %+d frame vs the plan (%d requested, %d actual) -- "
                "this Resolve version treats AppendToTimeline's endFrame as inclusive. "
                "Cut points are within one frame of plan.",
                clip.segment_id, delta, clip.duration_frames, duration,
            )
    return items


def _marker_origin(timeline, items: list, result: BuildResult) -> int:
    """How much to add to a plan's 0-based ``timeline_in_frame`` to get the
    frame id ``AddMarker()`` wants.

    Normally zero: the plan starts at 0, the clips are appended at the start
    of the timeline, and AddMarker's frameId is already relative to
    ``GetStartFrame()``. This checks that rather than assuming it, the same
    way markers.py refuses to assume a timeline starts at frame 0.
    """
    start_frame = _call(timeline, "GetStartFrame")
    if start_frame is None:
        result.warn("Timeline.GetStartFrame() returned nothing -- writing markers at plan-relative frames.")
        return 0
    if not items:
        return 0
    first_start = _call(items[0], "GetStart")
    if first_start is None:
        return 0
    try:
        offset = int(first_start) - int(start_frame)
    except (TypeError, ValueError):
        return 0
    if offset:
        result.warn(
            "The first appended clip starts %d frame(s) after the timeline's start frame; "
            "offsetting every marker to match.", offset,
        )
    return offset


def write_edit_markers(timeline, edit_markers: list, *, origin_frame: int = 0) -> dict:
    """Write the planned markers onto a freshly built timeline.

    ``AddMarker()`` takes a frame relative to the timeline's start, and a
    plan's ``timeline_in_frame`` is already 0-based from the start of the
    assembly, so the two coincide and ``origin_frame`` is normally 0 --
    :func:`_marker_origin` verifies that against the live timeline instead
    of assuming it.

    Returns ``{"written": int, "failed": int, "offset": int}``. Never raises:
    a build whose clips are all correct but whose markers didn't take is
    still a usable assembly, so marker failures are counted and reported.
    """
    existing = _call(timeline, "GetMarkers")
    used = set(existing.keys()) if isinstance(existing, dict) else set()

    written = failed = offset_count = 0
    for marker in edit_markers:
        frame = origin_frame + marker.timeline_frame
        if frame < 0:
            logger.warning("Marker for segment %s computed to negative frame %d -- skipping", marker.segment_id, frame)
            failed += 1
            continue
        while frame in used:
            frame += 1
            offset_count += 1

        ok = _call(
            timeline, "AddMarker",
            frame, marker.color, marker.name, marker.note,
            marker.duration_frames, json.dumps(marker.custom_data),
        )
        if not ok:
            logger.error("Resolve rejected the marker for segment %s at frame %d", marker.segment_id, frame)
            failed += 1
            continue
        used.add(frame)
        written += 1

    return {"written": written, "failed": failed, "offset": offset_count}


# --------------------------------------------------------------------------
# Multicam (optional, best-effort)
# --------------------------------------------------------------------------

def load_multicam(path) -> Optional[dict]:
    """Load a ``multicam.json`` written by :mod:`theodore.resolve.multicam`,
    or None if it doesn't exist / isn't readable. Multicam is always
    optional: the common case is no multicam.json at all."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s -- building without multicam", path)
        return None
    return data if isinstance(data, dict) else None


def multicam_clip_for_source(multicam: Optional[dict], source_path: str) -> Optional[str]:
    """Name of the multicam clip whose angles include `source_path`, per a
    loaded multicam.json, or None. Defensive by design -- an unexpected
    shape returns None and the build uses the plain source clip."""
    if not isinstance(multicam, dict):
        return None
    for group in _as_list(multicam.get("groups")):
        if not isinstance(group, dict):
            continue
        clip_name = group.get("multicam_clip_name")
        if not clip_name:
            continue
        for angle in _as_list(group.get("angles")):
            if isinstance(angle, dict) and (
                _same_file(angle.get("path"), source_path)
                or Path(str(angle.get("path") or "")).name == Path(str(source_path)).name
            ):
                return str(clip_name)
    return None


# --------------------------------------------------------------------------
# The live build
# --------------------------------------------------------------------------

def build_timeline(
    handles: ResolveHandles,
    plan: list[AssemblyClip],
    *,
    transcript: dict,
    analysis: dict,
    name: Optional[str] = None,
    project: Optional[str] = None,
    subject: Optional[str] = None,
    mode: str = "custom",
    trims: Optional[dict] = None,
    rationale: Optional[str] = None,
    multicam: Optional[dict] = None,
    timestamp: Optional[str] = None,
    frame_origin: str = "auto",
    allow_fps_mismatch: bool = False,
    write_markers: bool = True,
) -> BuildResult:
    """Build `plan` as a NEW timeline in the connected Resolve project.

    Never modifies an existing timeline: it creates one, verifies Resolve
    made that new timeline current, and only then appends. If the current
    timeline is anything else, the build aborts rather than risk appending
    an assembly into the editor's real cut.

    `plan` is built exactly as given, in the order given. Where it came from
    -- an ordering pass, or a persisted edit list being rebuilt -- makes no
    difference here, and no argument other than the timeline's name assumes
    one or the other.

    Parameters
    ----------
    handles:
        From :func:`resolve.connection.connect`. Its ``.project`` supplies
        the media pool; ``.timeline`` is left alone (and, on success,
        re-pointed at the newly built timeline, which is now the current
        one).
    plan:
        An ordered ``list[AssemblyClip]``, normally from
        :func:`assembly.plan.build_plan` -- the single source of truth for
        every in/out frame. Nothing here recomputes or reorders it.
    name:
        The exact name for the new timeline. Omit it to get
        :func:`timeline_name`'s
        ``THEODORE_<project>_<subject>_<mode>_<timestamp>``, in which case
        `project` and `subject` are required.
    project, subject, mode:
        Plain strings used only to build the default timeline name. `mode`
        is a free-form label -- an ordering strategy ("narrative"), an edit
        list version ("v003"), or anything else; nothing branches on its
        value except that `rationale` is described as belonging to it.
    transcript, analysis:
        Already-loaded dicts (not paths). ``transcript["source_file"]`` is
        the media to find-or-import; ``analysis`` supplies marker text.
    trims:
        Optional, already-loaded trims dict. Used only to annotate markers
        with what trimming removed -- the frame math already consumed it in
        ``build_plan``.
    rationale:
        Narrative-mode story rationale; lands in the first marker's note.
    multicam:
        Optional, already-loaded ``multicam.json`` dict. If it records a
        multicam clip covering this source and that clip is in the media
        pool, it is appended instead of the plain source clip. Missing or
        unmatched multicam data is not an error.
    name, timestamp:
        Override the generated timeline name (or just its timestamp).
    frame_origin:
        How ``AssemblyClip.source_in_frame`` (absolute, source-timecode
        space) becomes the in-point handed to ``AppendToTimeline``:
        ``"auto"`` (default) reads the clip's own ``Start TC``/``Start``
        properties and falls back to the transcript's ``start_timecode``;
        ``"media"`` always uses the transcript's ``start_timecode``;
        ``"absolute"`` passes plan.py's frame numbers through untouched.
        ``"absolute"`` is correct only if this Resolve version's clipInfo
        in/out points are in embedded-timecode space rather than counted
        from the start of the media.
    allow_fps_mismatch:
        Build even when the project's timeline frame rate differs from the
        source media's. Off by default; a mismatch silently misplaces every
        cut.
    write_markers:
        Set False to build the cut without annotating it.

    Raises
    ------
    BuilderError
        On any Resolve call that reports failure, and on any mismatch
        between the plan and what actually landed on the timeline.
    """
    if frame_origin not in FRAME_ORIGIN_MODES:
        raise ValueError(f"Unknown frame_origin {frame_origin!r}, expected one of {FRAME_ORIGIN_MODES}")

    fps = transcript["fps"]
    result = BuildResult(
        timeline_name=name or timeline_name(project, subject, mode, timestamp=timestamp),
        clips_planned=len(plan),
        runtime_frames=assembly_plan.total_runtime_frames(plan),
        fps=str(fps),
    )

    if not plan:
        raise BuilderError(
            "The assembly plan is empty -- nothing to build. Every segment was either "
            "excluded or could not be resolved against the transcript."
        )

    resolve_project = handles.project
    if resolve_project is None:
        raise BuilderError("No Resolve project on these handles -- reconnect with resolve.connection.connect().")

    media_pool = _call(resolve_project, "GetMediaPool")
    if not media_pool:
        raise BuilderError(
            "Project.GetMediaPool() returned nothing. Theodore cannot build an assembly "
            "without the media pool -- try restarting Resolve with the project open."
        )

    _check_frame_rate(resolve_project, fps, result, allow_fps_mismatch)

    source_path = transcript.get("source_file")
    if not source_path:
        raise BuilderError(
            "This transcript has no 'source_file' -- Theodore can't tell which media to "
            "build from. Re-run `theodore transcribe` for this subject."
        )

    # ---- Everything that can fail without side effects happens BEFORE the
    # ---- timeline is created, so a failure never leaves a half-built one.
    media_item = find_or_import_media(media_pool, source_path, result)

    multicam_name = multicam_clip_for_source(multicam, source_path)
    if multicam_name:
        mc_item = find_media_pool_item_by_name(media_pool, multicam_name)
        if mc_item is not None:
            media_item = mc_item
            result.multicam_clips_used.append(multicam_name)
            logger.info("Using multicam clip %r instead of the plain source clip", multicam_name)
        else:
            result.warn(
                "multicam.json names a multicam clip %r for this source, but no clip with "
                "that name is in the media pool -- building from the plain source clip. "
                "Create the multicam clip in Resolve first if you want angle switching.",
                multicam_name,
            )

    transcript_start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)
    if frame_origin == "absolute":
        origin, origin_source = 0, "frame_origin='absolute' (source frames passed through)"
    elif frame_origin == "media":
        origin, origin_source = transcript_start_frame, "transcript start_timecode (forced)"
    else:
        origin, origin_source = media_item_frame_origin(media_item, transcript_start_frame, fps)
    logger.info(
        "Source in/out points offset by %d frame(s) from %s", origin, origin_source,
    )

    clip_infos = _build_clip_infos(media_item, plan, origin, origin_source, fps, result)

    # ---- From here on, Resolve state changes.
    timeline = _call(media_pool, "CreateEmptyTimeline", result.timeline_name)
    if not timeline:
        raise BuilderError(
            f"MediaPool.CreateEmptyTimeline({result.timeline_name!r}) failed. "
            "Resolve returns no timeline (rather than an error) when the name is already "
            "taken or the project is read-only -- check for an existing timeline with "
            "that name in the media pool."
        )
    result.timeline = timeline
    logger.info("Created timeline %s", result.timeline_name)

    _make_current(resolve_project, timeline, result)

    appended = _call(media_pool, "AppendToTimeline", clip_infos)
    appended_list = _as_list(appended)
    if not appended_list:
        raise BuilderError(
            f"MediaPool.AppendToTimeline() appended nothing to '{result.timeline_name}'. "
            "The empty timeline was created and is still there -- delete it. This usually "
            "means the requested in/out points fall outside the source clip: the plan asks "
            f"for source frames {plan[0].source_in_frame}-{plan[-1].source_out_frame} "
            f"(offset by {origin} using {origin_source})."
        )
    if len(appended_list) != len(plan):
        result.warn(
            "AppendToTimeline returned %d item(s) for %d planned clip(s) -- verifying "
            "against the timeline itself.", len(appended_list), len(plan),
        )

    items = _verify_appended(timeline, plan, result.timeline_name, result)
    result.clips_appended = len(items) if items else len(appended_list)

    if write_markers:
        edit_markers = plan_edit_markers(
            plan, transcript, analysis, mode=mode, trims=trims, rationale=rationale,
        )
        marker_stats = write_edit_markers(
            timeline, edit_markers, origin_frame=_marker_origin(timeline, items, result),
        )
        result.markers_written = marker_stats["written"]
        result.markers_failed = marker_stats["failed"]
        if marker_stats["failed"]:
            result.warn(
                "%d of %d markers were rejected by Resolve -- the cut itself is fine.",
                marker_stats["failed"], len(edit_markers),
            )

    # The new timeline is now the current one; keep the handles honest so a
    # caller can go straight on to e.g. captions.import_subtitles_to_timeline.
    handles.timeline = timeline
    return result


def _build_clip_infos(
    media_item,
    plan: list[AssemblyClip],
    origin: int,
    origin_source: str,
    fps,
    result: BuildResult,
) -> list:
    """The clipInfo dicts for AppendToTimeline, with in/out points converted
    out of absolute source-timecode space, validated against the media's own
    bounds where Resolve exposes them."""
    media_end = None
    for prop in ("End", "Frames"):
        raw = _clip_property(media_item, prop)
        if raw is None:
            continue
        try:
            media_end = int(raw)
        except (TypeError, ValueError):
            continue
        break

    clip_infos = []
    for clip in plan:
        start = clip.source_in_frame - origin
        end = clip.source_out_frame - origin
        if start < 0:
            raise BuilderError(
                f"Segment {clip.segment_id} resolves to source frame {start} "
                f"(source_in_frame {clip.source_in_frame} minus an origin of {origin} "
                f"from {origin_source}), which is before the start of the media. The "
                "media's timecode and the transcript's start_timecode disagree -- "
                "re-ingest the file, or pass frame_origin='absolute' if this Resolve "
                "version expects absolute timecode frames."
            )
        if media_end is not None and end > media_end + 1:
            result.warn(
                "Segment %s asks for source frames %d-%d but the clip reports only %d "
                "frames; Resolve will clamp this edit.",
                clip.segment_id, start, end, media_end,
            )
        clip_infos.append({
            "mediaPoolItem": media_item,
            "startFrame": start,
            "endFrame": end,
        })
    return clip_infos


def _make_current(resolve_project, timeline, result: BuildResult) -> None:
    """Make the new timeline current, then PROVE it is.

    AppendToTimeline targets the current timeline. If Resolve did not switch
    -- SetCurrentTimeline returns False rather than raising -- appending
    would write the whole assembly into whatever timeline the editor had
    open. That is the one outcome this module must never allow, so a
    failure to confirm is fatal.
    """
    ok = _call(resolve_project, "SetCurrentTimeline", timeline)
    if not ok:
        result.warn(
            "Project.SetCurrentTimeline() did not report success for '%s' -- verifying "
            "directly before appending.", result.timeline_name,
        )

    current = _call(resolve_project, "GetCurrentTimeline")
    current_name = _call(current, "GetName") if current is not None else None
    if current_name != result.timeline_name:
        raise BuilderError(
            f"Refusing to append: Resolve's current timeline is "
            f"{current_name!r}, not the newly created {result.timeline_name!r}. "
            "AppendToTimeline writes to the current timeline, and Theodore will not "
            "risk appending an assembly into an existing cut. The new (empty) timeline "
            "was created -- open it in Resolve and re-run, or delete it."
        )


def format_result(result: BuildResult) -> str:
    """Human-readable summary of a completed build, for CLI output."""
    if result.fps:
        runtime = f"{tc.frames_to_timecode(result.runtime_frames, result.fps)} ({result.runtime_frames} frames)"
    else:
        runtime = f"{result.runtime_frames} frames"
    markers_line = f"  markers:  {result.markers_written} written"
    if result.markers_failed:
        markers_line += f", {result.markers_failed} rejected"

    lines = [
        f"Built timeline '{result.timeline_name}'",
        f"  clips:    {result.clips_appended}/{result.clips_planned}",
        f"  runtime:  {runtime}",
        markers_line,
    ]
    if result.imported_paths:
        lines.append(f"  imported: {', '.join(result.imported_paths)}")
    if result.multicam_clips_used:
        lines.append(f"  multicam: {', '.join(result.multicam_clips_used)}")
    for warning in result.warnings:
        lines.append(f"  WARNING:  {warning}")
    return "\n".join(lines)
