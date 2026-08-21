"""Pass 3A -- Claude identifies Q/A segment boundaries in the transcript."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.segmenter")

PROMPT_PATH = Path(__file__).parent / "prompts" / "segmenter.md"

MAX_TOKENS = 8000


def _format_transcript_chunk(utterances: list[dict], speakers: dict, interviewer: Optional[str]) -> str:
    lines = []
    for u in utterances:
        name = speakers.get(u["speaker"], f"Speaker {u['speaker']}")
        role = " (interviewer)" if interviewer and u["speaker"] == interviewer else ""
        lines.append(f"[{u['id']}] {name}{role} ({u['start']:.2f}-{u['end']:.2f}): {u['text']}")
    return "\n".join(lines)


def _dedupe_segments(segments: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for seg in segments:
        key = (
            seg.get("question_start_utterance"),
            seg.get("answer_start_utterance"),
            seg.get("answer_end_utterance"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(seg)
    return out


def run_segmenter(
    transcript: dict,
    *,
    interviewer: Optional[str],
    model_tier: str,
    cost_tracker: CostTracker,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("segmenter", model_tier)
    system = PROMPT_PATH.read_text()

    chunks = chunk_items(transcript["utterances"])
    all_segments = []
    for i, chunk in enumerate(chunks, start=1):
        logger.info("Segmenter: chunk %d/%d (%d utterances)", i, len(chunks), len(chunk))
        user = _format_transcript_chunk(chunk, transcript["speakers"], interviewer)
        result = call_json(
            client, pass_name="segmenter", model=model, system=system, user=user,
            max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
        )
        all_segments.extend(result.get("segments", []))

    segments = _dedupe_segments(all_segments)
    for i, seg in enumerate(segments, start=1):
        seg["id"] = f"s{i:03d}"
    return {"segments": segments}
