"""Interview footage in, a story cut out -- Claude as the story editor.

1. Every source is transcribed by Resolve, word by word (cached per clip).
2. Claude reads it all as numbered sentences and builds the story the way a
   senior interview editor does: a hook, the arc, a button, a call to action
   when it's a commercial -- to a target length.
3. Each chosen sentence becomes a cut on its word boundaries with a few frames
   of air, and the selects are assembled in story order on a NEW timeline,
   with a marker naming each section.

Sources are raw clips in the media pool (picture + sound together) or the
open timeline (every track kept in sync, re-ordered as the story needs).
"""
from __future__ import annotations

import json
from typing import Optional


from theodore import config
from theodore.llm import make_client
from theodore.analyze.claude_client import CostTracker, call_json
from theodore.edit import EDITOR_MODEL

PAD_IN, PAD_OUT = 3, 8   # frames of air before / after a select (lets a breath land)

SYSTEM = """You are the lead story editor at a top commercial and documentary post house, \
cutting interviews into finished stories. You think like the best interview editors:

- Find the story first: who this is, what they want or faced, what changed, why it matters to \
the audience, and the emotional peak.
- HOOK in the first 3-5 seconds: the most surprising, emotional or quotable line -- it can come \
from anywhere in the interview.
- Every select is a complete thought that stands alone. Trim leading throat-clearing ("So,", \
"And", "Yeah, so", "Um") with trim_start_words and trailing tails ("you know?", "and yeah") with \
trim_end_words.
- Prefer the clearest, most energetic delivery when a point is made twice; drop false starts, \
rambles, interviewer questions and anything off-message.
- Build an arc: hook -> context -> challenge/stakes -> turn -> payoff -> button (a line that \
lands as an ending). For commercials add a clear call to action if one was said.
- Never change what someone meant: never join pieces so they claim something they didn't say.
- Pace: ~2.5 spoken words per second. Hit the target length within 10%.

When there are SEVERAL SUBJECTS (people), cut BETWEEN them like a documentary editor:
- Weave, don't stack. Let one person set up a thought and another pay it off, answer, or \
disagree; cut on the beat where a second voice adds something new, not on a schedule.
- Open on the strongest single voice, but bring a second voice in early (within the first \
~15 seconds) so the audience knows this is a conversation of perspectives.
- Avoid a run of more than ~3 selects (or ~25 seconds) from one person while others have \
something to add. A long run is fine only for a story beat that truly needs it.
- Every subject with usable material should be heard; give the lead subject the most time and \
the others at least a quick, meaningful moment. Never invent a dialogue: two people who were \
filmed separately must not seem to be replying to each other unless their words truly connect.
- End on the line that lands hardest, from whichever voice earns it."""

INSTRUCTIONS = """Return ONLY JSON:
{
 "title": "<working title>",
 "logline": "<one sentence: what this cut is about>",
 "structure": [{"section": "<Hook/Context/Challenge/Turn/Payoff/Button/CTA>", "purpose": "..."}],
 "selects": [{"id": "<sentence id from the list>", "section": "<section name>", "subject": "<speaker name>",
              "trim_start_words": 0, "trim_end_words": 0, "why": "<short>"}],
 "estimated_seconds": <number>,
 "alternate_hooks": ["<id>", ...],
 "editor_notes": "<what you'd tell the producer: gaps in the story, pickups to shoot, b-roll needs>"
}
Selects are in STORY order (not source order). Use only ids from the list."""


MAX_RUN_SELECTS, MAX_RUN_SECONDS = 3, 25.0


def subject_label(clip_name: str, subjects: Optional[dict]) -> str:
    """Who is speaking in this clip: the editor's mapping (clip name or a part of it -> person) or,
    failing that, the clip's own name without its extension."""
    low = clip_name.lower()
    for key, person in (subjects or {}).items():
        if key.lower() in low:
            return str(person)
    return clip_name.rsplit(".", 1)[0]


def intercut_report(selects: list[dict], fps: float) -> dict:
    """How the story is shared between subjects: seconds and selects per person, the longest
    unbroken run of one voice, and how many times the cut changes speaker. `selects` carry
    "subject", "a", "b" (frames)."""
    per: dict[str, dict] = {}
    runs, run = [], None
    for sel in selects:
        who, sec = sel["subject"], (sel["b"] - sel["a"]) / fps
        d = per.setdefault(who, {"seconds": 0.0, "selects": 0})
        d["seconds"] += sec
        d["selects"] += 1
        if run and run["subject"] == who:
            run["selects"] += 1
            run["seconds"] += sec
        else:
            run = {"subject": who, "selects": 1, "seconds": sec}
            runs.append(run)
    for d in per.values():
        d["seconds"] = round(d["seconds"], 1)
    longest = max(runs, key=lambda r: r["seconds"], default=None)
    return {"subjects": per, "speaker_changes": max(0, len(runs) - 1),
            "longest_run": ({**longest, "seconds": round(longest["seconds"], 1)} if longest else None)}


def intercut_problems(report: dict, available: list[str]) -> list[str]:
    """Plain-language problems with how the voices are shared. Empty when the weave is fine or
    there is only one subject to weave."""
    if len(available) < 2:
        return []
    out = []
    run = report["longest_run"]
    if run and (run["selects"] > MAX_RUN_SELECTS or run["seconds"] > MAX_RUN_SECONDS):
        out.append(f"{run['subject']} talks for {run['selects']} selects / {run['seconds']}s in a row; "
                   f"break it up with another voice (max ~{MAX_RUN_SELECTS} selects or ~{MAX_RUN_SECONDS:.0f}s).")
    for who in available:
        if who not in report["subjects"]:
            out.append(f"{who} is never heard; give them at least one meaningful moment if they have one.")
    return out


class InterviewError(RuntimeError):
    pass


def _sentences_from_words(words: list[dict], prefix: str) -> list[dict]:
    from theodore.resolve.transcribe import sentences
    out = []
    # Re-split with word indices so selects can be trimmed by word.
    idx = 0
    for k, s in enumerate(sentences(words)):
        count = len(s["text"].split())
        out.append({"id": f"{prefix}.{k + 1}", "text": s["text"], "start": s["start"], "end": s["end"],
                    "words": words[idx:idx + count]})
        idx += count
    return out


def _select_range(sent: dict, trim_start: int, trim_end: int, prev_end: Optional[int],
                  next_start: Optional[int]) -> tuple[int, int]:
    words = sent["words"]
    keep = words[max(0, trim_start): len(words) - max(0, trim_end)] or words
    a = keep[0]["start_frame"] - PAD_IN
    b = keep[-1]["end_frame"] + PAD_OUT
    # Never reach into a neighbouring word: stop at the middle of the gap.
    first_i = words.index(keep[0])
    last_i = words.index(keep[-1])
    before = words[first_i - 1]["end_frame"] if first_i > 0 else prev_end
    after = words[last_i + 1]["start_frame"] if last_i + 1 < len(words) else next_start
    if before is not None:
        a = max(a, (before + keep[0]["start_frame"] + 1) // 2)
    if after is not None:
        b = min(b, (after + keep[-1]["end_frame"]) // 2)
    return max(0, int(a)), int(max(b, a + 1))


def style_from_reference(name: str, target_seconds: float) -> dict:
    """The reference's story shape scaled to `target_seconds`: section budgets, hook, pace, rules,
    and whatever the editor said they love about it."""
    from theodore.study import love, reference
    ref = reference.load_reference(name)
    bp, an = ref["blueprint"], ref["analysis"]
    dur = float((an.get("media") or {}).get("duration") or 0)
    lines = [f"Shape this story like the reference '{name}' (its measured structure, scaled to {target_seconds:.0f}s):"]
    arc = bp.get("story_arc") or bp.get("structure") or []
    for b in arc:
        try:
            a, z = float(b["start"]), float(b["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if dur:
            what = b.get("beat") or b.get("section")
            said = b.get("what_is_said") or b.get("purpose") or ""
            lines.append(f"- {what}: ~{(z - a) / dur * target_seconds:.0f}s ({a / dur * 100:.0f}-{z / dur * 100:.0f}% "
                         f"of the runtime). {said}")
    sp = an.get("speech") or {}
    wps = None
    if sp.get("words_per_minute") and (sp.get("speech_ratio") or 0) >= 0.2:
        wps = sp["words_per_minute"] / 60 * min(1.0, sp["speech_ratio"] + 0.05)
        lines.append(f"- It speaks {sp['words_per_minute']:.0f} words/min while talking and is "
                     f"{sp['speech_ratio'] * 100:.0f}% speech: aim for about {wps * target_seconds:.0f} spoken words.")
    pace = an.get("pacing") or {}
    if pace.get("average_shot_length"):
        lines.append(f"- Average shot length {pace['average_shot_length']}s, {pace.get('cuts_per_minute')} cuts/min -- "
                     "keep selects about that long where the content allows.")
    lines += [f"- {r}" for r in (bp.get("story_rules") or [])[:6]]
    lines += [f"- {r}" for r in (bp.get("pacing_rules") or [])[:4]]
    loved = love.rules_text(bp, ("build_story_cut", "clean_up_speech", "other"))
    if loved:
        lines.append("The editor told me what they love about this reference -- lead with it:\n" + loved)
    return {"rules": "\n".join(lines), "words_per_second": wps, "reference_seconds": dur or None}


def build_story(h, *, clips: Optional[list] = None, use_timeline: bool = False, brief: str = "",
                target_seconds: float = 60.0, name: Optional[str] = None, client=None,
                progress=None, reference: Optional[str] = None, subjects: Optional[dict] = None) -> dict:
    from theodore.resolve import timeline_ops, transcribe

    style = style_from_reference(reference, float(target_seconds)) if reference else None
    if style:
        brief = (brief + "\n\n" if brief else "") + style["rules"]

    say = progress or (lambda m: None)
    sources = []   # {"prefix", "label", "sentences", "clip" | None}
    if use_timeline:
        say("Transcribing the timeline")
        res = transcribe.transcribe_timeline(h, word_level=True)
        sources.append({"prefix": "T", "label": h.timeline.GetName(), "clip": None, "fps": res["fps"],
                        "subject": subject_label(h.timeline.GetName(), subjects),
                        "sentences": _sentences_from_words(res["words"], "T")})
    else:
        if not clips:
            raise InterviewError("Give interview clips (media pool names or a bin), or use_timeline=true.")
        for n, clip in enumerate(clips, 1):
            say(f"Transcribing {clip.GetName()} ({n}/{len(clips)})")
            res = transcribe.transcribe_clip(h, clip, word_level=True)
            if not res["words"]:
                continue
            sources.append({"prefix": f"C{n}", "label": clip.GetName(), "clip": clip, "fps": res["fps"],
                            "subject": subject_label(clip.GetName(), subjects),
                            "sentences": _sentences_from_words(res["words"], f"C{n}")})
    if not sources:
        raise InterviewError("No speech found in the sources.")
    by_id = {s["id"]: (src, i) for src in sources for i, s in enumerate(src["sentences"])}

    listing = []
    for src in sources:
        listing.append(f"## Source {src['prefix']}: {src['label']} (speaker: {src['subject']})")
        listing += [f"{s['id']} [{s['start']:.1f}s, {s['end'] - s['start']:.1f}s] {s['text']}"
                    for s in src["sentences"]]
    total_words = sum(len(s["text"].split()) for src in sources for s in src["sentences"])
    people = list(dict.fromkeys(src["subject"] for src in sources))
    fps = sources[0]["fps"]
    say(f"Claude is building the story from {len(by_id)} sentences"
        + (f" across {len(people)} subjects" if len(people) > 1 else ""))
    client = client or make_client()
    cost = CostTracker()
    ask = (f"Brief: {brief or '(none given -- find the strongest story)'}\n"
           f"Target length: {target_seconds:.0f} seconds (~{int(target_seconds * ((style or {}).get('words_per_second') or 2.5))} words of the "
           f"{total_words} available).\n"
           + (f"Subjects (speakers) available: {', '.join(people)}.\n" if len(people) > 1 else "")
           + "\n" + "\n".join(listing) + "\n\n" + INSTRUCTIONS)

    def resolve_plan(plan_json):
        picked, gone = [], []
        for sel in plan_json.get("selects", []):
            hit = by_id.get(str(sel.get("id")))
            if not hit:
                gone.append(sel.get("id"))
                continue
            src, i = hit
            sents = src["sentences"]
            prev_end = sents[i - 1]["words"][-1]["end_frame"] if i > 0 else None
            next_start = sents[i + 1]["words"][0]["start_frame"] if i + 1 < len(sents) else None
            a, b = _select_range(sents[i], int(sel.get("trim_start_words") or 0),
                                 int(sel.get("trim_end_words") or 0), prev_end, next_start)
            picked.append({"src": src, "a": a, "b": b, "section": sel.get("section", ""),
                           "subject": src["subject"], "text": sents[i]["text"], "why": sel.get("why", "")})
        return picked, gone

    plan = call_json(client, pass_name="interview-story", model=EDITOR_MODEL, system=SYSTEM, user=ask,
                     max_tokens=6000, cost_tracker=cost)
    selects, missing = resolve_plan(plan)
    weave = intercut_report(selects, fps) if selects else None
    problems = intercut_problems(weave, people) if weave else []
    revised = False
    if problems:
        # One revision, told exactly what is lopsided; keep it only if it is no worse.
        say("Re-weaving the voices: " + "; ".join(problems))
        try:
            plan2 = call_json(
                client, pass_name="interview-story-weave", model=EDITOR_MODEL, system=SYSTEM,
                user=ask + "\n\nYour previous plan:\n" + json.dumps(plan) + "\n\nFix these problems and "
                     "return the full corrected JSON, same format:\n- " + "\n- ".join(problems),
                max_tokens=6000, cost_tracker=cost)
            selects2, missing2 = resolve_plan(plan2)
            weave2 = intercut_report(selects2, fps) if selects2 else None
            problems2 = intercut_problems(weave2, people) if weave2 else problems
            if selects2 and len(problems2) <= len(problems):
                plan, selects, missing, weave, problems, revised = plan2, selects2, missing2, weave2, problems2, True
        except Exception:  # noqa: BLE001  the first plan is still a good cut
            pass
    if not selects:
        raise InterviewError(f"Claude's plan matched no sentences (ids: {missing[:5]}).")

    title = name or f"{plan.get('title') or 'Story'} - {int(target_seconds)}s"
    say("Assembling the cut in Resolve")
    labels, last = [], None
    for s in selects:
        labels.append(s["section"] if s["section"] != last else "")
        last = s["section"]
    if use_timeline:
        start = int(h.timeline.GetStartFrame())
        built = timeline_ops.assemble_timeline_ranges(
            h, [(start + s["a"], start + s["b"]) for s in selects], title, labels=labels)
    else:
        built = timeline_ops.assemble_clip_selects(
            h, [{"clip": s["src"]["clip"], "start": s["a"], "end": s["b"], "label": lab,
                 "note": s["why"]} for s, lab in zip(selects, labels)], title)
    return {
        **built, "title": plan.get("title"), "logline": plan.get("logline"),
        "structure": plan.get("structure"), "seconds": round(built["duration_frames"] / fps, 1),
        "target_seconds": target_seconds,
        "story": [{"section": s["section"], "subject": s["subject"], "line": s["text"], "why": s["why"]}
                  for s in selects],
        "alternate_hooks": [by_id[i][0]["sentences"][by_id[i][1]]["text"] for i in plan.get("alternate_hooks", [])
                            if i in by_id],
        "editor_notes": plan.get("editor_notes"), "unmatched_ids": missing, "matched_reference": reference,
        "intercut": {**weave, "problems_left": problems, "re_woven": revised} if len(people) > 1 else None,
    }


def find_clips(h, names: Optional[list] = None, bin_name: Optional[str] = None) -> list:
    pool = h.project.GetMediaPool()

    def walk(folder):
        yield folder
        for sub in folder.GetSubFolderList() or []:
            yield from walk(sub)

    folders = list(walk(pool.GetRootFolder()))
    if bin_name:
        match = [f for f in folders if f.GetName().lower() == bin_name.lower()]
        if not match:
            raise InterviewError(f"No bin named {bin_name!r}.")
        return [c for c in match[0].GetClipList() or []]
    wanted = {n.lower() for n in names or []}
    found = [c for f in folders for c in (f.GetClipList() or []) if c.GetName().lower() in wanted]
    missing = wanted - {c.GetName().lower() for c in found}
    if missing:
        raise InterviewError(f"Not in the media pool: {sorted(missing)}. Use list_media_pool.")
    return found


def dump(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)
