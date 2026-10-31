"""Cut it like this: reference + footage in, a finished edit out.

One call runs the editor's chain in the order that survives Resolve's round
trips, each step following the reference's measured habits and what the
editor said they love about it:

  1. story        interviews -> a story to length (sections, pace, hook)
  2. clean-up     ums, stumbles, dead air
  3. B-roll       cutaways over the words, like the reference
  4. check        measure the timeline against the reference; if B-roll is
                  short, one more pass fills the gaps (never stacks on top)
  5. transitions  last, because they rebuild the timeline through FCPXML
  6. final check  the verdict and what is still off

A failure part-way keeps everything built so far and says where it stopped,
instead of throwing the work away.
"""
from __future__ import annotations

from typing import Callable, Optional

from theodore.edit import broll, cleanup, compare, interview


def _short(d: dict, *keys) -> dict:
    return {k: d[k] for k in keys if d.get(k) is not None}


def cut_like_reference(connect: Callable, reference: str, *, target_seconds: float = 60.0, brief: str = "",
                       clips: Optional[list] = None, bin: Optional[str] = None, use_timeline: bool = False,
                       broll_bin: Optional[str] = None, broll_clips: Optional[list] = None,
                       story: bool = True, clean_up: bool = True, transitions: bool = True,
                       name: Optional[str] = None, progress: Optional[Callable] = None) -> dict:
    from theodore.resolve import transitions as trans_mod
    from theodore.study import reference as ref_mod

    ref_mod.load_reference(reference)           # fail early, before anything is built
    log: list[dict] = []
    out: dict = {"reference": reference, "steps": log}

    def say(msg):
        if progress:
            progress(msg)

    def step(label, fn):
        say(label)
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 -- keep what's built, report where it stopped
            log.append({"step": label, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            out["stopped_at"] = label
            return None
        return res

    def record(label, **info):
        h = connect()
        log.append({"step": label, "ok": True, "timeline": h.timeline.GetName() if h.timeline else None, **info})

    h = connect()
    if story:
        sources = None if use_timeline else interview.find_clips(h, clips, bin)
        r = step("story", lambda: interview.build_story(
            h, clips=sources, use_timeline=bool(use_timeline), brief=brief, target_seconds=float(target_seconds),
            name=name, reference=reference, progress=say))
        if r is None:
            return out
        record("story", **_short(r, "seconds", "logline", "editor_notes", "unmatched_ids"))
        out["story"] = {"title": r.get("title"), "structure": r.get("structure"), "story": r.get("story"),
                        "alternate_hooks": r.get("alternate_hooks")}
    if clean_up:
        r = step("clean-up", lambda: cleanup.clean_up(connect(), new_name=None))
        if r is None:
            return out
        record("clean-up", **{k: v for k, v in r.items() if isinstance(v, (int, float, str)) and k != "timeline"})

    library = None
    if broll_bin or broll_clips:
        def place(coverage):
            h2 = connect()
            lib = interview.find_clips(h2, broll_clips, broll_bin)
            return broll.place_broll(h2, lib, coverage=coverage, brief=brief, on_duplicate=False,
                                     reference=reference, progress=say)
        r = step("b-roll", lambda: place(0.4))
        if r is None:
            return out
        record("b-roll", placed=len(r["placed"]), coverage_pct=r["coverage_pct"], refused=r["refused"],
               notes=r.get("notes"))
        check = step("check", lambda: compare.check(connect(), reference))
        if check:
            row = next((x for x in check["rows"] if x["measure"].startswith("B-roll share")), None)
            if row and not row["matches"] and row["edit"] < row["reference"]:
                gap = min(0.9, (row["reference"] - row["edit"]) / 100)
                r2 = step("b-roll (fill the gaps)", lambda: place(gap + row["edit"] / 100))
                if r2:
                    record("b-roll (fill the gaps)", placed=len(r2["placed"]), coverage_pct=r2["coverage_pct"],
                           refused=r2["refused"])
    if transitions:
        r = step("transitions", lambda: trans_mod.add_transitions(connect(), reference=reference))
        if r is None:
            return out
        record("transitions", added=len(r["transitions_added"]), notes=r.get("notes"), warning=r.get("warning"),
               editor_loves=r.get("editor_loves"))
        out["warning"] = r.get("warning")

    final = step("final check", lambda: compare.check(connect(), reference))
    if final:
        out["final_timeline"] = connect().timeline.GetName()
        out["verdict"] = final["verdict"]
        out["still_off"] = final["fixes"]
        out["measured"] = final["rows"]
    return out
