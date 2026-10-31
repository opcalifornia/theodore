"""Immutable segment ID assignment and cross-run matching (v2.0 Part 1).

Question numbers are assigned once and never renumbered: if re-running the
segmenter silently renumbered everything, every saved edit command and
every marker's customData reference would break. New segmenter output is
matched against a subject's existing segments.json by utterance-range
overlap first -- a free, deterministic path that covers the common case of
re-running analyze without the underlying transcript changing -- and only
escalates to a Claude pass (fixed to Haiku; this is a similarity task, not
editorial judgment) for segments that don't exact-match, to judge whether a
boundary-shifted segment is the same question re-detected or genuinely new.

Matched segments keep their existing id. Genuinely new segments get the
next number for that subject, drawn from a persistent, monotonically
increasing counter (registry.py's subjects.<id>.next_question_number) --
never the count of currently-present segments, so a deleted/retired id is
never recycled.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.claude_client import ClaudeCallError, CostTracker, call_json

logger = logging.getLogger("theodore.analyze.id_matching")

PROMPT_PATH = Path(__file__).parent / "prompts" / "id_matching.md"
MAX_TOKENS = 2000


def _utterance_range(seg: dict) -> tuple:
    return (seg.get("answer_start_utterance"), seg.get("answer_end_utterance"))


def _exact_overlap_matches(new_segments: list[dict], existing_segments: list[dict]) -> dict[int, str]:
    """Segments whose answer utterance range is byte-for-byte identical to
    an existing segment's -- the common case when re-running analyze
    without the transcript changing underneath it."""
    existing_by_range: dict[tuple, str] = {}
    for es in existing_segments:
        existing_by_range.setdefault(_utterance_range(es), es["id"])

    matches = {}
    used = set()
    for ni, ns in enumerate(new_segments):
        rng = _utterance_range(ns)
        eid = existing_by_range.get(rng)
        if eid is not None and eid not in used:
            matches[ni] = eid
            used.add(eid)
    return matches


def _format_segment(prefix: str, ident, seg: dict) -> str:
    q = seg.get("question_text") or "(volunteered)"
    return (
        f"[{prefix}{ident}] range={seg.get('answer_start_utterance')}-{seg.get('answer_end_utterance')} "
        f"| Q: {q} | A: {seg.get('answer_summary', '')}"
    )


def _fuzzy_match_via_claude(
    unmatched_new: list[tuple],
    unmatched_existing: list[dict],
    *,
    cost_tracker: CostTracker,
    client: Optional[anthropic.Anthropic],
) -> dict[int, str]:
    if not unmatched_new or not unmatched_existing:
        return {}

    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    system = PROMPT_PATH.read_text()
    new_text = "\n".join(_format_segment("new:", ni, seg) for ni, seg in unmatched_new)
    existing_text = "\n".join(_format_segment("existing:", seg["id"], seg) for seg in unmatched_existing)
    user = f"Existing segments:\n{existing_text}\n\nNew segments:\n{new_text}"

    result = call_json(
        client, pass_name="id_matching", model=config.ID_MATCHING_MODEL, system=system, user=user,
        max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
    )

    valid_new_indices = {ni for ni, _ in unmatched_new}
    valid_existing_ids = {seg["id"] for seg in unmatched_existing}
    matches: dict[int, str] = {}
    used_existing = set()
    for m in result.get("matches", []):
        ni, eid = m.get("new_index"), m.get("existing_id")
        if ni in valid_new_indices and eid in valid_existing_ids and ni not in matches and eid not in used_existing:
            matches[ni] = eid
            used_existing.add(eid)
    return matches


def assign_immutable_ids(
    new_segments: list[dict],
    existing_segments: list[dict],
    subject_id: str,
    next_number: int,
    *,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
    use_claude: bool = True,
) -> tuple[list[dict], int]:
    """Assigns/preserves immutable "<subject_id>.q<NN>" ids on
    `new_segments` in place. Returns (new_segments, updated_next_number) --
    the caller must persist updated_next_number back to the registry even
    when no new ids were minted this run, since it's the authoritative
    counter going forward.
    """
    cost_tracker = cost_tracker or CostTracker()
    matches = _exact_overlap_matches(new_segments, existing_segments)

    if use_claude:
        matched_existing_ids = set(matches.values())
        unmatched_new = [(ni, ns) for ni, ns in enumerate(new_segments) if ni not in matches]
        unmatched_existing = [es for es in existing_segments if es["id"] not in matched_existing_ids]
        try:
            matches.update(_fuzzy_match_via_claude(
                unmatched_new, unmatched_existing, cost_tracker=cost_tracker, client=client,
            ))
        except ClaudeCallError:
            logger.exception(
                "Fuzzy id-matching pass failed; unmatched segments will be treated as new "
                "rather than blocking the run"
            )

    for ni, seg in enumerate(new_segments):
        if ni in matches:
            seg["id"] = matches[ni]
        else:
            seg["id"] = f"{subject_id}.q{next_number:02d}"
            next_number += 1

    return new_segments, next_number
