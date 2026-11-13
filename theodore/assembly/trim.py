"""Automatic trim proposals: leading/trailing filler, dead air, and
backchannel-overlap flagging, computed word-by-word from the Deepgram
word-level timestamps already sitting in transcript.json.

Pure data transformation -- no Resolve dependency, nothing here is
destructive. Every trim is logged to data/<project>/trims.json with both the
original and trimmed in/out for each segment, so `theodore untrim` (in
assembly/builder.py) can always rebuild the assembly at full length. Nothing
computed here is ever applied directly to a timeline; assembly/builder.py
decides what to actually cut.

What this module does NOT do: false-start trimming. The Selects pass
(analyze/selects.py) already identifies false starts and produces
clean_start_utterance/clean_end_utterance specifically to skip them -- that
utterance-level range is this module's starting point. This module only
refines it at the word level (filler + dead air) within that range.
"""
from __future__ import annotations

import json
import logging
import string
from pathlib import Path
from typing import Optional

from theodore import config

logger = logging.getLogger("theodore.assembly.trim")


def _normalize(word: str) -> str:
    return word.strip(string.whitespace + string.punctuation).lower()


def _uid_index(uid: str) -> int:
    return int(uid[1:])


def _flatten_words(utterances: list[dict]) -> list[dict]:
    """Word dicts annotated with which utterance id they came from, in
    chronological order."""
    words = []
    for u in utterances:
        for w in u.get("words", []):
            words.append({**w, "utterance_id": u["id"], "speaker": u["speaker"]})
    words.sort(key=lambda w: w["start"])
    return words


def _strip_leading_filler(words: list[dict], filler_phrases: set) -> tuple[list[dict], list[str]]:
    removed = []
    i = 0
    while i < len(words):
        # Try the longest phrase match first (e.g. "you know" before "you").
        matched = False
        for span in (2, 1):
            if i + span > len(words):
                continue
            phrase = " ".join(_normalize(w["word"]) for w in words[i:i + span])
            if phrase in filler_phrases:
                removed.append(phrase)
                i += span
                matched = True
                break
        if not matched:
            break
    return words[i:], removed


def _strip_trailing_filler(words: list[dict], filler_phrases: set) -> tuple[list[dict], list[str]]:
    reversed_words, removed = _strip_leading_filler(list(reversed(words)), filler_phrases)
    return list(reversed(reversed_words)), list(reversed(removed))


def _interior_filler_cuts(words: list[dict], filler_phrases: set) -> list[list[float]]:
    cuts = []
    i = 0
    while i < len(words):
        matched_span = 0
        for span in (2, 1):
            if i + span > len(words):
                continue
            phrase = " ".join(_normalize(w["word"]) for w in words[i:i + span])
            if phrase in filler_phrases:
                matched_span = span
                break
        if matched_span:
            cuts.append([words[i]["start"], words[i + matched_span - 1]["end"]])
            i += matched_span
        else:
            i += 1
    return cuts


def _dead_air_cuts(words: list[dict], threshold: float) -> list[list[float]]:
    cuts = []
    for prev_word, next_word in zip(words, words[1:]):
        gap = next_word["start"] - prev_word["end"]
        if gap >= threshold:
            cuts.append([prev_word["end"], next_word["start"]])
    return cuts


def _merge_intervals(intervals: list[list[float]]) -> list[list[float]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _backchannel_overlaps(
    utterances_in_range: list[dict],
    answer_speaker: str,
    speaker_names: dict,
    window_start: float,
    window_end: float,
) -> list[dict]:
    overlaps = []
    for u in utterances_in_range:
        if u["speaker"] == answer_speaker:
            continue
        if u["end"] <= window_start or u["start"] >= window_end:
            continue
        text = _normalize(u["text"])
        if text in config.BACKCHANNEL_WORDS or any(_normalize(w) in config.BACKCHANNEL_WORDS for w in u["text"].split()):
            overlaps.append({
                "speaker": u["speaker"],
                "speaker_name": speaker_names.get(u["speaker"], u["speaker"]),
                "text": u["text"],
                "start": u["start"],
                "end": u["end"],
            })
    return overlaps


def get_segment_words(segment: dict, select: Optional[dict], transcript: dict) -> Optional[tuple]:
    """The (answer_speaker, flattened word list) for a segment's clean
    utterance range, in chronological order. Returns None if the range can't
    be resolved. Shared between compute_trim() and export/review.py, which
    needs the same word list to render struck-through trimmed text."""
    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}

    start_uid = (select or {}).get("clean_start_utterance") or segment["answer_start_utterance"]
    end_uid = (select or {}).get("clean_end_utterance") or segment["answer_end_utterance"]
    start_u = utterances_by_id.get(start_uid)
    end_u = utterances_by_id.get(end_uid)
    if start_u is None or end_u is None:
        logger.warning("Segment %s: clean utterance range not found in transcript", segment["id"])
        return None

    answer_speaker = start_u["speaker"]
    lo, hi = _uid_index(start_uid), _uid_index(end_uid)
    in_range = [u for u in transcript["utterances"] if lo <= _uid_index(u["id"]) <= hi]
    same_speaker = [u for u in in_range if u["speaker"] == answer_speaker]

    words = _flatten_words(same_speaker)
    if not words:
        logger.warning("Segment %s: no words in clean range", segment["id"])
        return None
    return answer_speaker, words


def is_word_kept(word: dict, trim: Optional[dict]) -> bool:
    """Whether a word survives trimming: not in the boundary-narrowed
    [trimmed_start, trimmed_end] range, and not inside any of trim's `cuts`
    (dead air, or interior filler if computed with aggressive=True). A
    single source of truth for "is this word still in the cut" -- used by
    export/review.py (to strike removed words) and export/captions.py (to
    build caption cues from only the surviving words)."""
    if not trim:
        return True
    if word["start"] < trim["trimmed_start"] or word["end"] > trim["trimmed_end"]:
        return False
    return not any(c[0] <= word["start"] < c[1] or c[0] < word["end"] <= c[1] for c in trim.get("cuts", []))


def kept_words(segment: dict, select: Optional[dict], transcript: dict, trim: Optional[dict]) -> list[dict]:
    """The words from get_segment_words() that survive trimming, in order."""
    resolved = get_segment_words(segment, select, transcript)
    if resolved is None:
        return []
    _, words = resolved
    return [w for w in words if is_word_kept(w, trim)]


def compute_trim(
    segment: dict,
    select: Optional[dict],
    transcript: dict,
    *,
    silence_threshold: float = config.DEFAULT_SILENCE_THRESHOLD_SECONDS,
    aggressive: bool = False,
) -> Optional[dict]:
    """Compute the trim proposal for one segment. Returns None if the
    segment's utterance range can't be resolved (e.g. stale analysis)."""
    resolved = get_segment_words(segment, select, transcript)
    if resolved is None:
        return None
    answer_speaker, words = resolved

    original_start, original_end = words[0]["start"], words[-1]["end"]

    trimmed_words, leading_removed = _strip_leading_filler(words, config.LEADING_TRAILING_FILLERS)
    trimmed_words, trailing_removed = _strip_trailing_filler(trimmed_words, config.LEADING_TRAILING_FILLERS)
    if not trimmed_words:
        # Entire segment was filler words -- keep the untrimmed original
        # rather than collapsing to nothing.
        logger.warning("Segment %s: filler-stripping removed all words, keeping original range", segment["id"])
        trimmed_words, leading_removed, trailing_removed = words, [], []

    trimmed_start, trimmed_end = trimmed_words[0]["start"], trimmed_words[-1]["end"]

    dead_air = _dead_air_cuts(trimmed_words, silence_threshold)
    interior_filler = _interior_filler_cuts(trimmed_words, config.LEADING_TRAILING_FILLERS) if aggressive else []
    cuts = _merge_intervals(dead_air + interior_filler)

    # Backchannel detection scans the *whole* transcript by time overlap,
    # not just utterances sharing this segment's utterance-id range: a
    # backchannel like "mm-hmm" nested inside a single long subject
    # utterance gets its own utterance id assigned later (by start time)
    # than the containing utterance, so id-range filtering would miss it.
    backchannel = _backchannel_overlaps(
        transcript["utterances"], answer_speaker, transcript["speakers"], trimmed_start, trimmed_end,
    )

    return {
        "segment_id": segment["id"],
        "speaker": answer_speaker,
        "original_start": original_start,
        "original_end": original_end,
        "trimmed_start": trimmed_start,
        "trimmed_end": trimmed_end,
        "leading_filler_removed": leading_removed,
        "trailing_filler_removed": trailing_removed,
        "dead_air_cuts": dead_air,
        "interior_filler_cuts": interior_filler,
        "cuts": cuts,
        "backchannel_overlaps": backchannel,
    }


def compute_trims(
    analysis: dict,
    transcript: dict,
    *,
    silence_threshold: float = config.DEFAULT_SILENCE_THRESHOLD_SECONDS,
    aggressive: bool = False,
) -> dict:
    """Trim proposals for every segment in `analysis`, keyed by segment id."""
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    trims = {}
    for seg in analysis["segments"]:
        result = compute_trim(
            seg, selects_by_id.get(seg["id"]), transcript,
            silence_threshold=silence_threshold, aggressive=aggressive,
        )
        if result is not None:
            trims[seg["id"]] = result
    return trims


def save_trims(trims: dict, project_dir: Path) -> Path:
    out = project_dir / "trims.json"
    out.write_text(json.dumps(trims, indent=2))
    logger.info("Wrote %s", out)
    return out


def load_trims(project_dir: Path) -> dict:
    path = project_dir / "trims.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())
