"""SRT/WebVTT export + pull-quote sheet (v1.5 addendum, module 4).

Caption timing is remapped onto the ASSEMBLY timeline (assembly/plan.py's
per-clip frame math), not the source media's timecode -- a caption for the
third clip in an assembly needs to land at 00:00:47 on the new timeline,
not at whatever time that word was originally spoken in the source file.
Only words that survived trimming (assembly.trim.kept_words) get captions;
a cut word doesn't get a caption for time that no longer exists in the cut.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from theodore.assembly import builder as assembly_builder
from theodore.assembly.plan import AssemblyClip
from theodore.assembly.trim import kept_words
from theodore.resolve import timecode as tc
from theodore.resolve.connection import ResolveHandles

logger = logging.getLogger("theodore.export.captions")

MAX_CUE_CHARS = 84
MAX_CUE_SECONDS = 6.0
CUE_BREAK_GAP_SECONDS = 0.5


@dataclass
class Cue:
    start_seconds: float
    end_seconds: float
    text: str


def _word_assembly_seconds(word: dict, clip: AssemblyClip, source_start_frame: int, fps) -> tuple:
    def to_assembly_frame(t: float) -> int:
        source_frame = source_start_frame + tc.seconds_to_frames(t, fps)
        offset = source_frame - clip.source_in_frame
        return clip.timeline_in_frame + offset

    start_frame = max(clip.timeline_in_frame, to_assembly_frame(word["start"]))
    end_frame = min(clip.timeline_out_frame, to_assembly_frame(word["end"]))
    return tc.frames_to_seconds(start_frame, fps), tc.frames_to_seconds(end_frame, fps)


def build_cues(transcript: dict, analysis: dict, trims: dict, plan: list[AssemblyClip]) -> list[Cue]:
    """One or more caption cues per assembled clip, in assembly-timeline
    order. Cues break on a natural pause, a length/duration cap, or a clip
    boundary -- two different segments concatenated in an assembly never
    share one caption cue."""
    fps = transcript["fps"]
    source_start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)
    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}

    cues: list[Cue] = []
    for clip in plan:
        seg = segments_by_id.get(clip.segment_id)
        if seg is None:
            continue
        sel = selects_by_id.get(clip.segment_id)
        trim = trims.get(clip.segment_id)
        words = kept_words(seg, sel, transcript, trim)
        if not words:
            continue

        group: list[dict] = []
        group_start: Optional[float] = None
        group_end: Optional[float] = None

        def flush():
            if group:
                cues.append(Cue(start_seconds=group_start, end_seconds=group_end, text=" ".join(w["word"] for w in group)))

        for w in words:
            w_start, w_end = _word_assembly_seconds(w, clip, source_start_frame, fps)
            if group:
                gap = w_start - group_end
                prospective_len = len(" ".join(x["word"] for x in group)) + 1 + len(w["word"])
                if gap >= CUE_BREAK_GAP_SECONDS or prospective_len > MAX_CUE_CHARS or (w_end - group_start) > MAX_CUE_SECONDS:
                    flush()
                    group = []
            if not group:
                group_start = w_start
            group_end = w_end
            group.append(w)
        flush()

    return cues


def _source_moment_for_merged_second(transcript: dict, merged_seconds: float):
    """Which source file `merged_seconds` (a word's own start/end, already
    in merged-transcript-second space) really belongs to, and the FILE-
    RELATIVE second within it -- same offset logic
    assembly.builder._cut_ranges_by_source_index uses for dead-air, just
    resolving one instant instead of a range. Returns None if no utterance
    covers that instant (shouldn't happen for a word's own timestamp, but
    a stale/hand-edited transcript is not this function's problem to
    diagnose)."""
    sources = transcript.get("sources") or (
        [{"path": transcript["source_file"]}] if transcript.get("source_file") else []
    )
    for u in transcript.get("utterances", []):
        if u["start"] <= merged_seconds <= u["end"]:
            offset = u["start"] - u.get("source_start", u["start"])
            source_index = u.get("source_index", 0)
            if source_index >= len(sources):
                return None
            return sources[source_index].get("path"), merged_seconds - offset
    return None


def build_cues_for_open_timeline(
    handles: ResolveHandles, transcript: dict, analysis: dict, trims: dict,
) -> tuple[list[Cue], list[str]]:
    """Caption cues for the CURRENTLY OPEN Resolve timeline exactly as it
    is -- an editor's own synced timeline, or a duplicate remove-silence/
    trim-timeline produced -- instead of build_cues()'s fresh Theodore-built
    assembly. Every segment's kept words (assembly.trim.kept_words, so a
    trimmed-out filler word or dead-air gap gets no caption) are placed by
    finding where each word's own source file actually sits on the real
    timeline -- the same source-file matching
    assembly.builder.dead_air_timeline_cut_ranges uses -- rather than
    assuming clips are laid out the way a from-scratch assembly would.

    This works correctly on a trim-timeline/remove-silence duplicate too:
    those rebuild every clip from the SAME original media pool items with
    their own real GetSourceStartFrame(), so "which source frame is this"
    still holds even after ranges were removed and the rest rippled
    together.

    Returns `(cues, warnings)` -- a word whose source file isn't on the
    timeline (or isn't covered by any placed clip) is not an error: it's
    reported as a warning and simply has no caption, the same "unmatched
    isn't a failure" philosophy dead_air_timeline_cut_ranges uses.
    """
    fps = assembly_builder.read_timeline_fps(handles)

    sources = transcript.get("sources") or (
        [{"path": transcript["source_file"]}] if transcript.get("source_file") else []
    )
    known_paths = [s["path"] for s in sources if s.get("path")]
    matches = assembly_builder.match_timeline_to_sources(handles.timeline, known_paths)
    matches_by_path: dict = {}
    for m in matches:
        matches_by_path.setdefault(m.source_path, []).append(m)

    def timeline_seconds(merged_seconds: float):
        found = _source_moment_for_merged_second(transcript, merged_seconds)
        if found is None:
            return None
        path, file_seconds = found
        for m in matches_by_path.get(path, []):
            if m.source_start_frame is None:
                continue
            item_start_s = float(tc.frames_to_seconds(m.source_start_frame, fps))
            item_end_s = item_start_s + float(tc.frames_to_seconds(m.timeline_end_frame - m.timeline_start_frame, fps))
            if file_seconds >= item_start_s - 1e-6 and file_seconds <= item_end_s + 1e-6:
                frame = m.timeline_start_frame + tc.seconds_to_frames(file_seconds - item_start_s, fps)
                return float(tc.frames_to_seconds(frame, fps))
        return None

    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}

    cues: list[Cue] = []
    warnings: list[str] = []
    unmatched_paths: set = set()
    for seg in analysis["segments"]:
        sel = selects_by_id.get(seg["id"])
        trim = trims.get(seg["id"])
        words = kept_words(seg, sel, transcript, trim)
        if not words:
            continue

        group: list[dict] = []
        group_start = None
        group_end = None

        def flush():
            if group:
                cues.append(Cue(start_seconds=group_start, end_seconds=group_end, text=" ".join(w["word"] for w in group)))

        for w in words:
            w_start = timeline_seconds(w["start"])
            w_end = timeline_seconds(w["end"])
            if w_start is None or w_end is None or w_end <= w_start:
                found = _source_moment_for_merged_second(transcript, w["start"])
                if found and found[0] not in unmatched_paths:
                    unmatched_paths.add(found[0])
                    warnings.append(
                        f"{Path(found[0]).name if found[0] else '(unknown source)'} has no matching clip "
                        "on the timeline -- words from it have no caption."
                    )
                continue
            if group:
                gap = w_start - group_end
                prospective_len = len(" ".join(x["word"] for x in group)) + 1 + len(w["word"])
                if gap >= CUE_BREAK_GAP_SECONDS or prospective_len > MAX_CUE_CHARS or (w_end - group_start) > MAX_CUE_SECONDS:
                    flush()
                    group = []
            if not group:
                group_start = w_start
            group_end = w_end
            group.append(w)
        flush()

    cues.sort(key=lambda c: c.start_seconds)
    return cues, warnings


def _format_srt_time(seconds: float) -> str:
    total_ms = round(max(0.0, seconds) * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def write_srt(cues: list[Cue], out_path: Path) -> Path:
    lines = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(cue.start_seconds)} --> {_format_srt_time(cue.end_seconds)}")
        lines.append(cue.text)
        lines.append("")
    out_path.write_text("\n".join(lines))
    logger.info("Wrote %s (%d cues)", out_path, len(cues))
    return out_path


def write_vtt(cues: list[Cue], out_path: Path) -> Path:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{_format_vtt_time(cue.start_seconds)} --> {_format_vtt_time(cue.end_seconds)}")
        lines.append(cue.text)
        lines.append("")
    out_path.write_text("\n".join(lines))
    logger.info("Wrote %s (%d cues)", out_path, len(cues))
    return out_path


def import_subtitles_to_timeline(handles, srt_path: Path) -> bool:
    """Best-effort attempt to import the SRT directly onto the connected
    Resolve timeline. Resolve's scripting API does not consistently
    document a subtitle-import method across versions -- exactly the kind
    of call the project's own engineering notes require verifying against
    the installed version's bundled README before trusting. This never
    raises: on any failure (missing method, or the call itself erroring) it
    returns False and the caller falls back to the SRT/VTT file already on
    disk, which always exists regardless of this call's outcome.
    """
    import_fn = getattr(handles.timeline, "ImportIntoTimeline", None)
    if import_fn is None:
        logger.info("This Resolve version's Timeline object has no ImportIntoTimeline() -- import the SRT manually.")
        return False
    try:
        ok = import_fn(str(srt_path), {"autoImportSourceClipsIntoMediaPool": False})
    except Exception:
        logger.exception("Timeline.ImportIntoTimeline() raised while importing subtitles")
        return False
    return bool(ok)


def write_quotes(analysis: dict, transcript: dict, out_path: Path) -> Path:
    """Plain-text pull-quote sheet: every best_line across all segments with
    timecodes, sorted by strength -- feeds social cuts and client-facing
    quote approvals."""
    fps = transcript["fps"]
    start_frame = tc.timecode_to_frames(transcript["start_timecode"], fps)
    segments_by_id = {s["id"]: s for s in analysis["segments"]}

    rows = []
    for sel in analysis.get("selects", []):
        if not sel.get("best_line") or sel.get("best_line_start") is None:
            continue
        seg = segments_by_id.get(sel["segment_id"], {})
        frame = start_frame + tc.seconds_to_frames(sel["best_line_start"], fps)
        rows.append({
            "timecode": tc.frames_to_timecode(frame, fps),
            "strength": sel.get("strength") or 0.0,
            "quote": sel["best_line"],
            "label": seg.get("question_label", sel["segment_id"]),
        })
    rows.sort(key=lambda r: r["strength"], reverse=True)

    lines = [f"Pull Quotes -- {transcript.get('source_file', 'unknown')}", "=" * 40, ""]
    for r in rows:
        lines.append(f"[{r['timecode']}] (strength {r['strength']:.2f}) {r['label']}")
        lines.append(f'  "{r["quote"]}"')
        lines.append("")

    out_path.write_text("\n".join(lines))
    logger.info("Wrote %s (%d quotes)", out_path, len(rows))
    return out_path
