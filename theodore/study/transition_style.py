"""A reference's transition habits, and a plan that repeats them.

Measured per kind of cut, because editors don't dissolve everywhere: a jump cut
between two answers is treated differently from a cut into B-roll or between
two B-roll shots, and cards (titles, end cards) differently again.

  contexts        face_to_face | face_broll | broll_to_broll | card
  per context     how many cuts, what share carry a transition, which kinds,
                  typical length in frames
  head / tail     does it fade in from black / fade out to black

`plan` turns that into per-cut choices for a timeline: evenly spread to hit the
reference's rate (no randomness, so a re-run gives the same result), with the
reference's usual kind and length. Kinds Resolve's FCPXML import can't make
(wipes, stingers, flashes with graphics) map to the nearest one that it can and
are reported, not silently dropped.
"""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Optional

FACE = {"talking_head", "talent_silent"}
#: reference transition kind -> what we can build ("dissolve" | "dip_to_black" | "dip_to_white")
BUILDABLE = {"dissolve": "dissolve", "fade_in": "dissolve", "fade_out": "dissolve", "dip_to_black": "dip_to_black",
             "dip_to_white": "dip_to_white", "flash": "dip_to_white", "wipe": "dissolve",
             "stylized": "dissolve", "stinger": None}
CONTEXTS = ("face_to_face", "face_broll", "broll_to_broll", "card")


def context_of(a: str, b: str) -> str:
    if "card" in (a, b):
        return "card"
    fa, fb = a in FACE, b in FACE
    return "face_to_face" if fa and fb else "broll_to_broll" if not fa and not fb else "face_broll"


def _labels(analysis: dict) -> dict[int, str]:
    lay = analysis.get("layers") or {}
    if lay.get("shots"):
        return {r["index"]: r["label"] for r in lay["shots"]}
    out = {}
    for k, v in (analysis.get("shot_log") or {}).items():
        role = (v or {}).get("role")
        out[int(k)] = ("card" if role in ("graphic", "title_card", "end_card")
                       else "talking_head" if role in ("a_roll", "reaction") else "broll_clean")
    return out


def measure(analysis: dict) -> dict:
    shots = analysis.get("shots") or []
    if len(shots) < 2:
        return {}
    labels = _labels(analysis)
    starts = [s["start_time"] for s in shots]
    by_boundary: dict[int, dict] = {}
    head = tail = None
    for t in analysis.get("transitions") or []:
        if t["kind"] == "flash_in_shot":
            continue
        if t["kind"] == "fade_in" and t["time"] < 1.0:
            head = {"frames": t["frames"]}
            continue
        if t["kind"] == "fade_out" and t["time"] > analysis["media"]["duration"] - 3:
            tail = {"frames": t["frames"]}
            continue
        i = min(range(len(starts)), key=lambda k: abs(starts[k] - t["time"] - t.get("seconds", 0) / 2))
        if i > 0 and abs(starts[i] - t["time"]) < 1.0 + t.get("seconds", 0):
            by_boundary[i] = t
    ctx: dict[str, dict] = {}
    for i in range(1, len(shots)):
        c = context_of(labels.get(i - 1, "broll_clean"), labels.get(i, "broll_clean"))
        d = ctx.setdefault(c, {"cuts": 0, "kinds": Counter(), "frames": []})
        d["cuts"] += 1
        t = by_boundary.get(i)
        if t and t["kind"] != "cut":
            d["kinds"][t["kind"]] += 1
            d["frames"].append(t["frames"])
    out = {}
    for c, d in ctx.items():
        n_t = sum(d["kinds"].values())
        out[c] = {"cuts": d["cuts"], "with_transition_pct": round(100 * n_t / d["cuts"], 1),
                  "kinds": dict(d["kinds"].most_common()),
                  "typical_frames": int(median(d["frames"])) if d["frames"] else 0}
    return {"contexts": out, "head_fade": head, "tail_fade": tail, "fps": analysis["media"]["fps"]}


def choose_context(style: dict, preferred: Optional[str] = None) -> str:
    """Which of the reference's contexts a timeline's plain cuts should follow."""
    ctxs = style.get("contexts") or {}
    if preferred in ctxs:
        return preferred
    for c in ("face_to_face", "broll_to_broll", "face_broll"):
        if ctxs.get(c, {}).get("cuts", 0) >= 3:
            return c
    return max(ctxs, key=lambda c: ctxs[c]["cuts"]) if ctxs else ""


def plan(style: dict, n_cuts: int, context: Optional[str] = None, fps: float = 24.0) -> dict:
    """{"cuts": {cut_number: (kind, seconds)}, "head": (kind, seconds)|None, "tail": ..., "notes": [...]}"""
    ctx = choose_context(style, context)
    info = (style.get("contexts") or {}).get(ctx)
    notes, cuts = [], {}
    if info and info["kinds"]:
        rate = info["with_transition_pct"] / 100
        kind_ref, _ = next(iter(info["kinds"].items()))
        kind = BUILDABLE.get(kind_ref)
        if kind is None:
            notes.append(f"The reference's usual transition here is a {kind_ref}, which can't be built through "
                         "Resolve's import; using a dissolve.")
            kind = "dissolve"
        elif kind != kind_ref:
            notes.append(f"Reference uses {kind_ref}; built as {kind.replace('_', ' ')}.")
        secs = max(info["typical_frames"], 2) / (style.get("fps") or fps)
        acc = 0.0
        for n in range(1, n_cuts + 1):
            acc += rate
            if acc >= 1.0 - 1e-9:
                acc -= 1.0
                cuts[n] = (kind, round(secs, 3))
    elif ctx:
        notes.append(f"The reference uses hard cuts for {ctx.replace('_', ' ')} -- no transitions added there.")
    head = tail = None
    for key in ("head_fade", "tail_fade"):
        f = style.get(key)
        if f:
            val = ("dissolve", round(max(f["frames"], 2) / (style.get("fps") or fps), 3))
            head, tail = (val, tail) if key == "head_fade" else (head, val)
    return {"context": ctx, "cuts": cuts, "head": head, "tail": tail, "notes": notes,
            "rate_pct": info["with_transition_pct"] if info else 0}
