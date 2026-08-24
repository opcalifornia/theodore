"""v2.0 Part 4 step 7 -- redundancy detection.

Real interviews are full of near-duplicate answers: the subject circles
back to a good story unprompted, or gets asked a similar question two
different ways and gives essentially the same answer both times. Finding
these by re-watching every take is slow; this pass flags them
automatically so an editor only has to choose once, not for both.

Candidate pooling is deliberately cheap: rather than an O(n^2) all-pairs
comparison, segments are pooled by shared theme id (`theodore analyze`
already tags every segment with 1-3 themes) and only segments that share a
theme are ever sent to Claude together for a redundancy judgment. A
segment can land in more than one pool if it carries more than one theme,
so groups Claude reports across separate calls that share a segment id are
merged afterward.

"Which take is the strongest" is deliberately NOT a second Claude
judgment call -- Selects already produces a holistic strength score
(content + delivery) for every segment, so the recommended take within a
redundant group is just the highest-strength member of that group,
computed in Python for free.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import anthropic

from theodore import config
from theodore.analyze.chunking import chunk_items
from theodore.analyze.claude_client import CostTracker, call_json

logger = logging.getLogger("theodore.analyze.redundancy")

PROMPT_PATH = Path(__file__).parent / "prompts" / "redundancy.md"

MAX_TOKENS = 2000


def _segment_summaries(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        q = seg.get("question_text") or "(volunteered)"
        lines.append(f"[{seg['id']}] Q: {q} | A: {seg.get('answer_summary', '')}")
    return "\n".join(lines)


def _candidate_pools(segments: list[dict], theme_assignments: dict) -> list[list[dict]]:
    by_theme: dict[str, list[dict]] = {}
    for seg in segments:
        for theme_id in theme_assignments.get(seg["id"], []):
            by_theme.setdefault(theme_id, []).append(seg)
    pools = [group for group in by_theme.values() if len(group) >= 2]
    if pools:
        return pools
    # No theme data (or every theme turned out to be a singleton) -- fall
    # back to a full, chunked pass over every segment rather than silently
    # finding nothing.
    return [chunk for chunk in chunk_items(segments) if len(chunk) >= 2]


def _find(parent: dict, x: str) -> str:
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: dict, a: str, b: str) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


def _merge_groups(raw_groups: list[dict]) -> list[dict]:
    """A segment can appear in more than one candidate pool (it may carry
    more than one theme), so Claude can report the same redundancy from two
    separate calls, or report overlapping groups that should really be one.
    Union-find merges anything that shares a segment id into a single
    group."""
    parent: dict[str, str] = {}
    clean = [g for g in raw_groups if len(g.get("segment_ids") or []) >= 2]
    for g in clean:
        ids = g["segment_ids"]
        for sid in ids[1:]:
            _union(parent, ids[0], sid)

    buckets: dict[str, set] = {}
    reasons_by_root: dict[str, list[str]] = {}
    for g in clean:
        root = _find(parent, g["segment_ids"][0])
        buckets.setdefault(root, set()).update(g["segment_ids"])
        reason = (g.get("reason") or "").strip()
        if reason:
            reasons_by_root.setdefault(root, []).append(reason)

    merged = []
    for root, ids in buckets.items():
        reasons = list(dict.fromkeys(reasons_by_root.get(root, [])))
        merged.append({"segment_ids": sorted(ids), "reason": " / ".join(reasons)})
    return merged


def _recommend(segment_ids: list[str], selects_by_id: dict) -> Optional[str]:
    if not segment_ids:
        return None

    def score(sid: str) -> tuple:
        s = selects_by_id.get(sid, {})
        return (s.get("strength") or 0.0, s.get("delivery_strength") or 0.0)

    return max(sorted(segment_ids), key=score)


def run_redundancy(
    segments: list[dict],
    theme_assignments: dict,
    selects: list[dict],
    *,
    model_tier: str,
    cost_tracker: CostTracker,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    if len(segments) < 2:
        return {"groups": []}

    client = client or anthropic.Anthropic(api_key=config.require_anthropic_key())
    model = config.model_for_pass("redundancy", model_tier)
    system = PROMPT_PATH.read_text()
    selects_by_id = {s["segment_id"]: s for s in selects}

    pools = _candidate_pools(segments, theme_assignments or {})
    raw_groups = []
    for i, pool in enumerate(pools, start=1):
        logger.info("Redundancy: pool %d/%d (%d segments)", i, len(pools), len(pool))
        user = _segment_summaries(pool)
        result = call_json(
            client, pass_name="redundancy", model=model, system=system, user=user,
            max_tokens=MAX_TOKENS, cost_tracker=cost_tracker,
        )
        raw_groups.extend(result.get("groups", []))

    merged = _merge_groups(raw_groups)
    merged.sort(key=lambda g: g["segment_ids"][0])

    groups = []
    for i, g in enumerate(merged, start=1):
        groups.append({
            "id": f"r{i:02d}",
            "segment_ids": g["segment_ids"],
            "recommended_id": _recommend(g["segment_ids"], selects_by_id),
            "reason": g["reason"],
        })
    return {"groups": groups}


def format_redundancy_groups(
    result: dict, segments: list[dict], selects: list[dict], delivery: Optional[dict] = None,
) -> str:
    """Human-readable dupes listing: each group's candidates with content
    strength and -- when `theodore delivery` has been run -- the actual
    measured delivery facts behind the recommendation. `_recommend()`
    already picks the strongest take for free out of Selects' existing
    (optionally delivery-informed) strength score; this is purely the
    presentation layer that makes WHY visible instead of a silent pick,
    since "trust the ranking" isn't as useful as seeing the same measured
    facts (energy, pace, pauses, hesitation) a human would notice on a
    rewatch -- without having to rewatch every take to find them.
    """
    if not result["groups"]:
        return "No redundant segments found."

    segments_by_id = {s["id"]: s for s in segments}
    selects_by_id = {s["segment_id"]: s for s in selects}
    delivery_segments = (delivery or {}).get("segments", {})

    lines = []
    for group in result["groups"]:
        lines.append(f"\n[{group['id']}] {len(group['segment_ids'])} segments cover the same ground:")
        for sid in group["segment_ids"]:
            mark = "-> keep" if sid == group["recommended_id"] else "  drop?"
            label = segments_by_id.get(sid, {}).get("question_text") or "(volunteered)"
            sel = selects_by_id.get(sid, {})
            strength_bits = []
            if sel.get("strength") is not None:
                strength_bits.append(f"strength {sel['strength']:.2f}")
            if sel.get("delivery_strength") is not None:
                strength_bits.append(f"delivery {sel['delivery_strength']:.2f}")
            suffix = f"  ({', '.join(strength_bits)})" if strength_bits else ""
            lines.append(f"  {mark}  {sid}  {label}{suffix}")

            d = delivery_segments.get(sid)
            if d and d.get("descriptor"):
                for fact in d["descriptor"].splitlines()[1:]:
                    fact = fact.strip()
                    if fact:
                        lines.append(f"         {fact}")
        lines.append(f"  reason: {group['reason']}")
    return "\n".join(lines)


def save_redundancy(result: dict, subj_dir: Path) -> Path:
    out = subj_dir / "redundancy.json"
    out.write_text(json.dumps(result, indent=2))
    return out


def load_redundancy(subj_dir: Path) -> Optional[dict]:
    path = subj_dir / "redundancy.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
