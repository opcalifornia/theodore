"""Interview analysis: the whole conversation read like a producer, strategist and archivist at once.

Two mics (one per person) are transcribed separately, each word is kept only for the person actually speaking
(a lav also hears the other voice), and the merged conversation is read as numbered turns. Claude then returns:
the story, the themes, the strongest verbatim quotes, social clips matched to the editor's event/story goals,
what needs fact-checking or care, pickups, and an inventory of every person, place, date, event and claim, so
the intellectual property in the footage is organised, not just cut.
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np

from theodore.analyze.claude_client import CostTracker, call_json
from theodore.edit import EDITOR_MODEL

HOP = 0.05
GAP = 1.2          # a pause this long between words starts a new turn


def merge_speakers(words_by_person: dict, masks: dict, origin: float = 0.0) -> list[dict]:
    """words_by_person: person -> [{"word", "start", "end"}] on the shared clock.
    masks: person -> bool array (HOP s per value, from `origin`). Returns words tagged with a speaker, in time
    order, without the bleed of one person's voice picked up by the other's mic."""
    people = list(words_by_person)
    kept = []
    for who in people:
        others = [p for p in people if p != who]
        for w in words_by_person[who]:
            mid = int(((w["start"] + w["end"]) / 2 - origin) / HOP)

            def active(p):
                m = masks.get(p)
                return m is not None and 0 <= mid < len(m) and bool(m[mid])

            if active(who) or not any(active(o) for o in others):
                kept.append({**w, "speaker": who})
    kept.sort(key=lambda w: (w["start"], w["speaker"]))
    # a word that both mics heard while neither mask was on: keep it once
    out = []
    for w in kept:
        if out and out[-1]["speaker"] != w["speaker"] and abs(out[-1]["start"] - w["start"]) < 0.15 \
                and out[-1]["word"].lower().strip(".,?!") == w["word"].lower().strip(".,?!"):
            continue
        out.append(w)
    return out


def turns(words: list[dict], gap: float = GAP) -> list[dict]:
    """Group words into numbered speaker turns."""
    out: list[dict] = []
    for w in words:
        if out and out[-1]["speaker"] == w["speaker"] and w["start"] - out[-1]["end"] <= gap:
            out[-1]["text"] += " " + w["word"]
            out[-1]["end"] = w["end"]
        else:
            out.append({"id": len(out) + 1, "speaker": w["speaker"], "start": w["start"], "end": w["end"],
                        "text": w["word"]})
    return out


def clock(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


SYSTEM = """You are the producer, story editor, social strategist and archivist for a filmmaker's \
interview. You read a full transcript and make it usable: you find the story, the quotes that carry \
it, the moments that work as short social posts for a given event or message, what needs care or a fact \
check, and an inventory of the intellectual property inside the footage. You quote EXACTLY: every \
`quote` must be copied word for word from the transcript. You never put words in anyone's mouth and \
never invent facts; where the transcript makes a factual claim, you list it to verify, you do not vouch \
for it. Subject matter involving history, politics, violence or living people gets extra care: flag \
claims, allegations and anything that could be taken out of context."""

INSTRUCTIONS = """Return ONLY JSON:
{
 "summary": "<3-4 sentences: who, what this conversation is really about, why it matters>",
 "speaker_notes": {"<speaker>": "<role/who they seem to be, from what they say about themselves>"},
 "story": {"logline": "<one sentence>", "acts": [{"name": "", "start_turn": 0, "end_turn": 0, "what_happens": ""}],
           "emotional_peak_turn": 0, "strongest_open_turn": 0, "strongest_close_turn": 0},
 "themes": [{"theme": "", "why_it_matters": "", "turns": [0]}],
 "best_quotes": [{"turn": 0, "quote": "<verbatim>", "speaker": "", "why": ""}],
 "social_clips": [{"title": "", "start_turn": 0, "end_turn": 0, "platforms": ["Reels/TikTok/Shorts/LinkedIn/X/YouTube"],
                   "hook": "<the first line that stops the scroll>", "why_it_works": "",
                   "event_tie_in": "<how it supports the editor's event/story goal; 'general' if none given>",
                   "caption": "", "length_seconds_estimate": 0, "risk": "<none or what to watch>"}],
 "needs_care": [{"turn": 0, "issue": "<claim to verify, sensitive topic, or out-of-context risk>", "suggestion": ""}],
 "fact_check": [{"turn": 0, "claim": "", "how_to_verify": ""}],
 "ip_inventory": {"people": [], "organizations": [], "places": [], "dates_events": [], "works_and_sources": [],
                  "stories_that_could_be_standalone_pieces": []},
 "pickups_and_broll": ["<what to shoot or find: archive, photos, locations, documents>"],
 "questions_for_the_editor": ["<what you need to know to sharpen this: the event, audience, platform, deadline>"]
}
Turn numbers refer to the numbered turns. Give 8-15 social_clips ranked best first, each 15-90 seconds of \
turns, each a complete thought that works with no context."""


def analyze(turns_list: list[dict], *, goal: str = "", client=None, brand_text: str = "") -> dict:
    from theodore.llm import make_client
    listing = "\n".join(f"[{t['id']}] {clock(t['start'])} {t['speaker']}: {t['text']}" for t in turns_list)
    client = client or make_client()
    user = (f"The editor's goal / event / story: {goal or '(not given: judge by the content, and ask in questions_for_the_editor)'}\n"
            + (f"{brand_text}\n" if brand_text else "")
            + f"\nTranscript, {len(turns_list)} turns:\n{listing}\n\n{INSTRUCTIONS}")
    out = call_json(client, pass_name="interview-analysis", model=EDITOR_MODEL, system=SYSTEM, user=user,
                    max_tokens=12000, cost_tracker=CostTracker())
    by_id = {t["id"]: t for t in turns_list}
    for k in ("best_quotes", "social_clips", "needs_care", "fact_check"):
        for item in out.get(k, []):
            tid = item.get("turn", item.get("start_turn"))
            if tid in by_id:
                item["at"] = clock(by_id[tid]["start"])
                item["start_seconds"] = by_id[tid]["start"]
            if k == "social_clips" and item.get("end_turn") in by_id:
                item["end_seconds"] = by_id[item["end_turn"]]["end"]
    # a quote Claude put in quotation marks must be in the transcript: say so when it isn't
    text = " ".join(t["text"] for t in turns_list).lower()
    norm = lambda s: " ".join("".join(c for c in s.lower() if c.isalnum() or c.isspace()).split())
    corpus = norm(text)
    for q in out.get("best_quotes", []):
        q["verbatim"] = norm(q.get("quote", "")) in corpus
    return out
