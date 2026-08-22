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

from theodore.assembly.plan import AssemblyClip
from theodore.assembly.trim import kept_words
from theodore.resolve import timecode as tc

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
