"""Pass 3B -- Claude scores each segment's answer as usable footage."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.selects")

PROMPT_PATH = Path(__file__).parent / "prompts" / "selects.md"

MAX_TOKENS = 6000


def _utterance_index(uid: str) -> int:
    return int(uid[1:])


def _format_segment(seg: dict, utterances: list[dict], delivery: Optional[dict] = None) -> str:
    lo = _utterance_index(seg["answer_start_utterance"])
    hi = _utterance_index(seg["answer_end_utterance"])
    span = [u for u in utterances if lo <= _utterance_index(u["id"]) <= hi]

    lines = [f"Segment {seg['id']} -- Q: {seg.get('question_text') or '(volunteered)'}"]
    for u in span:
        lines.append(f"  [{u['id']}] ({u['start']:.2f}-{u['end']:.2f}): {u['text']}")

    # Delivery (v2.0 Part 3): measured, speaker-relative acoustic facts --
    # never raw audio, never an unqualified adjective -- so the strength
    # judgment below can weigh HOW something was said alongside WHAT was
    # said, without the well-documented failure mode where a model's
    # "prosody" read is actually just tracking the words.
    profile = (delivery or {}).get("segments", {}).get(seg["id"])
    if profile and profile.get("descriptor"):
        lines.append("")
        lines.append(profile["descriptor"])

    return "\n".join(lines)


def run_selects(
    segments: list[dict],
    transcript: dict,
    *,
    model_tier: str,
    cost_tracker: CostTracker,
    delivery: Optional[dict] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    if not segments:
        return {"selects": []}

    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("selects", model_tier)
    system = PROMPT_PATH.read_text()
    utterances = transcript["utterances"]

    all_selects = []
    chunks = chunk_items(segments)
    for i, chunk in enumerate(chunks, start=1):
        logger.info("Selects: chunk %d/%d (%d segments)", i, len(chunks), len(chunk))
        user = "\n\n".join(_format_segment(seg, utterances, delivery) for seg in chunk)
        result = call_json(
            client, pass_name="selects", model=model, system=system, user=user,
            max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
        )
        all_selects.extend(result.get("selects", []))

    seen = set()
    deduped = []
    for s in all_selects:
        if s["segment_id"] in seen:
            continue
        seen.add(s["segment_id"])
        deduped.append(s)
    return {"selects": deduped}
