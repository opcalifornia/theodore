"""Delivery (prosody) analysis (v2.0 Part 3): the acoustic signal a
transcript-only pipeline structurally can't see -- composure, hesitation,
emphasis, the pause before an answer. Extracted deterministically here in
Python -- parselmouth/Praat for pitch, intensity, jitter, and shimmer;
the word-level timestamps already in transcript.json for speaking rate,
pause structure, and onset delay -- and handed to Claude only as measured,
speaker-relative textual descriptors, never as raw audio.

Studies of speech-capable models find emotion predictions track the WORDS
far more than the prosody, even when the prompt explicitly says to judge
prosody alone. The only reliable way to get Claude reasoning over delivery
is to make the measurement Python's job and the interpretation Claude's --
exactly like this codebase already does for content strength.

Every feature is scored relative to that SPEAKER'S OWN baseline across the
session (compute_baselines()), never an absolute: a naturally soft-spoken
subject must not read as "low energy" on every segment just because
they're quiet.

Deliberately parselmouth-only, not librosa+parselmouth: parselmouth
(Praat) already covers everything here that needs the audio itself --
pitch, intensity, jitter, shimmer -- with the actual industry-standard
algorithms, so librosa would be a second, redundant dependency. Speaking
rate, pause structure, and onset delay need no audio at all; they're
arithmetic over transcript.json's existing word timestamps.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
import parselmouth
from parselmouth.praat import call

logger = logging.getLogger("theodore.analyze.delivery")

MIN_PITCH_HZ = 75
MAX_PITCH_HZ = 500
INTERNAL_PAUSE_THRESHOLD_SECONDS = 0.3
# Jitter/shimmer are undefined (Praat errors or returns garbage) on very
# short or mostly-unvoiced spans; skip them below this many detected pulses
# rather than report a number that doesn't mean anything.
MIN_VOICED_PULSES_FOR_JITTER = 3

_BASELINE_FEATURES = (
    "pitch_mean_hz", "pitch_range_hz", "intensity_mean_db",
    "jitter_local", "shimmer_local", "speaking_rate_wps",
)

_EMPTY_ACOUSTIC = {
    "pitch_mean_hz": None, "pitch_std_hz": None, "pitch_range_hz": None,
    "intensity_mean_db": None, "jitter_local": None, "shimmer_local": None,
}


class DeliveryError(RuntimeError):
    pass


def _safe_float(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _utterance_index(uid: str) -> int:
    return int(uid[1:])


# --------------------------------------------------------------------------
# Audio-derived features (parselmouth/Praat)
# --------------------------------------------------------------------------

def extract_acoustic_features(sound: "parselmouth.Sound", start: float, end: float) -> dict:
    """Pitch, intensity, jitter, and shimmer for the [start, end) window of
    `sound`. Every value is None -- never 0.0, never a crash -- when Praat
    can't compute it for this window, typically because it's shorter than
    a couple of pitch periods or entirely unvoiced.
    """
    end = min(end, sound.duration)
    if end <= start:
        return dict(_EMPTY_ACOUSTIC)
    try:
        part = sound.extract_part(from_time=start, to_time=end, preserve_times=False)
    except Exception:
        logger.warning("Could not extract audio window [%.2f, %.2f)", start, end)
        return dict(_EMPTY_ACOUSTIC)

    features = dict(_EMPTY_ACOUSTIC)

    try:
        pitch = part.to_pitch(pitch_floor=MIN_PITCH_HZ, pitch_ceiling=MAX_PITCH_HZ)
        f0 = pitch.selected_array["frequency"]
        f0 = f0[f0 > 0]
        if len(f0):
            features["pitch_mean_hz"] = _safe_float(np.mean(f0))
            features["pitch_std_hz"] = _safe_float(np.std(f0))
            features["pitch_range_hz"] = _safe_float(np.ptp(f0))
    except Exception:
        logger.debug("Pitch extraction failed for [%.2f, %.2f)", start, end, exc_info=True)

    try:
        intensity = part.to_intensity()
        db = _safe_float(intensity.get_average())
        # Praat's floor sentinel for an effectively silent window (no real
        # recorded speech reads this low) -- treat it as "nothing to
        # measure" rather than a genuine, very-quiet-but-real intensity
        # value that would then corrupt a speaker's baseline.
        if db is not None and db > -100:
            features["intensity_mean_db"] = db
    except Exception:
        logger.debug("Intensity extraction failed for [%.2f, %.2f)", start, end, exc_info=True)

    try:
        point_process = call(part, "To PointProcess (periodic, cc)", MIN_PITCH_HZ, MAX_PITCH_HZ)
        if call(point_process, "Get number of points") >= MIN_VOICED_PULSES_FOR_JITTER:
            jitter = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
            shimmer = call([part, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
            features["jitter_local"] = _safe_float(jitter)
            features["shimmer_local"] = _safe_float(shimmer)
    except Exception:
        logger.debug("Jitter/shimmer extraction failed for [%.2f, %.2f)", start, end, exc_info=True)

    return features


def intensity_trend(sound: "parselmouth.Sound", start: float, end: float) -> Optional[str]:
    """Returns "rising" / "falling" / "steady": mean intensity of the first
    half of the window vs the second half. None if the window's too short to split
    meaningfully."""
    duration = end - start
    if duration < 0.6:
        return None
    mid = start + duration / 2
    first = extract_acoustic_features(sound, start, mid).get("intensity_mean_db")
    second = extract_acoustic_features(sound, mid, end).get("intensity_mean_db")
    if first is None or second is None:
        return None
    delta = second - first
    if delta > 2.0:
        return "rising"
    if delta < -2.0:
        return "falling"
    return "steady"


# --------------------------------------------------------------------------
# Word-timestamp-derived features (no audio needed)
# --------------------------------------------------------------------------

def speaking_rate_wps(words: list) -> Optional[float]:
    if len(words) < 2:
        return None
    duration = words[-1]["end"] - words[0]["start"]
    return len(words) / duration if duration > 0 else None


def pause_structure(words: list, threshold: float = INTERNAL_PAUSE_THRESHOLD_SECONDS) -> dict:
    gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
    pauses = [g for g in gaps if g >= threshold]
    return {"count": len(pauses), "total_seconds": round(sum(pauses), 3)}


def onset_delay_seconds(question_end: Optional[float], answer_start: float) -> Optional[float]:
    """Gap between a question ending and the answer beginning. None if
    there's no preceding question, or if the answer overlaps/interrupts it
    (a negative "delay" isn't a delay)."""
    if question_end is None:
        return None
    delay = answer_start - question_end
    return round(delay, 3) if delay > 0 else None


# --------------------------------------------------------------------------
# Orchestration: per-utterance -> per-speaker baseline -> per-segment profile
# --------------------------------------------------------------------------

def analyze_utterances(wav_path: Path, transcript: dict) -> dict:
    """{utterance_id: {raw features...}} for every utterance in the
    transcript. Onset delay is computed only where the immediately
    preceding utterance is a DIFFERENT speaker (i.e. this utterance opens a
    turn) -- consecutive same-speaker utterances aren't "answers" to
    anything in this sense."""
    sound = parselmouth.Sound(str(wav_path))
    utterances = transcript["utterances"]

    results = {}
    for i, u in enumerate(utterances):
        words = u.get("words") or []
        acoustic = extract_acoustic_features(sound, u["start"], u["end"])
        trend = intensity_trend(sound, u["start"], u["end"])
        pauses = pause_structure(words) if words else {"count": None, "total_seconds": None}

        prev = utterances[i - 1] if i > 0 else None
        onset = None
        if prev is not None and prev["speaker"] != u["speaker"]:
            onset = onset_delay_seconds(prev["end"], u["start"])

        results[u["id"]] = {
            "speaker": u["speaker"],
            **acoustic,
            "intensity_trend": trend,
            "speaking_rate_wps": speaking_rate_wps(words) if words else None,
            "pause_count": pauses["count"],
            "pause_total_seconds": pauses["total_seconds"],
            "onset_delay_seconds": onset,
        }
    return results


def compute_baselines(utterance_features: dict) -> dict:
    """{speaker_id: {feature_name: {"mean", "std", "n"}}} across every
    utterance for that speaker with a non-None value for that feature. A
    speaker with fewer than 2 samples for a feature gets no baseline entry
    for it -- there's nothing to compare against yet, and every descriptor/
    divergence function below treats a missing baseline as "say nothing"
    rather than dividing by a fabricated number.
    """
    by_speaker: dict = {}
    for feats in utterance_features.values():
        by_speaker.setdefault(feats["speaker"], []).append(feats)

    baselines: dict = {}
    for speaker, rows in by_speaker.items():
        baselines[speaker] = {}
        for name in _BASELINE_FEATURES:
            values = [r[name] for r in rows if r.get(name) is not None]
            if len(values) >= 2:
                baselines[speaker][name] = {
                    "mean": float(np.mean(values)),
                    "std": float(np.std(values)),
                    "n": len(values),
                }
    return baselines


def _answer_speaker(segment: dict, transcript: dict) -> Optional[str]:
    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
    u = utterances_by_id.get(segment["answer_start_utterance"])
    return u["speaker"] if u else None


def aggregate_segment_features(segment: dict, utterance_features: dict, transcript: dict) -> Optional[dict]:
    """Averages per-utterance raw features across a segment's answer
    utterance range into one segment-level feature dict. Onset delay
    prefers the first in-range utterance's own value (computed from
    whatever immediately precedes it in the transcript); if that's absent
    but the segmenter recorded a distinct question utterance, falls back
    to measuring against that instead.
    """
    utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
    lo = _utterance_index(segment["answer_start_utterance"])
    hi = _utterance_index(segment["answer_end_utterance"])
    ids_in_range = sorted(
        (uid for uid in utterance_features if lo <= _utterance_index(uid) <= hi),
        key=_utterance_index,
    )
    if not ids_in_range:
        return None

    merged: dict = {}
    for name in _BASELINE_FEATURES:
        values = [utterance_features[uid][name] for uid in ids_in_range if utterance_features[uid].get(name) is not None]
        merged[name] = float(np.mean(values)) if values else None

    merged["pause_count"] = sum(utterance_features[uid].get("pause_count") or 0 for uid in ids_in_range)
    merged["pause_total_seconds"] = round(
        sum(utterance_features[uid].get("pause_total_seconds") or 0 for uid in ids_in_range), 3,
    )
    merged["intensity_trend"] = utterance_features[ids_in_range[-1]].get("intensity_trend")

    onset = utterance_features[ids_in_range[0]].get("onset_delay_seconds")
    if onset is None and segment.get("question_start_utterance"):
        q = utterances_by_id.get(segment["question_start_utterance"])
        a = utterances_by_id.get(ids_in_range[0])
        if q and a:
            onset = onset_delay_seconds(q["end"], a["start"])
    merged["onset_delay_seconds"] = onset

    return merged


def _relative_pct(value: float, mean: float) -> Optional[float]:
    if mean == 0:
        return None
    return (value - mean) / mean * 100


def describe_segment_delivery(segment_id: str, features: dict, baseline: dict) -> str:
    """The measured, speaker-relative textual profile handed to the
    Selects pass -- exactly what gets reasoned over, never raw audio and
    never an absolute judgment like "flat" or "energetic" without a
    baseline to back it up.
    """
    lines = [f"Segment {segment_id} delivery profile:"]

    rate = features.get("speaking_rate_wps")
    rate_base = baseline.get("speaking_rate_wps")
    if rate is not None and rate_base:
        pct = _relative_pct(rate, rate_base["mean"])
        if pct is not None:
            direction = "below" if pct < 0 else "above"
            lines.append(
                f"  - Speaking rate: {rate:.1f} words/sec "
                f"({abs(pct):.0f}% {direction} baseline of {rate_base['mean']:.1f})"
            )

    prange = features.get("pitch_range_hz")
    prange_base = baseline.get("pitch_range_hz")
    if prange is not None and prange_base and prange_base["mean"]:
        ratio = prange / prange_base["mean"]
        width = "narrow" if ratio < 0.8 else ("wide" if ratio > 1.2 else "typical")
        lines.append(f"  - Pitch range: {width}, {ratio:.1f}x baseline")

    pause_count = features.get("pause_count")
    if pause_count:
        threshold_ms = int(INTERNAL_PAUSE_THRESHOLD_SECONDS * 1000)
        plural = "s" if pause_count != 1 else ""
        lines.append(f"  - Pauses: {pause_count} internal pause{plural} over {threshold_ms}ms")

    onset = features.get("onset_delay_seconds")
    if onset is not None:
        lines.append(f"  - Onset delay: {onset:.1f}s before beginning to answer")

    trend = features.get("intensity_trend")
    if trend and trend != "steady":
        lines.append(f"  - Energy: {trend} across the answer")

    if len(lines) == 1:
        lines.append("  - (not enough data yet for a delivery baseline)")
    return "\n".join(lines)


def divergence_score(features: dict, baseline: dict) -> Optional[float]:
    """Root-mean-square of z-scores across every feature with both a value
    and a baseline: how far this segment's delivery sits from the
    speaker's own norm, magnitude only (not direction -- an unusually
    energetic segment and an unusually flat one both score as "diverges").
    None if there's nothing to compare.
    """
    z_scores = []
    for name in _BASELINE_FEATURES:
        value = features.get(name)
        base = baseline.get(name)
        if value is None or not base or not base.get("std"):
            continue
        z_scores.append((value - base["mean"]) / base["std"])
    if not z_scores:
        return None
    return float(np.sqrt(np.mean(np.square(z_scores))))


def analyze_delivery(wav_path: Path, transcript: dict, analysis: dict) -> dict:
    """The full pass: per-utterance raw features, per-speaker baselines,
    and a per-segment aggregated profile + descriptor + divergence score.
    This is exactly what gets written to delivery.json."""
    utterance_features = analyze_utterances(wav_path, transcript)
    baselines = compute_baselines(utterance_features)

    segments = {}
    for seg in analysis.get("segments", []):
        agg = aggregate_segment_features(seg, utterance_features, transcript)
        if agg is None:
            continue
        speaker = _answer_speaker(seg, transcript)
        baseline = baselines.get(speaker, {})
        segments[seg["id"]] = {
            "speaker": speaker,
            "features": agg,
            "descriptor": describe_segment_delivery(seg["id"], agg, baseline),
            "divergence": divergence_score(agg, baseline),
        }

    return {"utterances": utterance_features, "baselines": baselines, "segments": segments}


def save_delivery(delivery: dict, subj_dir: Path) -> Path:
    out = subj_dir / "delivery.json"
    out.write_text(json.dumps(delivery, indent=2))
    return out


def load_delivery(subj_dir: Path) -> Optional[dict]:
    path = subj_dir / "delivery.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def rank_by_divergence(delivery: dict, analysis: dict) -> list:
    """Segments sorted by delivery divergence from the speaker's own
    baseline, most-diverging first -- per spec, these are "almost always
    your best footage and the hardest to find by reading."
    """
    segments_by_id = {s["id"]: s for s in analysis.get("segments", [])}
    rows = []
    for seg_id, d in delivery.get("segments", {}).items():
        if d.get("divergence") is None:
            continue
        seg = segments_by_id.get(seg_id, {})
        rows.append({
            "segment_id": seg_id,
            "divergence": d["divergence"],
            "label": seg.get("question_label", seg_id),
            "descriptor": d["descriptor"],
        })
    rows.sort(key=lambda r: r["divergence"], reverse=True)
    return rows
