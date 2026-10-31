"""Assembly ordering strategies: chronological, strength, thematic, and
narrative (a Claude pass proposing an actual story order).

Every strategy takes analysis.json's shape and returns a list of segment
ids in the proposed order, plus an optional rationale string (only
narrative mode produces one). assembly/builder.py consumes that order to
build the timeline; export/review.py consumes it to render the review page
in the same order, so what you see is what gets built.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.assembly.ordering")

NARRATIVE_PROMPT_PATH = Path(__file__).resolve().parent.parent / "analyze" / "prompts" / "narrative.md"
MAX_TOKENS = 4000

MODES = ("chronological", "strength", "thematic", "narrative")


def _uid_index(uid: Optional[str]) -> int:
    if not uid:
        return 0
    try:
        return int(uid[1:])
    except ValueError:
        return 0


def _strength_of(seg_id: str, selects_by_id: dict) -> float:
    sel = selects_by_id.get(seg_id)
    strength = sel.get("strength") if sel else None
    return strength if strength is not None else -1.0


def order_chronological(analysis: dict) -> list[str]:
    """Source order -- the safe default. Segments are already produced in
    roughly chronological order by the segmenter, but this sorts explicitly
    by the answer's utterance position so it's correct even after
    de-duplication or manual edits to segments.json."""
    segments = sorted(
        analysis["segments"],
        key=lambda s: _uid_index(s.get("answer_start_utterance") or s.get("question_start_utterance")),
    )
    return [s["id"] for s in segments]


def order_by_strength(analysis: dict) -> list[str]:
    """Strongest soundbites first -- for finding your open. Unscored
    segments (no Selects entry) sort last."""
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    segments = sorted(analysis["segments"], key=lambda s: _strength_of(s["id"], selects_by_id), reverse=True)
    return [s["id"] for s in segments]


def order_thematic(analysis: dict) -> list[str]:
    """Grouped by theme, themes ordered by aggregate (mean) strength of
    their member segments, descending. A segment's "primary" theme is the
    first entry in its theme_ids (the Themes pass assigns 1-3, ranked by
    relevance); segments with no theme assignment land in a trailing group,
    ordered by strength like everything else."""
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    assignments = analysis.get("theme_assignments", {})
    theme_ids_in_vocab = [t["id"] for t in analysis.get("themes", [])]

    groups: dict[str, list[str]] = {tid: [] for tid in theme_ids_in_vocab}
    other: list[str] = []
    for seg in analysis["segments"]:
        theme_ids = assignments.get(seg["id"], [])
        primary = theme_ids[0] if theme_ids else None
        (groups[primary] if primary in groups else other).append(seg["id"])

    def theme_strength(tid: str) -> float:
        members = groups[tid]
        if not members:
            return -1.0
        return sum(_strength_of(sid, selects_by_id) for sid in members) / len(members)

    ordered_theme_ids = sorted(theme_ids_in_vocab, key=theme_strength, reverse=True)

    ordered: list[str] = []
    for tid in ordered_theme_ids:
        ordered.extend(sorted(groups[tid], key=lambda sid: _strength_of(sid, selects_by_id), reverse=True))
    ordered.extend(sorted(other, key=lambda sid: _strength_of(sid, selects_by_id), reverse=True))
    return ordered


def _segment_summaries_for_prompt(analysis: dict) -> str:
    selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
    themes_by_id = {t["id"]: t for t in analysis.get("themes", [])}
    assignments = analysis.get("theme_assignments", {})

    lines = []
    for seg in analysis["segments"]:
        sel = selects_by_id.get(seg["id"], {})
        theme_labels = [themes_by_id[t]["label"] for t in assignments.get(seg["id"], []) if t in themes_by_id]
        strength = sel.get("strength")
        strength_str = f"{strength:.2f}" if strength is not None else "?"
        q = seg.get("question_text") or "(volunteered)"
        lines.append(
            f"[{seg['id']}] strength={strength_str} themes={','.join(theme_labels) or 'none'} | "
            f"Q: {q} | A: {seg.get('answer_summary', '')}"
        )
    return "\n".join(lines)


def run_narrative_ordering(
    analysis: dict,
    *,
    model_tier: str = config.DEFAULT_MODEL_TIER,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> tuple[list[str], str]:
    """A Claude pass proposing an actual story order -- setup, tension,
    resolution -- from the segment summaries. Returns (ordered_segment_ids,
    rationale). Always includes every segment id, even if Claude's response
    dropped one: missing ids are appended in chronological order rather than
    silently lost."""
    if not analysis["segments"]:
        return [], "No segments to order."

    cost_tracker = cost_tracker or CostTracker()
    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("narrative", model_tier)
    system = NARRATIVE_PROMPT_PATH.read_text()
    user = _segment_summaries_for_prompt(analysis)

    result = call_json(
        client, pass_name="narrative", model=model, system=system, user=user,
        max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
    )

    valid_ids = {s["id"] for s in analysis["segments"]}
    order = [sid for sid in result.get("order", []) if sid in valid_ids]
    seen = set(order)
    order.extend(sid for sid in order_chronological(analysis) if sid not in seen)
    if len(order) != len(valid_ids):
        logger.warning("Narrative ordering returned duplicate or unexpected ids; deduplicated to %d segments", len(order))

    return order, result.get("rationale", "")


def compute_order(
    analysis: dict,
    mode: str,
    *,
    model_tier: str = config.DEFAULT_MODEL_TIER,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> tuple[list[str], Optional[str]]:
    """Dispatch to the requested ordering strategy. Returns (ordered_segment_ids,
    rationale_or_None)."""
    if mode == "chronological":
        return order_chronological(analysis), None
    if mode == "strength":
        return order_by_strength(analysis), None
    if mode == "thematic":
        return order_thematic(analysis), None
    if mode == "narrative":
        return run_narrative_ordering(analysis, model_tier=model_tier, cost_tracker=cost_tracker, client=client)
    raise ValueError(f"Unknown assembly mode {mode!r}, expected one of {MODES}")
