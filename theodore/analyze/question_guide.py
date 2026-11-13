"""Canonical question-guide matching (v2.0 Part 1).

When a project defines a shared question_guide in project.json, this maps
each subject's actual segments to a guide id -- so "q03" addresses the same
question across every subject, regardless of interview order. This is
ADDITIVE metadata (segment["canonical_question_id"]), layered on top of the
immutable "<subject_id>.q<NN>" sequential id from analyze/id_matching.py,
which stays each segment's true identity and is never replaced by this.

Fixed to Haiku -- a similarity/matching task, not editorial judgment.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.question_guide")

PROMPT_PATH = Path(__file__).parent / "prompts" / "question_guide.md"
MAX_TOKENS = 2000


def _format_guide(question_guide: list[dict]) -> str:
    lines = []
    for item in question_guide:
        aliases = item.get("aliases") or []
        alias_text = f" (aka: {'; '.join(aliases)})" if aliases else ""
        lines.append(f"[{item['id']}] {item['canonical']}{alias_text}")
    return "\n".join(lines)


def _format_segments(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        q = seg.get("question_text") or "(volunteered)"
        lines.append(f"[{seg['id']}] Q: {q} | A: {seg.get('answer_summary', '')}")
    return "\n".join(lines)


def match_to_guide(
    segments: list[dict],
    question_guide: list[dict],
    *,
    cost_tracker: Optional[CostTracker] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> dict[str, str]:
    """Returns {segment_id: guide_id} for segments matched well enough to a
    guide question. Unmatched segments are simply absent from the result --
    callers keep the segment's existing sequential id as-is either way."""
    if not segments or not question_guide:
        return {}

    cost_tracker = cost_tracker or CostTracker()
    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    system = PROMPT_PATH.read_text()
    user = f"Question guide:\n{_format_guide(question_guide)}\n\nSegments:\n{_format_segments(segments)}"

    result = call_json(
        client, pass_name="question_guide", model=config.QUESTION_GUIDE_MODEL, system=system, user=user,
        max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
    )

    valid_segment_ids = {s["id"] for s in segments}
    valid_guide_ids = {g["id"] for g in question_guide}
    matches: dict[str, str] = {}
    used_guide_ids = set()
    for m in result.get("matches", []):
        sid, gid = m.get("segment_id"), m.get("guide_id")
        if sid in valid_segment_ids and gid in valid_guide_ids and sid not in matches and gid not in used_guide_ids:
            matches[sid] = gid
            used_guide_ids.add(gid)
    return matches


def apply_canonical_ids(segments: list[dict], matches: dict[str, str]) -> list[dict]:
    """Sets segment["canonical_question_id"] from `matches`, explicitly
    clearing it to None for segments with no match rather than leaving a
    stale value behind from a prior run's guide (e.g. after the guide
    itself was edited)."""
    for seg in segments:
        seg["canonical_question_id"] = matches.get(seg["id"])
    return segments
