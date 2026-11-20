"""Heuristic first pass: which sentences carry feeling (value) and which are intros, resumes or logistics.

This only ranks and filters the haystack; the final pick is read and judged by Theodore. It was calibrated against the
editor's partner's final CFF Recap (see calibrate()): his picks should land in the top of this ranking."""
from __future__ import annotations

import re

SPINE = ["purpose", "gratitude", "belief", "proof", "giving", "action", "closing"]

# --- things the final cut throws away -------------------------------------------------------------------------
INTRO = re.compile(r"\b(my name is|i am the|i'?m the|i'?m a |i am a |i serve|i work|i hold|i wear|i represent|i also (am|work|serve)|"
                   r"known as|call myself|go by|everybody knows me|people simply think|i started my|raised in|specialty|my business|"
                   r"we focus on|i (have|'ve) been (doing|in|working)|ceo|executive director|superintendent|commissioner|trustee|"
                   r"vice president|board member|former|alumni|alumna|nonprofit|non-profit|founder|supervisor|principal of)\b", re.I)
LOGISTICS = re.compile(r"\b(pass around|a mic|the mic|mic time|record it|too loud|agenda|schedule|coming up|first week of|next week|"
                       r"can you hear|hold on|let me|over here|on the table|round the|start with|go ahead|give this back|"
                       r"\$\s?\d|per hour|apprenticeship|10-week|union|certified|recruit|funding|grant)\b", re.I)
GENERIC = re.compile(r"(thank (you|y'?all)( all)?( so much)?( for (the )?(invit\w+|invitation|welcom\w+|having|coming))|thank you\.?$|anything i can do|sitting here|i'?m honored to be here|good afternoon)", re.I)
SPECIFIC = re.compile(r"\b(taking away|so much more|in tears|bad days?|reason we do|didn'?t want them to leave|pulling away|came here as|north star|lights on|pass the baton|mini-?moguls?)\b", re.I)
FRAGMENT = re.compile(r"^(yeah|yes|so|and|but|okay|ok|um|uh|right|well)[\s,.!?]*$", re.I)

# --- things the final cut keeps -------------------------------------------------------------------------------
VALUE = {
    "gratitude": re.compile(r"\b(grateful|thank(s| you| y'?all)?|appreciat\w+|honou?red|humbled|blessed)\b", re.I),
    "excitement": re.compile(r"\b(excited|exciting|amazing|awesome|inspir\w+|wonderful\w*|beautiful|incredible|love (to|any|sitting)|proud|dope|magic|wild)\b", re.I),
    "belief": re.compile(r"\b(power of (the )?youth|youth|creative|change the world|north star|uplift|shin(e|ing)|keep your lights|mini-?moguls?|moguls?|young moguls|future|next generation|pass the baton)\b", re.I),
    "proof": re.compile(r"\b(came here as|became a|then (she|he) (starts|started)|leading assemblies|throwing rallies|taking leadership|in tears|pulling away|outshine|first-?hand|success stor\w+|won a|number one)\b", re.I),
    "giving": re.compile(r"\b(more than (i'?m|i am|i)\s*(ever\s*)?(giving|expected)|give back|giving back|the reason we do what we do|bad days?|for the kids|value (add|of those))\b", re.I),
    "action": re.compile(r"\b(we can'?t stop|can'?t stop|keep (going|your lights)|conversation|what happens next|let flow|energy flows|resources|assets|circle|show up)\b", re.I),
    "closing": re.compile(r"\b(resonate|through your actions|how far (their|the)|shown these kids|being here today)\b", re.I),
}
WEIGHT = {"gratitude": 1.6, "excitement": 1.5, "belief": 1.6, "proof": 2.2, "giving": 2.4, "action": 1.4, "closing": 2.6}


def words(t: str) -> int:
    return len(re.findall(r"[A-Za-z']+", t))


def score(text: str) -> dict:
    t = text.strip()
    n = words(t)
    tags = [k for k, rx in VALUE.items() if rx.search(t)]
    s = sum(WEIGHT[k] for k in tags)
    if INTRO.search(t): s -= 2.4
    if LOGISTICS.search(t): s -= 1.4
    if t.endswith("?") or re.match(r"^(what|why|how|who|where|when|did|do|can|could|would|should)\b", t, re.I): s -= 1.0
    if GENERIC.search(t): s -= 1.8
    if SPECIFIC.search(t): s += 1.2
    if FRAGMENT.match(t): s -= 2.0
    if n < 5: s -= 1.0
    if 8 <= n <= 45: s += 0.5          # sentences long enough to stand alone, short enough to land
    if n > 70: s -= 0.8
    kind = "value" if s >= 1.0 and tags else ("intro/resume/logistics" if (INTRO.search(t) or LOGISTICS.search(t)) else "other")
    return dict(score=round(s, 2), tags=tags, kind=kind, words=n)


def sentences(segments: list[dict]) -> list[dict]:
    """Merge Whisper segments into sentences (split on . ? !) keeping start/end seconds."""
    out, cur = [], None
    for g in segments:
        if cur is None:
            cur = dict(s=g["s"], e=g["e"], t=g["t"])
        else:
            cur["e"], cur["t"] = g["e"], cur["t"] + " " + g["t"]
        if re.search(r"[.?!][\"')\]]*$", g["t"].strip()):
            out.append(cur); cur = None
    if cur: out.append(cur)
    return out


def rank(sents: list[dict]) -> list[dict]:
    for x in sents: x.update(score(x["t"]))
    return sorted(sents, key=lambda x: -x["score"])


def calibrate(sents: list[dict], used) -> dict:
    """How well does the ranking recover a known good edit? `used(s,e)` -> fraction of the sentence the editor kept."""
    for x in sents: x["used"] = used(x["s"], x["e"]) >= 0.5
    r = rank([dict(x) for x in sents]); pos = [x for x in r if x["used"]]
    n = len(r); res = {"sentences": n, "kept_by_editor": len(pos)}
    for frac in (0.15, 0.25, 0.4):
        k = max(1, int(n * frac)); res[f"recall@top{int(frac*100)}%"] = round(sum(1 for x in r[:k] if x["used"]) / max(1, len(pos)), 2)
    # how many resume/logistics lines sneak into the top 25%
    k = int(n * 0.25); res["intro/logistics in top25%"] = sum(1 for x in r[:k] if x["kind"] == "intro/resume/logistics")
    ranks = {id(x): i for i, x in enumerate(r)}
    return res
