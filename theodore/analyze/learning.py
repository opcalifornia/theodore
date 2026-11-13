"""v2.0 Part 4 step 11 -- the learning loop.

Every edit list version already records exactly which segments an editor
actually kept and dropped (edits.py). This module compares the LATEST
version's real state against what Selects predicted (strength),
surfacing where the two diverge: segments Selects rated strong that
aren't in the current cut, and segments it rated weak that are.

Deliberately observational, not a hidden feedback mechanism: nothing here
rewrites a prompt or a score automatically. `theodore learn` just makes
the gap between prediction and practice visible -- the safer, reversible
half of a feedback loop to build first; an automatic recalibration is a
much bigger, harder-to-undo bet on top of this that isn't taken here.

Compares against the latest version rather than "ever appeared in any
version": a segment that was cut and later restored is, by definition,
kept as of now -- only the current decision is a live disagreement with
Selects worth surfacing. `versions_checked` still reports how much
history exists, as context.
"""
from __future__ import annotations

from theodore import edits

SURPRISE_LOW_THRESHOLD = 0.4
SURPRISE_HIGH_THRESHOLD = 0.7


def _subject_versions(project_dir, subject: str) -> list[edits.EditList]:
    """Every stored edit list version belonging to `subject` alone, oldest
    first. edits/ is one directory per PROJECT, not per subject, and a
    version may now legitimately span subjects (`theodore build` assembles
    those). Filtering by prefix is still exact rather than a heuristic --
    ids carry their subject -- and a mixed version is deliberately skipped
    rather than partially counted: "what did the editor keep of what I
    proposed for this subject?" is only answerable from a version that is
    unambiguously about that subject, since a cross-subject cut drops
    segments for reasons that have nothing to do with their own strength."""
    result = []
    for version in edits.list_versions(project_dir):
        edit_list = edits.load(project_dir, version)
        ids = edit_list.segment_ids
        if ids and all(sid.startswith(f"{subject}.") for sid in ids):
            result.append(edit_list)
    return result


def summarize(project_dir, subject: str, segments: list[dict], selects: list[dict]) -> dict:
    versions = _subject_versions(project_dir, subject)
    if not versions:
        return {
            "versions_checked": 0,
            "kept_count": 0,
            "dropped_count": 0,
            "avg_strength_kept": None,
            "avg_strength_dropped": None,
            "dropped_despite_high_strength": [],
            "kept_despite_low_strength": [],
        }

    selects_by_id = {s["segment_id"]: s for s in selects}
    kept = set(versions[-1].segment_ids)
    dropped = {s["id"] for s in segments} - kept

    def strength(sid: str) -> float:
        return selects_by_id.get(sid, {}).get("strength") or 0.0

    kept_scores = [strength(sid) for sid in kept if sid in selects_by_id]
    dropped_scores = [strength(sid) for sid in dropped if sid in selects_by_id]

    # Only ever flagged for segments Selects actually scored -- an unscored
    # segment has no real prediction to diverge from, so strength()'s 0.0
    # fallback (used above only to keep the averages simple) must not turn
    # "never scored" into a false "predicted weak" surprise here.
    dropped_despite_high_strength = sorted(
        (sid for sid in dropped if sid in selects_by_id and strength(sid) >= SURPRISE_HIGH_THRESHOLD),
        key=strength, reverse=True,
    )
    kept_despite_low_strength = sorted(
        (sid for sid in kept if sid in selects_by_id and strength(sid) < SURPRISE_LOW_THRESHOLD),
        key=strength,
    )

    return {
        "versions_checked": len(versions),
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "avg_strength_kept": (sum(kept_scores) / len(kept_scores)) if kept_scores else None,
        "avg_strength_dropped": (sum(dropped_scores) / len(dropped_scores)) if dropped_scores else None,
        "dropped_despite_high_strength": dropped_despite_high_strength,
        "kept_despite_low_strength": kept_despite_low_strength,
    }
