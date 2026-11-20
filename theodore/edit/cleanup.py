"""Take the ums, stumbles and dead air out of a talking timeline.

Works from Resolve's own word-level transcript of the open timeline, decides
the cuts here (deterministically, so the same timeline always gets the same
edit), and hands them to the tested ripple-trim engine, which builds a
duplicate with every track cut together so picture and sound stay in sync.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "mm", "mhm", "mmm"}


@dataclass
class Cut:
    start: int   # absolute timeline frame, inclusive
    end: int     # exclusive
    reason: str
    text: str = ""


def _norm(w: dict) -> str:
    return re.sub(r"[^a-z']", "", str(w.get("word") or w.get("punctuated_word") or "").lower())


def plan_cuts(words: list[dict], fps: float, *, fillers: bool = True, repeats: bool = True, max_pause: float = 0.6, keep_pause: float = 0.25,
              pad: int = 1) -> list[Cut]:
    """words: [{"word", "punctuated_word", "frame" (abs start), "end" (abs end frame)}] in order."""
    cuts: list[Cut] = []
    n = len(words)
    max_gap, keep = int(round(max_pause * fps)), int(round(keep_pause * fps))
    skip = set()

    def nxt(i):
        return words[i + 1]["frame"] if i + 1 < n else words[i]["end"]

    def prv(i):
        return words[i - 1]["end"] if i > 0 else words[i]["frame"]

    for i, w in enumerate(words):
        token = _norm(w)
        if fillers and token in FILLERS:
            a = max(w["frame"], prv(i) + pad)
            b = max(a, nxt(i) - pad)
            if b > a:
                cuts.append(Cut(a, b, "filler", w.get("punctuated_word", token)))
                skip.add(i)
            continue
        if repeats and i + 1 < n and token and token == _norm(words[i + 1]) and token not in {"that", "had", "very"}:
            # "I, I think" / "the the": drop the first, keep the second.
            a, b = max(w["frame"], prv(i) + pad), max(w["frame"], words[i + 1]["frame"] - pad)
            if b > a:
                cuts.append(Cut(a, b, "repeat", w.get("punctuated_word", token)))
                skip.add(i)
    # Long pauses between kept words: shorten them, don't remove them.
    kept = [w for i, w in enumerate(words) if i not in skip]
    for prev, cur in zip(kept, kept[1:]):
        gap = cur["frame"] - prev["end"]
        if gap > max_gap:
            a, b = prev["end"] + keep // 2, cur["frame"] - (keep - keep // 2)
            if b > a:
                cuts.append(Cut(a, b, "pause", f"{gap / fps:.1f}s"))
    cuts.sort(key=lambda c: c.start)
    merged: list[Cut] = []
    for c in cuts:
        if merged and c.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, c.end)
            merged[-1].reason += f"+{c.reason}"
        else:
            merged.append(c)
    return merged


def clean_up(h, *, fillers: bool = True, repeats: bool = True, max_pause: float = 0.6,
             keep_pause: float = 0.25, new_name: str | None = None, dry_run: bool = False) -> dict:
    from theodore.assembly import builder
    from theodore.resolve import transcribe

    tl = h.timeline
    start = int(tl.GetStartFrame())
    result = transcribe.transcribe_timeline(h, word_level=True)
    fps = result["fps"]
    words = [{**w, "frame": start + w["start_frame"], "end": start + w["end_frame"]} for w in result["words"]]
    cuts = plan_cuts(words, fps, fillers=fillers, repeats=repeats, max_pause=max_pause, keep_pause=keep_pause)
    summary = {
        "fillers": sum("filler" in c.reason for c in cuts),
        "repeats": sum("repeat" in c.reason for c in cuts),
        "pauses_tightened": sum("pause" in c.reason for c in cuts),
        "seconds_removed": round(sum(c.end - c.start for c in cuts) / fps, 2),
        "cuts": [{"at_seconds": round((c.start - start) / fps, 2), "frames": c.end - c.start,
                  "why": c.reason, "what": c.text} for c in cuts[:200]],
    }
    if dry_run or not cuts:
        summary["note"] = "Preview only -- nothing changed." if cuts else "Nothing to clean up."
        return summary
    built = builder.trim_timeline_ranges(h, [(c.start, c.end) for c in cuts],
                                         new_name=new_name or f"{tl.GetName()} - clean")
    summary.update({"timeline": built.timeline_name, "from": built.original_timeline_name,
                    "clips_before": built.original_clip_count, "clips_after": built.new_clip_count,
                    "warnings": list(getattr(built, "warnings", []) or [])})
    return summary
