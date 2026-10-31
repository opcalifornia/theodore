"""Picture over sound: who is on screen while who is talking.

The shot log says what each shot *is*; the transcript says when words are
spoken. Laid together they show the editor's real system:

  talking_head       talent on camera, speaking
  broll_over_speech  a cutaway playing while the line continues (the story
                     being told in pictures over the voice)
  broll_clean        B-roll with no one speaking (music-led moments)
  talent_silent      talent on camera, not speaking (reaction, listening)
  card               title / end card / graphic

and how each cutaway is handled: how long into the line it comes in, how long
it stays, whether it lands on a phrase break, whether we return to the face
before the line ends. A second pass reads the music bed against the speech
(how far it ducks, how it enters and leaves, where it peaks).

All measured -- the blueprint cites these numbers instead of guessing.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

import numpy as np

TALENT_ROLES = {"a_roll", "reaction"}
CARD_ROLES = {"graphic", "title_card", "end_card"}
SPEECH_MIN = 0.5      # a shot counts as "over speech" when this much of it is spoken
PHRASE_TOL = 0.25     # seconds: a cut this close to a pause/sentence end is "on the phrase"
GAP_MIN = 0.8         # seconds of no speech that count as a real pause for the music read


def _overlap(a0: float, a1: float, spans) -> float:
    return sum(max(0.0, min(a1, e) - max(a0, s)) for s, e in spans)


def label_shots(shots: list[dict], log: dict, spans) -> list[dict]:
    """One row per shot with its picture/sound label."""
    rows = []
    for s in shots:
        a, d = float(s["start_time"]), float(s["duration"])
        v = log.get(s["index"]) or log.get(str(s["index"])) or {}
        role = v.get("role") or "unknown"
        frac = _overlap(a, a + d, spans) / d if d else 0.0
        talking = frac >= SPEECH_MIN
        if role in CARD_ROLES:
            label = "card"
        elif role in TALENT_ROLES:
            label = "talking_head" if talking else "talent_silent"
        else:
            label = "broll_over_speech" if talking else "broll_clean"
        rows.append({"index": s["index"], "start": a, "end": round(a + d, 3), "dur": d, "label": label,
                     "role": role, "size": v.get("shot_size"), "subject": v.get("subject"),
                     "speech_frac": round(frac, 2)})
    return rows


def _words_between(words: list[dict], a: float, b: float) -> str:
    out = [str(w.get("punctuated_word") or w.get("word")) for w in words
           if float(w["start"]) >= a - 0.05 and float(w["end"]) <= b + 0.05]
    return " ".join(out)


def _phrase_breaks(words: list[dict]) -> list[float]:
    """Moments a cut can land cleanly: sentence ends and pauses between words."""
    marks = []
    for i, w in enumerate(words):
        end = float(w["end"])
        text = str(w.get("punctuated_word") or w.get("word") or "").rstrip()
        nxt = float(words[i + 1]["start"]) if i + 1 < len(words) else None
        if text[-1:] in ".?!,;:" or (nxt is not None and nxt - end >= 0.2):
            marks.append(end)
            if nxt is not None:
                marks.append(nxt)
    return marks


def cutaways(rows: list[dict], words: list[dict], spans) -> list[dict]:
    """Runs of consecutive B-roll-over-speech shots, with how each is cut."""
    breaks = np.array(sorted(_phrase_breaks(words))) if words else np.array([])
    runs, cur = [], []
    for r in rows:
        if r["label"] == "broll_over_speech":
            cur.append(r)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        a, b = run[0]["start"], run[-1]["end"]
        span = next(((s, e) for s, e in spans if s - 0.05 <= a <= e + 0.05), None)
        prev = next((r for r in rows if r["end"] <= a + 0.01 and r["index"] == run[0]["index"] - 1), None)
        on_phrase = bool(len(breaks)) and float(np.min(np.abs(breaks - a))) <= PHRASE_TOL
        out.append({
            "at": a, "dur": round(b - a, 2), "shots": len(run),
            "lead_in": round(a - span[0], 2) if span else None,        # how far into the line it enters
            "returns_before_line_ends": bool(span and span[1] - b > 0.3),
            "return_gap": round(span[1] - b, 2) if span and span[1] > b else 0.0,
            "starts_on_phrase": on_phrase,
            "after": prev["label"] if prev else None,
            "sizes": [r["size"] for r in run],
            "covers": _words_between(words, a, b)[:240],
            "pictures": [r["subject"] for r in run],
        })
    return out


def summarize(rows: list[dict], cuts: list[dict], spans, duration: float) -> dict:
    def pct(x):
        return round(100 * x / duration, 1) if duration else 0.0

    by = {}
    for r in rows:
        by[r["label"]] = by.get(r["label"], 0.0) + r["dur"]
    talk = sum(e - s for s, e in spans)
    covered = sum(_overlap(r["start"], r["end"], spans) for r in rows if r["label"] == "broll_over_speech")
    longest_face, run = 0.0, 0.0
    for r in rows:
        run = run + r["dur"] if r["label"] in ("talking_head", "talent_silent") else 0.0
        longest_face = max(longest_face, run)
    durs = [c["dur"] for c in cuts]
    leads = [c["lead_in"] for c in cuts if c["lead_in"] is not None]
    sizes: dict = {}
    for r in rows:
        if r["label"].startswith("broll"):
            sizes[r["size"] or "unknown"] = sizes.get(r["size"] or "unknown", 0) + 1
    seq = []
    for r in rows:
        tag = {"talking_head": "T", "talent_silent": "t", "broll_over_speech": "B", "broll_clean": "b",
               "card": "C"}[r["label"]]
        if seq and seq[-1][0] == tag:
            seq[-1][1] += r["dur"]
        else:
            seq.append([tag, r["dur"]])
    n = len(cuts)
    return {
        "time_pct": {k: pct(v) for k, v in sorted(by.items())},
        "dialogue_covered_by_broll_pct": round(100 * covered / talk, 1) if talk else 0.0,
        "cutaways": n,
        "cutaway_seconds": {"median": round(median(durs), 2), "min": min(durs), "max": max(durs)} if n else {},
        "cutaway_shots_median": median([c["shots"] for c in cuts]) if n else 0,
        "first_cutaway_at": cuts[0]["at"] if n else None,
        "lead_in_seconds_median": round(median(leads), 2) if leads else None,
        "cutaways_on_phrase_pct": round(100 * sum(c["starts_on_phrase"] for c in cuts) / n, 1) if n else 0.0,
        "cutaways_return_before_line_end_pct":
            round(100 * sum(c["returns_before_line_ends"] for c in cuts) / n, 1) if n else 0.0,
        "longest_uninterrupted_face_seconds": round(longest_face, 1),
        "broll_shot_sizes": sizes,
        "sequence": " ".join(f"{t}{d:.0f}" for t, d in seq)[:600],
    }


def score_vs_speech(rms: np.ndarray, spans, duration: float, rate: float, rows: Optional[list[dict]] = None) -> dict:
    """The music bed against the voice: ducking, entry/exit, peaks."""
    if rms is None or len(rms) == 0 or duration <= 0:
        return {}
    db = 20 * np.log10(rms + 1e-6)

    def mean_db(a, b):
        i, j = int(a * rate), max(int(b * rate), int(a * rate) + 1)
        seg = db[i:j]
        return float(seg.mean()) if len(seg) else None

    speech = [mean_db(s, e) for s, e in spans if e - s >= 0.5]
    gaps = []
    for (_, end_prev), (start_next, _) in zip(spans, spans[1:]):
        if start_next - end_prev >= GAP_MIN:
            gaps.append(mean_db(end_prev + 0.15, start_next - 0.15))
    speech, gaps = [x for x in speech if x is not None], [x for x in gaps if x is not None]
    out: dict = {}
    if speech and gaps:
        out["level_under_speech_vs_gaps_db"] = round(float(np.mean(speech) - np.mean(gaps)), 1)
        out["music_rises_in_pauses"] = bool(np.mean(gaps) - np.mean(speech) > 1.5)
    if spans:
        first, last = spans[0][0], spans[-1][1]
        out["audio_before_first_word_s"] = round(first, 2)
        out["audio_after_last_word_s"] = round(duration - last, 2)
        out["loudness_first_word_vs_piece_db"] = round((mean_db(first, first + 3) or 0) - float(np.median(db)), 1)
    out["fades_in"] = bool((mean_db(0, 1) or 0) < (mean_db(duration * 0.1, duration * 0.1 + 1) or 0) - 3)
    out["fades_out"] = bool((mean_db(max(duration - 1, 0), duration) or 0) < (mean_db(duration * 0.8, duration * 0.8 + 1) or 0) - 3)
    per = max(int(2 * rate), 1)
    n = len(db) // per
    if n >= 3:
        bins = db[:n * per].reshape(n, per).mean(axis=1)
        peaks = []
        for i in np.argsort(bins)[::-1][:3]:
            t = i * 2.0
            at = next((r["label"] for r in (rows or []) if r["start"] <= t + 1 < r["end"]), None)
            peaks.append({"at": round(t, 1), "picture": at})
        out["loudest_moments"] = sorted(peaks, key=lambda p: p["at"])
    return out
