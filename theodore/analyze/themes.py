"""Pass 3C -- controlled-vocabulary theme tagging.

Two Claude calls: first generate a small controlled theme vocabulary over
the whole interview's segments, then tag each segment with 1-3 theme IDs
drawn from that fixed vocabulary. Free-form per-segment tagging is
deliberately avoided -- it produces dozens of near-duplicate themes that are
useless for the cross-interview clustering planned for a later phase.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.themes")

VOCAB_PROMPT_PATH = Path(__file__).parent / "prompts" / "themes_vocabulary.md"
TAGGING_PROMPT_PATH = Path(__file__).parent / "prompts" / "themes_tagging.md"

MAX_TOKENS = 4000


def _segment_summaries(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        q = seg.get("question_text") or "(volunteered)"
        lines.append(f"[{seg['id']}] Q: {q} | A: {seg.get('answer_summary', '')}")
    return "\n".join(lines)


def _generate_vocabulary(segments, model, client, cost_tracker) -> list[dict]:
    system = VOCAB_PROMPT_PATH.read_text()
    user = _segment_summaries(segments)
    result = call_json(
        client, pass_name="themes-vocab", model=model, system=system, user=user,
        max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
    )
    return result.get("themes", [])


def _tag_segments(segments, themes, model, client, cost_tracker) -> dict:
    system = TAGGING_PROMPT_PATH.read_text()
    vocab_text = "\n".join(f"- {t['id']}: {t['label']} -- {t['description']}" for t in themes)

    assignments: dict[str, list[str]] = {}
    chunks = chunk_items(segments)
    for i, chunk in enumerate(chunks, start=1):
        logger.info("Themes: tagging chunk %d/%d (%d segments)", i, len(chunks), len(chunk))
        user = f"Theme vocabulary:\n{vocab_text}\n\nSegments:\n{_segment_summaries(chunk)}"
        result = call_json(
            client, pass_name="themes-tag", model=model, system=system, user=user,
            max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
        )
        for a in result.get("assignments", []):
            assignments[a["segment_id"]] = a.get("theme_ids", [])
    return assignments


def run_themes(
    segments: list[dict],
    *,
    model_tier: str,
    cost_tracker: CostTracker,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    if not segments:
        return {"themes": [], "assignments": {}}

    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("themes", model_tier)

    themes = _generate_vocabulary(segments, model, client, cost_tracker)
    assignments = _tag_segments(segments, themes, model, client, cost_tracker)
    return {"themes": themes, "assignments": assignments}
