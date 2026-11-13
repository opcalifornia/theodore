"""v2.0 Part 4 step 8 -- semantic search across segments.

`theodore find <query>` lets an editor ask for footage in plain language
("someone talking about losing their mother") instead of scrolling
transcripts. It searches one subject by default, or every registered
subject in the project when no subject is given -- segment ids already
carry their subject prefix (`haylee.q03`), so a cross-subject result set
stays unambiguous without extra bookkeeping.

This is a single Claude judgment pass, not embeddings plus a vector
index: per-project segment counts are small (tens to low hundreds, not
thousands), so sending every segment's question/answer summary straight
to Claude and asking for a relevance-ranked shortlist is simpler than a
retrieval pipeline and, at this scale, no less accurate.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.search")

PROMPT_PATH = Path(__file__).parent / "prompts" / "search.md"

MAX_TOKENS = 3000


def _segment_summaries(segments: list[dict], selects_by_id: dict) -> str:
    lines = []
    for seg in segments:
        q = seg.get("question_text") or "(volunteered)"
        line = f"[{seg['id']}] Q: {q} | A: {seg.get('answer_summary', '')}"
        best_line = selects_by_id.get(seg["id"], {}).get("best_line")
        if best_line:
            line += f' | Quote: "{best_line}"'
        lines.append(line)
    return "\n".join(lines)


def run_search(
    segments: list[dict],
    query: str,
    *,
    model_tier: str,
    cost_tracker: CostTracker,
    selects: Optional[list[dict]] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    if not segments or not query.strip():
        return {"matches": []}

    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("search", model_tier)
    system = PROMPT_PATH.read_text()
    selects_by_id = {s["segment_id"]: s for s in (selects or [])}

    all_matches = []
    chunks = chunk_items(segments)
    for i, chunk in enumerate(chunks, start=1):
        logger.info("Search: chunk %d/%d (%d segments)", i, len(chunks), len(chunk))
        user = f'Request: "{query}"\n\nSegments:\n{_segment_summaries(chunk, selects_by_id)}'
        result = call_json(
            client, pass_name="search", model=model, system=system, user=user,
            max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
        )
        all_matches.extend(result.get("matches", []))

    # A segment repeated across overlapping chunks keeps its highest
    # reported relevance rather than being counted twice.
    best_by_id: dict[str, dict] = {}
    for m in all_matches:
        sid = m.get("segment_id")
        if not sid:
            continue
        if sid not in best_by_id or (m.get("relevance") or 0) > (best_by_id[sid].get("relevance") or 0):
            best_by_id[sid] = m

    matches = sorted(best_by_id.values(), key=lambda m: -(m.get("relevance") or 0))
    return {"matches": matches}
