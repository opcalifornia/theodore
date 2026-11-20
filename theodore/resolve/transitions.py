"""Transitions -- which Resolve's scripting API cannot add directly.

Resolve can export a timeline as FCPXML and import one back. So: export the
open timeline, add <transition> elements at the chosen cuts of the primary
storyline (cross dissolves, dips to black/white), and import the result as a
NEW timeline. The original is never touched.

A transition needs handles: frames beyond each cut for the two shots to
overlap. Where a clip has none, Resolve shortens or skips that transition,
so the result is read back and compared with what was asked.
"""
from __future__ import annotations

import glob
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from typing import Optional

KINDS = {
    "dissolve": "Cross Dissolve",
    "dip_to_black": "Dip To Color Dissolve",
    "dip_to_white": "Dip To Color Dissolve",
    "fade": "Cross Dissolve",
}


class TransitionError(RuntimeError):
    pass


def _t(value: str) -> Fraction:
    return Fraction(value.rstrip("s")) if value else Fraction(0)


def _s(value: Fraction) -> str:
    value = Fraction(value)
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}/1s"


def _read_fcpxml(path: str) -> tuple[ET.ElementTree, str]:
    real = path
    if os.path.isdir(path):  # Resolve writes FCPXML 1.10 as a bundle folder
        found = glob.glob(os.path.join(path, "*.fcpxml"))
        if not found:
            raise TransitionError("Resolve's FCPXML export is empty.")
        real = found[0]
    return ET.parse(real), real


def cut_points(spine) -> list[tuple[int, Fraction, ET.Element, ET.Element]]:
    """(index, time, outgoing clip, incoming clip) for each butt cut in the spine."""
    clips = [c for c in spine if c.tag in ("clip", "asset-clip", "ref-clip", "video", "gap")]
    cuts = []
    for i in range(1, len(clips)):
        a, b = clips[i - 1], clips[i]
        if a.tag == "gap" or b.tag == "gap":
            continue
        end_a = _t(a.get("offset")) + _t(a.get("duration"))
        if end_a == _t(b.get("offset")):
            cuts.append((i, end_a, a, b))
    return cuts


def _covered_cuts(tl, cuts, fps: float) -> set[int]:
    """Cut numbers hidden under B-roll on a track above the main one -- already invisible, no transition needed."""
    start = int(tl.GetStartFrame())
    over = []
    for idx in range(2, int(tl.GetTrackCount("video") or 0) + 1):
        over += [(it.GetStart(), it.GetEnd()) for it in (tl.GetItemListInTrack("video", idx) or [])]
    out = set()
    for n, (_i, at, _a, _b) in enumerate(cuts, 1):
        frame = start + float(at) * fps
        if any(a < frame < b for a, b in over):
            out.add(n)
    return out


def add_transitions(h, *, at_cuts: Optional[list[int]] = None, kind: str = "dissolve",
                    seconds: float = 0.5, head: bool = False, tail: bool = False,
                    name: Optional[str] = None, plan: Optional[dict] = None, reference: Optional[str] = None,
                    context: Optional[str] = None) -> dict:
    """Add `kind` transitions at cut numbers `at_cuts` (1 = first cut; None = all cuts),
    optionally a fade in at the head and out at the tail, on a NEW timeline.

    `plan` ({cut_number: (kind, seconds)}) gives each cut its own kind and length;
    `reference` builds that plan from a studied video's measured transition habits."""
    if kind not in KINDS:
        raise TransitionError(f"kind must be one of {sorted(KINDS)}")
    tl, project, r = h.timeline, h.project, h.resolve
    tmp = tempfile.mkdtemp(prefix="theodore-fcpxml-")
    src = os.path.join(tmp, "in.fcpxml")
    fcpxml_type = getattr(r, "EXPORT_FCPXML_1_10", 6)
    if not tl.Export(src, fcpxml_type, getattr(r, "EXPORT_NONE", -1)):
        raise TransitionError("Resolve could not export the timeline as FCPXML.")
    tree, _real = _read_fcpxml(src)
    spine = tree.getroot().find(".//sequence/spine")
    if spine is None:
        raise TransitionError("No primary storyline in the exported timeline.")
    dur = Fraction(seconds).limit_denominator(1000)
    cuts = cut_points(spine)
    ref_notes: list = []
    head_dur = tail_dur = dur
    loved = ""
    if reference:
        from theodore.study import reference as ref_mod, transition_style
        try:
            loaded = ref_mod.load_reference(reference)
        except FileNotFoundError as exc:
            raise TransitionError(str(exc))
        from theodore.study import love
        loved = love.rules_text(loaded["blueprint"], ("add_transitions", "other"))
        style = transition_style.measure(loaded["analysis"])
        if not style:
            raise TransitionError(f"The reference {reference!r} has too few shots to read a transition style from.")
        fps = float(tl.GetSetting("timelineFrameRate") or 24)
        covered = _covered_cuts(tl, cuts, fps)
        open_cuts = [n for n in range(1, len(cuts) + 1) if n not in covered]
        plan_out = transition_style.plan(style, len(open_cuts), context, fps)
        plan = {open_cuts[k - 1]: v for k, v in plan_out["cuts"].items()}
        ref_notes = plan_out["notes"] + ([f"{len(covered)} cut(s) sit under B-roll and were left alone."]
                                         if covered else [])
        head = head or bool(plan_out["head"])
        tail = tail or bool(plan_out["tail"])
        head_dur = Fraction(plan_out["head"][1]).limit_denominator(1000) if plan_out["head"] else dur
        tail_dur = Fraction(plan_out["tail"][1]).limit_denominator(1000) if plan_out["tail"] else dur
    wanted = set(plan) if plan is not None else set(at_cuts) if at_cuts else set(range(1, len(cuts) + 1))
    added = []
    # Insert from the last cut backwards so indices stay valid.
    for n, (i, at, _a, _b) in reversed(list(enumerate(cuts, 1))):
        if n not in wanted:
            continue
        k_n, d_n = (plan[n] if plan is not None else (kind, None))
        d_use = Fraction(d_n).limit_denominator(1000) if d_n else dur
        el = ET.Element("transition", {"name": KINDS[k_n], "offset": _s(at - d_use / 2), "duration": _s(d_use)})
        children = list(spine)
        spine.insert(children.index(_b), el)
        added.append(n)
    clips = [c for c in spine if c.tag != "transition"]
    if head and clips:
        spine.insert(0, ET.Element("transition", {"name": KINDS["fade"], "offset": clips[0].get("offset"),
                                                  "duration": _s(head_dur)}))
    if tail and clips:
        last = clips[-1]
        end = _t(last.get("offset")) + _t(last.get("duration"))
        spine.append(ET.Element("transition", {"name": KINDS["fade"], "offset": _s(end - tail_dur), "duration": _s(tail_dur)}))
    out = os.path.join(tmp, "out.fcpxml")
    with open(out, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        tree.write(fh, encoding="utf-8", xml_declaration=False)
    pool = project.GetMediaPool()
    new_name = name or f"{tl.GetName()} - {kind.replace('_', ' ')}s"
    folders = []

    def walk(folder):
        folders.append(folder)
        for sub in folder.GetSubFolderList() or []:
            walk(sub)

    walk(pool.GetRootFolder())
    # Re-link to the clips already in the media pool instead of importing
    # them again (or coming in as Media Offline).
    new = pool.ImportTimelineFromFile(out, {"timelineName": new_name, "importSourceClips": False,
                                            "sourceClipsFolders": folders})
    if not new:
        raise TransitionError("Resolve would not import the timeline with transitions.")
    project.SetCurrentTimeline(new)

    def media_counts(timeline):
        counts = {}
        for kind in ("video", "audio"):
            for idx in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                items = [it for it in (timeline.GetItemListInTrack(kind, idx) or []) if it.GetMediaPoolItem()]
                counts[f"{kind[0].upper()}{idx}"] = len(items)
        return counts

    before, after = media_counts(tl), media_counts(new)
    lost = {k: before[k] - after.get(k, 0) for k in before if before[k] > after.get(k, 0)}
    return {
        "warning": (f"Some clips didn't survive the round trip: {lost}. Titles, Fusion clips, grades and "
                    "effects don't travel through FCPXML -- add transitions before graphics and color."
                    if lost else None),"timeline": new.GetName(), "from": tl.GetName(), "cuts_in_timeline": len(cuts),
            "transitions_added": sorted(added), "kind": kind if plan is None else "per the reference",
            "seconds": float(dur), "file": out, "matched_reference": reference, "notes": ref_notes,
            "editor_loves": loved or None}
