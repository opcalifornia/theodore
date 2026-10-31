"""v2.0 Part 4 step 9 -- rejects view + coverage/gap analysis.

Both features are pure Python over data `theodore analyze` already
produced -- no new Claude calls, no new dependencies. This is the natural
complement to Selects and the question guide: Selects already tells you
what's GOOD, this tells you what's WEAK (rejects) and what's MISSING
(gaps), which matters just as much when deciding whether an interview is
actually finished.
"""
from __future__ import annotations

REJECT_STRENGTH_THRESHOLD = 0.4


def find_rejects(
    segments: list[dict],
    selects: list[dict],
    *,
    threshold: float = REJECT_STRENGTH_THRESHOLD,
) -> list[dict]:
    """Segments Selects scored weak enough that an editor probably
    wouldn't use them as-is, sorted weakest first so the most clear-cut
    cuts surface before the borderline ones."""
    segments_by_id = {s["id"]: s for s in segments}
    rejects = []
    for sel in selects:
        strength = sel.get("strength")
        if strength is None or strength >= threshold:
            continue
        seg = segments_by_id.get(sel["segment_id"], {})
        rejects.append({
            "segment_id": sel["segment_id"],
            "strength": strength,
            "question_text": seg.get("question_text"),
            "issues": sel.get("issues") or [],
            "rationale": sel.get("rationale"),
        })
    rejects.sort(key=lambda r: r["strength"])
    return rejects


def find_gaps(segments: list[dict], question_guide: list[dict]) -> list[dict]:
    """Guide questions with no segment matched to them for this subject --
    i.e. this subject was never asked, or never actually answered, that
    canonical question. Requires `--addressing canonical` to have run
    (segments carry `canonical_question_id`); with no guide there is
    nothing to compare against, so this returns an empty list rather than
    treating every question as missing."""
    if not question_guide:
        return []
    answered = {s["canonical_question_id"] for s in segments if s.get("canonical_question_id")}
    return [g for g in question_guide if g["id"] not in answered]
