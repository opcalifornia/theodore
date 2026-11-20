"""Tempo, beats, pacing, and how the cuts relate to the music and the words.

These are the numbers an editor feels rather than counts: is this cut to the
beat, every bar, or against it? Do cuts speed up when the track builds? Do
they land in the pauses between sentences (story editing) or mid-word
(montage over VO)? Each answer is compared with what chance alone would give,
so "62% of cuts are on a beat" means something.
"""
from __future__ import annotations

import numpy as np

from theodore.study.signals import AUDIO_SR, HOP, N_FFT

ENV_RATE = AUDIO_SR / HOP  # onset-envelope frames per second
#: Envelope frame i is the flux of the window starting at i*HOP, which rises
#: as an onset enters the *end* of that window: shift by this to land on it.
ENV_OFFSET = (N_FFT - HOP) / AUDIO_SR


def estimate_tempo(env: np.ndarray, lo_bpm: float = 60, hi_bpm: float = 200) -> tuple[float, float]:
    """(bpm, periodicity 0..1). Autocorrelation with a mild prior toward 120
    BPM, the usual octave-error guard."""
    if len(env) < ENV_RATE * 4 or env.std() < 1e-6:
        return 0.0, 0.0
    x = (env - env.mean()) / (env.std() + 1e-9)
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= ac[0] + 1e-9
    lags = np.arange(int(ENV_RATE * 60 / hi_bpm), int(ENV_RATE * 60 / lo_bpm) + 1)
    lags = lags[lags < n]
    if len(lags) == 0:
        return 0.0, 0.0
    bpms = 60.0 * ENV_RATE / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    best = int(lags[np.argmax(ac[lags] * prior)])
    # Parabolic interpolation: whole-frame lags alone can't tell 120 from 117 BPM.
    lag = float(best)
    if 0 < best < n - 1:
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        denom = y0 - 2 * y1 + y2
        if denom < 0:
            lag = best + 0.5 * (y0 - y2) / denom
    return round(float(60.0 * ENV_RATE / lag), 2), round(float(max(ac[best], 0.0)), 3)


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Dynamic-programming beat tracker (Ellis 2007). Returns beat times (s)."""
    if bpm <= 0 or len(env) == 0:
        return np.array([])
    period = ENV_RATE * 60.0 / bpm
    e = env / (env.std() + 1e-9)
    n = len(e)
    score = e.copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(-hi, -lo + 1)
    penalty = -tightness * np.log(-offsets / period) ** 2
    for t in range(hi, n):
        prev = t + offsets
        cand = score[prev] + penalty
        k = int(np.argmax(cand))
        score[t] = e[t] + cand[k]
        back[t] = prev[k]
    tail = max(0, n - int(period))
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1], dtype=np.float64) / ENV_RATE + ENV_OFFSET


def downbeat_phase(env: np.ndarray, beats: np.ndarray, meter: int = 4) -> int:
    if len(beats) < meter * 2:
        return 0
    idx = np.clip(((beats - ENV_OFFSET) * ENV_RATE).round().astype(int), 0, len(env) - 1)
    strengths = [env[idx[p::meter]].mean() for p in range(meter)]
    return int(np.argmax(strengths))


def _nearest(times: np.ndarray, t: float) -> float:
    i = np.searchsorted(times, t)
    best = np.inf
    for j in (i - 1, i):
        if 0 <= j < len(times):
            best = min(best, abs(times[j] - t))
    return float(best)


def music_sync(cut_times: list[float], beats: np.ndarray, bpm: float, phase: int,
               fps: float, meter: int = 4) -> dict:
    if len(beats) < 4 or not cut_times:
        return {}
    tol = max(1.5 / fps, 0.06)
    period = 60.0 / bpm
    bars = beats[phase::meter]
    on_beat = [c for c in cut_times if _nearest(beats, c) <= tol]
    on_bar = [c for c in cut_times if _nearest(bars, c) <= tol]
    beat_idx = np.searchsorted(beats, np.array(cut_times))
    gaps = np.diff(beat_idx) if len(beat_idx) > 1 else np.array([])
    gap_counts = {}
    for g in gaps:
        if g > 0:
            gap_counts[int(g)] = gap_counts.get(int(g), 0) + 1
    return {
        "tolerance_seconds": round(tol, 3),
        "cuts_on_beat_pct": round(100 * len(on_beat) / len(cut_times), 1),
        "chance_on_beat_pct": round(min(100.0, 100 * 2 * tol / period), 1),
        "cuts_on_downbeat_pct": round(100 * len(on_bar) / len(cut_times), 1),
        "chance_on_downbeat_pct": round(min(100.0, 100 * 2 * tol / (period * meter)), 1),
        "beats_between_cuts": dict(sorted(gap_counts.items())),
    }


def speech_spans(words: list[dict], join_gap: float = 0.35) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    for w in words:
        s, e = float(w["start"]), float(w["end"])
        if spans and s - spans[-1][1] <= join_gap:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    return [(s, e) for s, e in spans]


def speech_relation(cut_times: list[float], words: list[dict], duration: float) -> dict:
    if not words:
        return {"speech_ratio": 0.0}
    spans = speech_spans(words)
    talk = sum(e - s for s, e in spans)
    in_word = 0
    at_sentence_end = 0
    ends = [float(w["end"]) for w in words if str(w.get("punctuated_word", w.get("word", ""))).rstrip()[-1:] in ".?!"]
    for c in cut_times:
        if any(float(w["start"]) < c < float(w["end"]) for w in words):
            in_word += 1
        if ends and _nearest(np.array(sorted(ends)), c) <= 0.6:
            at_sentence_end += 1
    n = max(len(cut_times), 1)
    return {
        "speech_ratio": round(talk / duration, 3) if duration else 0.0,
        "words": len(words),
        "words_per_minute": round(len(words) / (talk / 60), 1) if talk else 0.0,
        "cuts_mid_word_pct": round(100 * in_word / n, 1),
        "cuts_near_sentence_end_pct": round(100 * at_sentence_end / n, 1),
    }


def pacing(shot_durations: list[float], duration: float) -> dict:
    d = np.array(shot_durations, dtype=float)
    if len(d) == 0:
        return {}
    quarters = []
    edges = np.linspace(0, duration, 5)
    starts = np.concatenate([[0], np.cumsum(d)[:-1]])
    for q in range(4):
        sel = d[(starts >= edges[q]) & (starts < edges[q + 1])]
        quarters.append(round(float(sel.mean()), 2) if len(sel) else None)
    return {
        "shots": int(len(d)),
        "average_shot_length": round(float(d.mean()), 2),
        "median_shot_length": round(float(np.median(d)), 2),
        "shortest": round(float(d.min()), 2),
        "longest": round(float(d.max()), 2),
        "cuts_per_minute": round(60.0 * max(len(d) - 1, 0) / duration, 1) if duration else 0.0,
        "shot_length_by_quarter": quarters,
        "flash_shots_under_half_second": int((d < 0.5).sum()),
    }


def energy_curve(rms: np.ndarray, seconds_per_bin: float) -> list[float]:
    per = max(1, int(round(seconds_per_bin * ENV_RATE)))
    n = len(rms) // per
    if n == 0:
        return []
    bins = rms[:n * per].reshape(n, per).mean(axis=1)
    db = 20 * np.log10(bins + 1e-6)
    lo, hi = np.percentile(db, 5), db.max()
    return [round(float(x), 2) for x in np.clip((db - lo) / (hi - lo + 1e-6), 0, 1)]


def cut_energy_correlation(cut_times: list[float], rms: np.ndarray, duration: float,
                           window: float = 4.0) -> float | None:
    """Do cuts come faster when the audio is louder? Pearson r over windows."""
    if duration < window * 4 or len(cut_times) < 4:
        return None
    edges = np.arange(0, duration, window)
    counts = np.histogram(cut_times, bins=np.append(edges, duration))[0]
    per = int(window * ENV_RATE)
    loud = np.array([rms[int(e * ENV_RATE):int(e * ENV_RATE) + per].mean() if int(e * ENV_RATE) < len(rms) else 0
                     for e in edges])
    if counts.std() == 0 or loud.std() == 0:
        return None
    return round(float(np.corrcoef(counts, loud)[0, 1]), 2)
