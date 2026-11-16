"""selection.json: the picked lines in spine order. This is the contract between picking, the Gate 1 report, the builder
and the Gate 2 check.

{ "project": "...", "brand": "...", "source_minutes": 23.3,
  "lines": [ {"beat": "purpose", "file": "/path/DJI_x.WAV", "start": 287.08, "end": 297.4, "text": "...", "block": "thesis"} , ... ] }
Lines are sentence pieces; consecutive lines are butt-joined. `file` may differ from line to line (the closing line can come
from any recording)."""
from __future__ import annotations

import json
import os

from .tagger import SPINE


def load(path): return json.load(open(path))


def validate(sel: dict) -> list[str]:
    problems, seen = [], []
    if not sel.get("lines"): return ["no lines picked"]
    last = -1
    for i, ln in enumerate(sel["lines"]):
        if ln["beat"] not in SPINE: problems.append(f"line {i+1}: unknown beat {ln['beat']!r}")
        elif SPINE.index(ln["beat"]) < last: problems.append(f"line {i+1}: beat {ln['beat']} comes after a later beat (spine order is {' > '.join(SPINE)})")
        else: last = SPINE.index(ln["beat"])
        if ln["end"] <= ln["start"]: problems.append(f"line {i+1}: end before start")
        if not os.path.exists(ln["file"]): problems.append(f"line {i+1}: file not found {ln['file']}")
    if SPINE[-1] not in {l["beat"] for l in sel["lines"]}: problems.append("no closing line picked")
    if sel["lines"][-1]["beat"] != "closing": problems.append("the last line must be the closing line")
    return problems


def stats(sel: dict) -> dict:
    kept = sum(l["end"] - l["start"] for l in sel["lines"])
    src = sel.get("source_minutes", 0) * 60
    return dict(lines=len(sel["lines"]), kept_s=round(kept, 1), kept_pct=round(100 * kept / src, 1) if src else None,
                longest_s=round(max(l["end"] - l["start"] for l in sel["lines"]), 1),
                speakers_files=len({l["file"] for l in sel["lines"]}))


def report(sel: dict) -> str:
    """Gate 1: what the editor reads before anything is built."""
    st, rows = stats(sel), []
    mm = lambda t: f"{int(t//60)}:{int(t%60):02d}"
    cur = None
    for i, l in enumerate(sel["lines"], 1):
        if l["beat"] != cur:
            rows.append(f"\n  {l['beat'].upper()}"); cur = l["beat"]
        rows.append(f"   {i:2d}. [{os.path.basename(l['file'])[-10:-4]} {mm(l['start'])}  {l['end']-l['start']:4.1f}s] {l['text'].strip()}")
    head = (f"GATE 1: {st['lines']} lines, {st['kept_s']} s of speech" +
            (f" = {st['kept_pct']}% of the source" if st["kept_pct"] else "") + f"; longest piece {st['longest_s']} s")
    bad = validate(sel)
    return head + ("\n  PROBLEMS: " + "; ".join(bad) if bad else "\n  spine order OK, closing line present") + "\n" + "\n".join(rows)
