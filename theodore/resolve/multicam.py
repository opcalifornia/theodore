"""Detects which of a subject's source files are camera angles of the same
simultaneous recording, and which audio-only files (a lav or boom
recorder's own take) belong with which camera(s) -- writing both into the
``multicam.json`` that :mod:`theodore.assembly.builder` consumes.

Theodore never creates the Multicam Clip itself. Resolve's scripting API has
no supported call for it, and guessing wrong would silently reshape an
editor's timeline. So the split of labour is:

  * Theodore decides *which files are angles of the same moment* and *what
    the multicam clip must be named*.
  * The editor selects those files in Resolve, right-clicks -> "New Multicam
    Clip Using...", and names it exactly what Theodore printed.
  * ``builder.build_timeline()`` looks the clip up by that exact name and,
    if it exists, builds from it instead of the plain source clip. If it
    doesn't exist yet the build warns and falls back -- never an error.

Two deliberately separate steps, the same split as
:mod:`theodore.resolve.markers`:

  1. :func:`plan_multicam` -- pure, Resolve-independent, operates only on
     ``media.json`` data. Fully unit-testable with no Resolve running.
  2. :func:`check_multicam_clips` -- the only function here that touches
     Resolve. Purely informational: it reports which proposed clip names
     already exist in the media pool so the CLI can tell the editor what is
     still left to create. Optional; everything works without it.

Camera-to-external-audio: :func:`plan_audio_sync`
--------------------------------------------------
The same split of labour applies to a lav or boom recorder's own audio-only
file: Theodore identifies which camera file(s) it belongs with and tells the
editor exactly what to do, but never touches the audio itself. Deliberately
NOT implemented as Theodore re-computing a waveform cross-correlation offset:
Resolve's own Media Pool already has this exact feature (right-click ->
"Auto Sync Audio" -> "Based on Waveform and Append Tracks"), production-
tested against clock drift and dropouts in ways a from-scratch reimplementation
here would not be, and it also decides which existing audio track to keep
alongside the new one. Recomputing that math in Python would be the "guessing
wrong reshapes the editor's timeline" risk this module's docstring already
warns about, aimed at a problem Resolve has already solved.

An external recorder is essentially never jam-synced to camera timecode in an
indie/documentary setup (that needs dedicated hardware most solo shooters
don't have) -- so unlike video-to-video grouping above, this does not require
matching embedded timecode. When exactly one audio-only file exists for a
subject, it is recommended for every camera file that subject has, timecode
or not: one continuous external recording covering the whole session is the
standard single/dual-camera interview setup. Multiple audio-only files for one
subject are a real ambiguity Theodore cannot resolve from metadata alone (no
signal says which file goes with which take) and are reported as such rather
than guessed.


The comparison space: "timecode label seconds"
----------------------------------------------
Overlap is never computed on raw frame counts. A frame is a different amount
of real time at 25 fps than at 23.976 fps, so comparing frame counts across
rates compares unlike quantities.

Instead every file's range is expressed in *timecode label seconds since
00:00:00:00*::

    start = timecode_to_frames(start_timecode, fps) / nominal_fps(fps)
    end   = start + seconds_to_frames(duration_seconds, fps) / nominal_fps(fps)

All exact ``Fraction`` arithmetic -- no rounded floats anywhere in the
comparison, which is this project's hardest rule.

Why *label* seconds rather than real elapsed seconds: cameras are synced by
their timecode *label*. Two jam-synced cameras both reading 01:00:00:00 are
at the same instant whether they run at 23.976 or 25. Converting each to
real elapsed time instead (frames / exact fps) would place the 23.976 camera
3.6 seconds away from the 25 fps one purely because NTSC non-drop labels
drift against wall clock -- a fake offset invented by the unit conversion.
Dividing the real frame count by the *nominal* rate keeps both cameras in the
shared label space they were actually synced in, and stays exact.

Cross-rate groups are therefore compared correctly, but they are still
flagged in the output: a mixed-frame-rate multicam clip is a real editorial
gotcha in Resolve (angles get conformed), and the editor should know before
they build it.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

from theodore.resolve import timecode as tc
from theodore.resolve.connection import ResolveHandles

logger = logging.getLogger("theodore.resolve.multicam")


# --------------------------------------------------------------------------
# Thresholds -- deliberately conservative, and deliberately named so they are
# auditable rather than buried in an expression.
# --------------------------------------------------------------------------
#
# A pair of files is treated as two angles of the same moment only when BOTH
# of these hold:
#
#   1. CONTAINMENT: the overlap covers at least this fraction of the SHORTER
#      file. "While this camera was rolling, the other one was rolling too,
#      essentially the whole time."
#
#   2. COINCIDENCE: the overlap covers at least this fraction of the UNION of
#      the two ranges. "The two cameras genuinely ran together", as opposed to
#      a short clip that merely happens to sit inside a much longer one.
#
# Condition 2 is what the spec is really asking for. Without it, a 10-minute
# second-camera file sitting anywhere inside a 60-minute A-cam scores 100% on
# containment alone, even though 50 of those 60 minutes have no second angle
# at all. That is a much weaker signal than two cameras that started and
# stopped together, and it is exactly the case worth refusing.
#
# The costs here are asymmetric, which is why both thresholds sit high:
# a false negative just means the build uses the plain source clip (the
# normal, safe path, and the editor can still make a multicam clip by hand),
# while a false positive tells the editor to construct a multicam clip that
# does not represent a real coincident recording.
MIN_CONTAINMENT_OF_SHORTER = Fraction(80, 100)
MIN_OVERLAP_OF_UNION = Fraction(50, 100)

# ffprobe writes this when a file carries no embedded timecode tag at all
# (see theodore.ingest.media.probe). It is a fallback default, NOT evidence
# that the camera started at frame zero, so it can never be used as sync
# evidence. Any timecode that resolves to absolute frame 0 is treated the
# same way -- "00:00:00;00" is the same non-signal as "00:00:00:00".
NO_TIMECODE_SENTINEL = "00:00:00:00"

# Clique enumeration is exponential in the worst case. Real subjects have a
# handful of camera files; this guard keeps a pathological media.json from
# hanging the CLI.
_MAX_CANDIDATES_FOR_GROUPING = 24


class MulticamError(RuntimeError):
    pass


@dataclass
class Angle:
    """One video file that is eligible to be a multicam angle."""

    path: str
    fps: str
    start_timecode: str
    duration_seconds: float
    start_frames: int
    duration_frames: int
    # Both in "timecode label seconds since 00:00:00:00", exact Fractions.
    start: Fraction
    end: Fraction

    @property
    def duration(self) -> Fraction:
        return self.end - self.start

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class Group:
    multicam_clip_name: str
    angles: list[Angle]
    mixed_frame_rates: bool = False

    @property
    def paths(self) -> list[str]:
        return [a.path for a in self.angles]


@dataclass
class MulticamPlan:
    """The full, auditable result of a detection run."""

    subject: str
    groups: list[Group] = field(default_factory=list)
    # Files that could not even be considered, each with a plain reason.
    skipped: list[dict] = field(default_factory=list)
    # Groupings Theodore refused to guess at, each with a plain reason.
    ambiguous: list[dict] = field(default_factory=list)
    # Files that were considered but matched nothing.
    ungrouped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Audio-only files (a lav or boom recorder's own take) this subject has,
    # each with which camera file(s) to select alongside it in Resolve. See
    # plan_audio_sync().
    external_audio: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The exact on-disk shape. ``groups[].multicam_clip_name`` and
        ``groups[].angles[].path`` are the contract
        :func:`theodore.assembly.builder.multicam_clip_for_source` reads;
        every other key is informational only."""
        return {
            "subject": self.subject,
            "generated_by": "theodore.resolve.multicam",
            "thresholds": {
                "min_containment_of_shorter": float(MIN_CONTAINMENT_OF_SHORTER),
                "min_overlap_of_union": float(MIN_OVERLAP_OF_UNION),
            },
            "groups": [
                {
                    "multicam_clip_name": g.multicam_clip_name,
                    "angles": [
                        {
                            "path": a.path,
                            "fps": a.fps,
                            "start_timecode": a.start_timecode,
                            "duration_seconds": a.duration_seconds,
                        }
                        for a in g.angles
                    ],
                    "mixed_frame_rates": g.mixed_frame_rates,
                }
                for g in self.groups
            ],
            "skipped": list(self.skipped),
            "ambiguous": list(self.ambiguous),
            "ungrouped": list(self.ungrouped),
            "notes": list(self.notes),
            "external_audio": list(self.external_audio),
        }


# --------------------------------------------------------------------------
# Step 1: pure detection
# --------------------------------------------------------------------------

def _sanitize_for_clip_name(subject: str) -> str:
    """Resolve clip names are matched byte-for-byte by the builder, so keep
    them to characters that survive a round trip through Resolve's UI."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(subject or "")).strip("_")
    return cleaned or "subject"


def clip_name_for(subject: str, index: int) -> str:
    """Deterministic multicam clip name: same media.json in, same name out.

    Groups are numbered from 1 in order of their earliest angle's start
    timecode, so re-running after adding an unrelated file does not reshuffle
    the names of groups that were already there.
    """
    return f"{_sanitize_for_clip_name(subject)}_multicam_{index}"


def _to_angle(entry: dict) -> tuple[Optional[Angle], Optional[str]]:
    """Convert one media.json entry into an :class:`Angle`, or return the
    reason it cannot be one. Never raises."""
    path = str(entry.get("path") or "")
    if not path:
        return None, "media.json entry has no path"

    if not entry.get("has_video"):
        # Audio-only files are never camera angles. Excluded outright.
        return None, "audio-only (has_video is false) -- never a camera angle"

    start_tc = str(entry.get("start_timecode") or "")
    fps_raw = entry.get("fps")

    try:
        fps = tc.parse_fps(fps_raw)
    except tc.TimecodeError as exc:
        return None, f"unusable frame rate {fps_raw!r} ({exc})"
    if fps <= 0:
        return None, f"frame rate {fps_raw!r} is not a real rate -- ffprobe found no video frame rate"

    nominal = tc.nominal_fps(fps)
    if nominal <= 0:
        return None, f"frame rate {fps_raw!r} rounds to a nominal 0 fps"

    try:
        start_frames = tc.timecode_to_frames(start_tc, fps)
    except tc.TimecodeError as exc:
        return None, f"unreadable start timecode {start_tc!r} ({exc})"

    if start_frames == 0:
        # THE important conservative rule. ffprobe writes 00:00:00:00 when a
        # file has no embedded timecode tag -- it is the absence of a signal,
        # not a claim that the camera rolled at frame zero. Grouping on it
        # would sync every untimecoded file in the folder to every other.
        return None, (
            f"start_timecode is {start_tc!r} -- that is ffprobe's no-embedded-timecode "
            "fallback, not a real synced start, so there is no signal to compare"
        )

    duration_seconds = entry.get("duration_seconds")
    try:
        duration_seconds = float(duration_seconds)
    except (TypeError, ValueError):
        return None, f"unusable duration_seconds {entry.get('duration_seconds')!r}"
    if duration_seconds <= 0:
        return None, f"duration_seconds is {duration_seconds} -- nothing to overlap"

    duration_frames = tc.seconds_to_frames(duration_seconds, fps)
    if duration_frames <= 0:
        return None, f"duration {duration_seconds}s is under one frame at {fps_raw}"

    start = Fraction(start_frames, nominal)
    end = start + Fraction(duration_frames, nominal)

    return Angle(
        path=path,
        fps=str(fps_raw),
        start_timecode=start_tc,
        duration_seconds=duration_seconds,
        start_frames=start_frames,
        duration_frames=duration_frames,
        start=start,
        end=end,
    ), None


def overlap_scores(a: Angle, b: Angle) -> tuple[Fraction, Fraction]:
    """(containment_of_shorter, overlap_of_union) for two angles, exact.

    Both are 0 when the ranges do not overlap at all.
    """
    overlap = min(a.end, b.end) - max(a.start, b.start)
    if overlap <= 0:
        return Fraction(0), Fraction(0)

    shorter = min(a.duration, b.duration)
    union = max(a.end, b.end) - min(a.start, b.start)
    if shorter <= 0 or union <= 0:
        return Fraction(0), Fraction(0)
    return overlap / shorter, overlap / union


def is_synced_pair(a: Angle, b: Angle) -> bool:
    """True when two angles pass BOTH thresholds -- see the module comment on
    MIN_CONTAINMENT_OF_SHORTER / MIN_OVERLAP_OF_UNION for the reasoning."""
    containment, coincidence = overlap_scores(a, b)
    return containment >= MIN_CONTAINMENT_OF_SHORTER and coincidence >= MIN_OVERLAP_OF_UNION


def _maximal_cliques(n: int, adjacency: dict[int, set[int]]) -> list[set[int]]:
    """All maximal cliques (Bron-Kerbosch). n is small by construction."""
    cliques: list[set[int]] = []

    def expand(r: set[int], p: set[int], x: set[int]) -> None:
        if not p and not x:
            if len(r) >= 2:
                cliques.append(set(r))
            return
        pivot = max(p | x, key=lambda v: len(adjacency[v]))
        for v in sorted(p - adjacency[pivot]):
            expand(r | {v}, p & adjacency[v], x & adjacency[v])
            p = p - {v}
            x = x | {v}

    expand(set(), set(range(n)), set())
    return cliques


def plan_multicam(media: list[dict], subject: str) -> MulticamPlan:
    """Detect multicam angle groups from a subject's ``media.json`` data.

    Pure function: no Resolve, no filesystem, no network. This is the whole
    requirement -- :func:`check_multicam_clips` is optional polish on top.

    A group is emitted only when EVERY pair in it passes
    :func:`is_synced_pair`. Connected components are deliberately not used: a
    Resolve multicam clip syncs all of its angles together, so a chain where
    A overlaps B and B overlaps C but A does not overlap C must never become
    one clip. When such a chain appears, the file in the middle belongs to
    two competing candidate groups and Theodore refuses to pick one -- the
    conflict is recorded in ``plan.ambiguous`` and reported to the editor
    instead of being resolved by a silent coin flip.
    """
    plan = MulticamPlan(subject=str(subject))
    # Computed unconditionally, up front: audio-sync recommendations don't
    # depend on whether any camera grouping succeeds, and several branches
    # below return early once the video-angle question is settled.
    plan.external_audio = plan_audio_sync(media, subject)

    candidates: list[Angle] = []
    for entry in media or []:
        if not isinstance(entry, dict):
            plan.skipped.append({"path": repr(entry), "reason": "media.json entry is not an object"})
            continue
        angle, reason = _to_angle(entry)
        if angle is None:
            plan.skipped.append({"path": str(entry.get("path") or repr(entry)), "reason": reason})
            logger.info("Not a multicam candidate: %s -- %s", entry.get("path"), reason)
        else:
            candidates.append(angle)

    # Deterministic order: by start, then path. Everything downstream (group
    # numbering, angle order inside a group) inherits this.
    candidates.sort(key=lambda a: (a.start, a.path))

    if len(candidates) < 2:
        if len(candidates) == 1:
            plan.ungrouped = [candidates[0].path]
            plan.notes.append(
                "Only one video file has usable embedded timecode -- a single camera is not "
                "multicam, so no groups were proposed."
            )
        else:
            plan.notes.append("No video files with usable embedded timecode -- nothing to group.")
        return plan

    if len(candidates) > _MAX_CANDIDATES_FOR_GROUPING:
        plan.notes.append(
            f"{len(candidates)} timecoded video files is more than this pass will group "
            f"automatically (limit {_MAX_CANDIDATES_FOR_GROUPING}). No groups proposed -- "
            "narrow the subject's media and re-run."
        )
        plan.ungrouped = [a.path for a in candidates]
        logger.warning("Refusing to group %d candidates (limit %d)", len(candidates), _MAX_CANDIDATES_FOR_GROUPING)
        return plan

    n = len(candidates)
    adjacency: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if is_synced_pair(candidates[i], candidates[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)

    cliques = _maximal_cliques(n, adjacency)

    # A file appearing in more than one maximal clique is genuinely ambiguous
    # -- report it, never guess.
    membership: dict[int, list[int]] = {}
    for ci, clique in enumerate(cliques):
        for v in clique:
            membership.setdefault(v, []).append(ci)
    contested_cliques = {ci for v, cis in membership.items() if len(cis) > 1 for ci in cis}

    accepted: list[set[int]] = []
    for ci, clique in enumerate(cliques):
        if ci in contested_cliques:
            continue
        accepted.append(clique)

    for ci in sorted(contested_cliques):
        clique = cliques[ci]
        members = sorted(clique)
        overlapping = sorted(
            {other for v in members for other in membership.get(v, []) if other != ci}
        )
        others = [
            sorted(candidates[v].name for v in cliques[o]) for o in overlapping
        ]
        reason = (
            "these files overlap each other but not every other file in the competing "
            "grouping, so they cannot all sync into one multicam clip; Theodore will not "
            "choose between the candidate groupings"
        )
        plan.ambiguous.append({
            "paths": [candidates[v].path for v in members],
            "files": [candidates[v].name for v in members],
            "competing_with": others,
            "reason": reason,
        })
        logger.warning(
            "Ambiguous multicam grouping for %s -- competing groupings %s. Not grouped.",
            [candidates[v].name for v in members], others,
        )

    # Group numbering follows the earliest angle in each group, so names stay
    # stable when unrelated files are added to the subject later.
    def _clique_sort_key(clique: set[int]) -> tuple:
        starts = sorted((candidates[v].start, candidates[v].path) for v in clique)
        return (starts[0][0], starts[0][1])

    accepted.sort(key=_clique_sort_key)

    grouped: set[int] = set()
    for index, clique in enumerate(accepted, start=1):
        angles = sorted((candidates[v] for v in clique), key=lambda a: (a.start, a.path))
        rates = {tc.parse_fps(a.fps) for a in angles}
        group = Group(
            multicam_clip_name=clip_name_for(subject, index),
            angles=angles,
            mixed_frame_rates=len(rates) > 1,
        )
        plan.groups.append(group)
        grouped.update(clique)
        if group.mixed_frame_rates:
            plan.notes.append(
                f"{group.multicam_clip_name} mixes frame rates "
                f"({', '.join(sorted({a.fps for a in angles}))}). The overlap math handles "
                "that correctly, but Resolve will conform the angles when you build the "
                "multicam clip -- check it looks right."
            )

    plan.ungrouped = sorted(
        candidates[v].path for v in range(n)
        if v not in grouped and not any(v in cliques[ci] for ci in contested_cliques)
    )

    if not plan.groups and not plan.ambiguous:
        plan.notes.append(
            "No two files overlap enough in timecode to be the same moment "
            f"(need {int(MIN_CONTAINMENT_OF_SHORTER * 100)}% of the shorter file covered and "
            f"{int(MIN_OVERLAP_OF_UNION * 100)}% of the combined span)."
        )

    return plan


# --------------------------------------------------------------------------
# Camera-to-external-audio sync recommendations
# --------------------------------------------------------------------------

def _camera_paths(media: list[dict]) -> list[str]:
    """Every file with a video stream, timecode or not. Unlike the multicam
    grouping candidates above, an external recorder does not need embedded
    timecode on the camera side either -- waveform-based sync doesn't care,
    so a camera file with no usable timecode still needs its audio matched
    just as much as one that has it."""
    return sorted({
        str(e["path"]) for e in (media or [])
        if isinstance(e, dict) and e.get("has_video") and e.get("path")
    })


def _audio_only_candidates(media: list[dict]) -> list[dict]:
    """Audio-only entries (a lav or boom recorder's own file) with a real
    duration to reason about. Mirrors _to_angle()'s exclusion of audio-only
    files from camera grouping -- this is the other side of that exclusion."""
    out = []
    for entry in media or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("has_video") or not entry.get("has_audio"):
            continue
        path = str(entry.get("path") or "")
        if not path:
            continue
        try:
            duration = float(entry.get("duration_seconds"))
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            continue
        start_tc = str(entry.get("start_timecode") or "")
        out.append({
            "path": path,
            "duration_seconds": duration,
            "has_embedded_timecode": bool(start_tc) and start_tc != NO_TIMECODE_SENTINEL,
            "start_timecode": start_tc,
        })
    return out


def plan_audio_sync(media: list[dict], subject: str) -> list[dict]:
    """One recommendation per audio-only file this subject has, naming which
    camera file(s) to select alongside it in Resolve. Pure and
    Resolve-independent, like :func:`plan_multicam`; see the module
    docstring for why this recommends a Resolve feature rather than
    computing a sync offset itself.

    Empty when there is nothing to recommend: no camera file, no audio-only
    file, or (implicitly) both -- there is nothing useful to say about an
    external recording with no camera to pair it with, or vice versa.
    """
    cameras = _camera_paths(media)
    audio_files = _audio_only_candidates(media)
    if not cameras or not audio_files:
        return []

    multiple = len(audio_files) > 1
    recommendations = []
    for audio in sorted(audio_files, key=lambda a: a["path"]):
        if audio["has_embedded_timecode"]:
            note = (
                f"Has embedded timecode ({audio['start_timecode']}). If this recorder was "
                "jam-synced to camera, select it with the matching camera file(s) and use "
                "Auto Sync Audio -> 'Based on Timecode and Append Tracks'; otherwise use "
                "'Based on Waveform and Append Tracks', which does not depend on it."
            )
        else:
            note = (
                "No embedded timecode -- select it with the camera file(s) below and use "
                "Auto Sync Audio -> 'Based on Waveform and Append Tracks', which does not "
                "need one."
            )
        if multiple:
            note += (
                f" {len(audio_files)} external audio files were found for this subject -- "
                "Theodore has no signal to tell which belongs with which take, so this is "
                "listed against every camera file. Confirm the right pairing by ear before "
                "syncing."
            )
        recommendations.append({
            "path": audio["path"],
            "duration_seconds": audio["duration_seconds"],
            "has_embedded_timecode": audio["has_embedded_timecode"],
            "start_timecode": audio["start_timecode"] if audio["has_embedded_timecode"] else None,
            "camera_files": cameras,
            "ambiguous": multiple,
            "note": note,
        })
    return recommendations


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def save_multicam(plan: MulticamPlan, subject_dir) -> Path:
    """Write ``multicam.json`` into the subject's directory."""
    subject_dir = Path(subject_dir)
    subject_dir.mkdir(parents=True, exist_ok=True)
    out = subject_dir / "multicam.json"
    out.write_text(json.dumps(plan.to_dict(), indent=2))
    return out


def load_multicam(subject_dir) -> Optional[dict]:
    """Read back a written ``multicam.json``, or None."""
    path = Path(subject_dir) / "multicam.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read %s", path)
        return None


# --------------------------------------------------------------------------
# Step 2: the only Resolve-touching function (optional)
# --------------------------------------------------------------------------

def check_multicam_clips(handles: ResolveHandles, plan: MulticamPlan) -> dict:
    """Which proposed clip names already exist in the open project's media
    pool, and which the editor still has to create.

    Read-only and purely informational -- it creates nothing and changes
    nothing. Everything in this module works without it; it exists so
    ``theodore multicam`` can say "you still need to build these two"
    instead of leaving the editor to check by hand.
    """
    # Imported here so the pure path never pulls in the assembly package.
    from theodore.assembly.builder import find_media_pool_item_by_name

    media_pool = handles.project.GetMediaPool()
    if media_pool is None:
        raise MulticamError(
            "Connected to Resolve but the project returned no media pool -- "
            "try restarting Resolve with the project open."
        )

    existing, missing = [], []
    for group in plan.groups:
        name = group.multicam_clip_name
        if find_media_pool_item_by_name(media_pool, name) is not None:
            existing.append(name)
        else:
            missing.append(name)
    return {"existing": existing, "missing": missing}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def format_plan(plan: MulticamPlan) -> str:
    """Human-readable report -- what got grouped, and what the editor must
    now go and create in Resolve, under exactly which name."""
    lines: list[str] = []

    if plan.groups:
        for group in plan.groups:
            lines.append("")
            lines.append(f"  Multicam clip name:  {group.multicam_clip_name}")
            if group.mixed_frame_rates:
                lines.append("  (mixed frame rates -- Resolve will conform the angles)")
            for angle in group.angles:
                lines.append(
                    f"    - {angle.name}   start {angle.start_timecode}  "
                    f"{angle.fps} fps  {angle.duration_seconds:.1f}s"
                )
    else:
        lines.append("")
        lines.append("  No multicam groups detected.")

    for item in plan.ambiguous:
        lines.append("")
        lines.append(f"  AMBIGUOUS, not grouped: {', '.join(item['files'])}")
        lines.append(f"    {item['reason']}.")
        for other in item.get("competing_with") or []:
            lines.append(f"    competing grouping: {', '.join(other)}")

    if plan.ungrouped:
        lines.append("")
        lines.append("  No matching angle (builds as a plain clip, which is normal):")
        for path in plan.ungrouped:
            lines.append(f"    - {Path(path).name}")

    if plan.skipped:
        lines.append("")
        lines.append("  Not considered:")
        for item in plan.skipped:
            lines.append(f"    - {Path(item['path']).name}: {item['reason']}")

    for note in plan.notes:
        lines.append("")
        lines.append(f"  Note: {note}")

    for rec in plan.external_audio:
        lines.append("")
        lines.append(f"  External audio:  {Path(rec['path']).name}  ({rec['duration_seconds']:.1f}s)")
        lines.append(f"    {rec['note']}")
        lines.append("    Select it together with:")
        for camera_path in rec["camera_files"]:
            lines.append(f"      - {Path(camera_path).name}")

    return "\n".join(lines)
