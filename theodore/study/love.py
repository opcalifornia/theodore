"""What the editor loves about a reference, tied to what can be measured.

The editor says it in their own words ("I love how the B-roll comes in late
and the music drops on the reveal", "the dissolves at 0:42 are perfect").
Claude is given those words next to the measurements -- shots, transitions,
cutaways over speech, music bed, anything at a timestamp they mention -- and
says which measurable traits they are pointing at, with evidence, which
Theodore tool replicates each (and with what settings), and what NOT to copy.

Saved beside the study as love.json (every note kept, newest last) and folded
into the blueprint as `editor_love`, so every later edit that uses the
reference leads with what the editor cares about instead of copying everything.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from theodore.analyze.claude_client import CostTracker, call_json

SYSTEM = """You are an editor's right hand. The editor loves something about a finished \
video and tells you in their own words. Your job is to translate that taste into specifics \
you can act on: tie each thing they say to evidence in the measurements (numbers, shot \
indexes, timestamps, the words and pictures at that moment), say which editing tool \
reproduces it and with what settings, and rank by how much they care. Take their words \
literally and weigh emphasis ("by far", "especially") as priority. Do not invent traits \
they did not mention, and do not drop ones they did. If something they said cannot be \
tied to anything measured, put it in `unclear` as a short question."""

INSTRUCTIONS = """Return ONLY JSON:
{"what_they_love": [{"trait": "<short name>", "their_words": "<quote/paraphrase>",
   "evidence": "<specific numbers, shot indexes, timestamps from the data>",
   "replicate": [{"tool": "place_broll|add_transitions|build_story_cut|clean_up_speech|add_graphic|add_captions|match_look|make_social_version|other",
                  "settings": "<params, e.g. reference=<name>, seconds=0.4, coverage=0.45>",
                  "how": "<one sentence on doing it on new footage>"}],
   "weight": <1-5>}],
 "priority_order": ["<trait>", ...],
 "do_not_copy": ["<things in the reference the editor did not praise and that need not be matched>"],
 "unclear": ["<question for the editor>"]}"""

_TS = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def timestamps(text: str) -> list[float]:
    return [int(m) * 60 + int(s) for m, s in _TS.findall(text or "")]


def _moment(analysis: dict, t: float) -> dict:
    """Everything known about second `t`: the shot, its log, the words, the layer label."""
    shot = next((s for s in analysis.get("shots") or [] if s["start_time"] <= t < s["start_time"] + s["duration"]), None)
    out: dict = {"at": t}
    if shot:
        out["shot"] = {k: shot[k] for k in ("index", "start_time", "duration", "transition_in", "transition_out", "motion")
                        if k in shot}
        out["log"] = (analysis.get("shot_log") or {}).get(str(shot["index"]))
        lay = next((r for r in (analysis.get("layers") or {}).get("shots", []) if r["index"] == shot["index"]), None)
        out["label"] = lay and lay["label"]
    words = (analysis.get("transcript_words") or [])
    if words:
        out["said"] = " ".join(w.get("punctuated_word") or w["word"] for w in words if t - 3 <= float(w["start"]) <= t + 3)
    near = [x for x in analysis.get("transitions") or [] if abs(x["time"] - t) <= 2 and x["kind"] != "flash_in_shot"]
    if near:
        out["transitions_near"] = near[:4]
    return out


def _digest(analysis: dict) -> dict:
    from theodore.study import transition_style
    lay = analysis.get("layers") or {}
    return {
        "pacing": analysis.get("pacing"), "transition_summary": analysis.get("transition_summary"),
        "transition_style": transition_style.measure(analysis),
        "picture_over_speech": lay.get("summary"), "cutaway_examples": (lay.get("cutaways") or [])[:12],
        "music": {k: v for k, v in (analysis.get("music") or {}).items() if k not in ("beats", "sync")},
        "music_sync": (analysis.get("music") or {}).get("sync"), "score_vs_speech": lay.get("score"),
        "speech": analysis.get("speech"), "loudness": analysis.get("loudness"),
    }


def interpret(client, words: str, name: str, blueprint: dict, analysis: dict) -> dict:
    payload = {"reference": name, "editor_words": words, "blueprint": blueprint, "measurements": _digest(analysis),
               "moments_they_named": [_moment(analysis, t) for t in timestamps(words)]}
    return call_json(client, pass_name="study-love", model=__import__("theodore.study.vision", fromlist=["x"]).STUDY_MODEL,
                     system=SYSTEM, user=json.dumps(payload, separators=(",", ":"), default=str) + "\n\n" + INSTRUCTIONS,
                     max_tokens=4000, cost_tracker=CostTracker())


def save(ref_dir: Path, words: str, result: dict) -> dict:
    path = ref_dir / "love.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append({"words": words, **result})
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    bp_path = ref_dir / "blueprint.json"
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    bp["editor_love"] = merged(history)
    bp_path.write_text(json.dumps(bp, indent=2), encoding="utf-8")
    report = ref_dir / "report.md"
    if report.exists():
        text = re.sub(r"\n## What the editor loves\n.*?(?=\n## |\Z)", "", report.read_text(encoding="utf-8"), flags=re.S)
        report.write_text(text.rstrip() + "\n\n" + render(bp["editor_love"]) + "\n", encoding="utf-8")
    return bp["editor_love"]


def merged(history: list[dict]) -> dict:
    """Later notes refine earlier ones: same trait -> newest wins; priority follows weight."""
    traits: dict[str, dict] = {}
    not_copy, unclear = [], []
    for note in history:
        for t in note.get("what_they_love") or []:
            traits[str(t.get("trait")).lower()] = t
        not_copy += [x for x in note.get("do_not_copy") or [] if x not in not_copy]
        unclear = list(note.get("unclear") or [])
    ordered = sorted(traits.values(), key=lambda t: -int(t.get("weight") or 0))
    return {"traits": ordered, "do_not_copy": not_copy, "unclear": unclear,
            "their_words": [n["words"] for n in history]}


def render(love: dict) -> str:
    lines = ["## What the editor loves", ""]
    for t in love.get("traits") or []:
        lines.append(f"- **{t.get('trait')}** (weight {t.get('weight')}): {t.get('their_words')}")
        lines.append(f"  - evidence: {t.get('evidence')}")
        for r in t.get("replicate") or []:
            lines.append(f"  - {r.get('tool')}: {r.get('settings')} -- {r.get('how')}")
    if love.get("do_not_copy"):
        lines += ["", "Not needed: " + "; ".join(love["do_not_copy"])]
    if love.get("unclear"):
        lines += ["", "Still unclear: " + "; ".join(love["unclear"])]
    return "\n".join(lines)


def rules_text(blueprint: dict, tools: tuple[str, ...] = ()) -> str:
    """The editor's priorities as prompt lines for a builder (filtered to `tools` when given)."""
    love = (blueprint or {}).get("editor_love") or {}
    out = []
    for t in love.get("traits") or []:
        reps = [r for r in t.get("replicate") or [] if not tools or r.get("tool") in tools]
        if reps or not tools:
            out.append(f"- THE EDITOR LOVES ({t.get('weight')}/5) {t.get('trait')}: {t.get('their_words')} "
                       f"[{t.get('evidence')}] " + " ".join(f"{r.get('how')}" for r in reps))
    if love.get("do_not_copy"):
        out.append("- Not needed from the reference: " + "; ".join(love["do_not_copy"]))
    return "\n".join(out)


def add_note(name: str, words: str, client=None) -> dict:
    from theodore.llm import make_client
    from theodore.study import reference
    ref = reference.load_reference(name)
    result = interpret(client or make_client(), words, name, ref["blueprint"], ref["analysis"])
    return save(Path(ref["dir"]), words, result)
