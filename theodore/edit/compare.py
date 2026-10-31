"""How close is the edit to the reference? Measured on the timeline itself.

After a build, the timeline is read back the way the reference was studied:
how long shots run, how many visible cuts per minute, how much of the runtime
is B-roll, how long cutaways stay, when the first one lands, the longest
stretch held on the face. Each is set beside the reference's number with a
verdict, so drift is fixed before the editor sees it -- and so "match it more
closely" has a concrete meaning.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

TOL = 0.25   # within 25% of the reference counts as matching


def _union(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def measure(base: list[tuple[float, float]], over: list[tuple[float, float]], runtime: float) -> dict:
    """Seconds, relative to the timeline start. `base` = main-track items, `over` = items on tracks above."""
    cover = _union(over)
    covered = sum(b - a for a, b in cover)
    # picture shots as the viewer sees them: cutaways, and the face between them
    face_runs: list[float] = []
    for a, b in _union(base):
        cur = a
        for c0, c1 in cover:
            if c1 <= cur or c0 >= b:
                continue
            if c0 > cur:
                face_runs.append(c0 - cur)
            cur = max(cur, c1)
        if cur < b:
            face_runs.append(b - cur)
    # visible cuts: every main-track item edge not under a cutaway, plus every cutaway edge
    edges = [a for a, _ in sorted(base)[1:]]
    visible = [e for e in edges if not any(c0 < e < c1 for c0, c1 in cover)]
    visible += [x for c in cover for x in c if 0 < x < runtime]
    dur = [b - a for a, b in cover]
    return {
        "runtime": round(runtime, 1),
        "average_shot_length": round(runtime / (len(visible) + 1), 2) if runtime else None,
        "cuts_per_minute": round(len(visible) / (runtime / 60), 1) if runtime else None,
        "broll_pct": round(100 * covered / runtime, 1) if runtime else 0.0,
        "cutaways": len(cover),
        "cutaway_seconds": {"median": round(median(dur), 2), "min": round(min(dur), 2), "max": round(max(dur), 2)} if dur else {},
        "first_cutaway_at": round(cover[0][0], 1) if cover else None,
        "longest_uninterrupted_face_seconds": round(max(face_runs), 1) if face_runs else 0.0,
    }


def _row(label: str, ours, theirs, advice_hi: str, advice_lo: str) -> Optional[dict]:
    if ours is None or theirs is None:
        return None
    if theirs == 0:
        ok = abs(ours) < 1e-9
    else:
        ok = abs(ours - theirs) / abs(theirs) <= TOL
    out = {"measure": label, "edit": ours, "reference": theirs, "matches": bool(ok)}
    if not ok:
        out["fix"] = advice_hi if ours > theirs else advice_lo
    return out


def compare(ours: dict, reference_analysis: dict) -> dict:
    pace = reference_analysis.get("pacing") or {}
    lay = (reference_analysis.get("layers") or {}).get("summary") or {}
    tp = lay.get("time_pct") or {}
    ref_broll = (tp.get("broll_over_speech", 0) + tp.get("broll_clean", 0)) if tp else None
    cs = lay.get("cutaway_seconds") or {}
    rows = [
        _row("average shot length (s)", ours.get("average_shot_length"), pace.get("average_shot_length"),
             "cut faster: shorten selects or add cutaways", "cut slower: hold shots longer, fewer cutaways"),
        _row("cuts per minute", ours.get("cuts_per_minute"), pace.get("cuts_per_minute"),
             "too many cuts: merge cutaways or lengthen shots", "too few cuts: add cutaways or tighten selects"),
        _row("B-roll share of runtime (%)", ours.get("broll_pct"), ref_broll,
             "too much B-roll: leave more on the face", "too little B-roll: place more cutaways"),
        _row("median cutaway (s)", (ours.get("cutaway_seconds") or {}).get("median"), cs.get("median"),
             "cutaways run long: trim them", "cutaways are short: let them breathe"),
        _row("first cutaway at (s)", ours.get("first_cutaway_at"), lay.get("first_cutaway_at"),
             "first cutaway is late: bring it in earlier", "first cutaway is early: hold the face longer first"),
        _row("longest stretch on the face (s)", ours.get("longest_uninterrupted_face_seconds"),
             lay.get("longest_uninterrupted_face_seconds"),
             "face held too long: break it with a cutaway", "never rests on the face as long as the reference does"),
    ]
    rows = [r for r in rows if r]
    off = [r for r in rows if not r["matches"]]
    return {"rows": rows, "matching": len(rows) - len(off), "of": len(rows),
            "verdict": "close match" if rows and not off else f"{len(off)} measure(s) off" if rows else "nothing to compare",
            "fixes": [f"{r['measure']}: {r['fix']} ({r['edit']} vs {r['reference']})" for r in off]}


def timeline_spans(h) -> tuple[list, list, float]:
    tl = h.timeline
    fps = float(tl.GetSetting("timelineFrameRate") or 24)
    start = int(tl.GetStartFrame())

    def spans(idx):
        return [((it.GetStart() - start) / fps, (it.GetEnd() - start) / fps)
                for it in (tl.GetItemListInTrack("video", idx) or [])]

    base = spans(1)
    over = []
    for idx in range(2, int(tl.GetTrackCount("video") or 0) + 1):
        over += spans(idx)
    runtime = max([b for _, b in base + over] or [0.0])
    return base, over, runtime


def check(h, reference: str) -> dict:
    from theodore.study import reference as ref_mod
    ref = ref_mod.load_reference(reference)
    base, over, runtime = timeline_spans(h)
    if not base:
        raise RuntimeError("Nothing on the main video track to measure.")
    ours = measure(base, over, runtime)
    return {"reference": reference, "edit": ours, **compare(ours, ref["analysis"])}
