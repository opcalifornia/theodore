"""Find every shot boundary in a finished video and say what kind it is.

Boundaries come in two speeds, found two ways:

* **Fast** -- a hard cut is one frame of large change; a wipe, push, whip,
  flash or graphic stinger is a short run of them. Found as runs of frames
  whose change score stands well above the local norm (so handheld footage
  doesn't read as a stream of cuts).
* **Slow** -- a dissolve or fade spreads its change over many frames, each
  too small to notice, so it's found by comparing frames ``SLOW_K`` apart and
  confirmed with a blend test: every in-between frame of a dissolve is a mix
  of the two shots, which a camera move or a walk through frame is not.

Each multi-frame boundary is then classified from what its in-between frames
look like: a mix of the two shots (dissolve), part one shot and part the
other (wipe/push), brighter than both (flash / dip to white), black (dip /
fade), or none of those (a stylised or graphic transition).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from theodore.study.signals import VideoSignals

LOCAL_WIN = 24          # frames each side for the local-norm estimate
ELEVATED_FLOOR = 0.09   # change score below this is never a boundary
ELEVATED_RATIO = 4.0    # ...and it must be this many times the local median
BOUNDARY_MIN = 0.12     # before/after frames must differ at least this much
SLOW_K = 8              # frames apart for the slow (dissolve/fade) detector
SLOW_MIN = 0.25         # histogram distance across 2*SLOW_K frames
BLACK = 0.07            # mean luma treated as black
STINGER_MAX_HOLD = 8    # held graphic frames inside a two-part transition


@dataclass
class Transition:
    kind: str            # cut | dissolve | fade_in | fade_out | dip_to_black | dip_to_white
                         # | flash | wipe | stinger | stylized
    start: int           # first frame that belongs to the transition (cut: first frame of new shot)
    end: int             # first clean frame of the incoming shot
    strength: float      # how different the two shots are, 0..1
    detail: dict = field(default_factory=dict)

    @property
    def frames(self) -> int:
        return max(0, self.end - self.start)

    def to_dict(self, fps: float) -> dict:
        d = asdict(self)
        d["frames"] = self.frames
        d["time"] = round(self.start / fps, 3)
        d["seconds"] = round(self.frames / fps, 3)
        return d


def _change_score(sig: VideoSignals) -> np.ndarray:
    return np.maximum(sig.d_hist, sig.d_pix * 1.2)


def _local_median(x: np.ndarray, win: int) -> np.ndarray:
    n = len(x)
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        lo, hi = max(0, i - win), min(n, i + win + 1)
        out[i] = np.median(x[lo:hi])
    return out


def _hist_dist(sig: VideoSignals, a: int, b: int) -> float:
    return float(0.5 * np.abs(sig.hist[a] - sig.hist[b]).sum())


def _pix_dist(sig: VideoSignals, a: int, b: int) -> float:
    return float(np.abs(sig.thumbs[a].astype(np.float32) - sig.thumbs[b]).mean() / 255.0)


def _shot_distance(sig: VideoSignals, a: int, b: int) -> float:
    return max(_hist_dist(sig, a, b), _pix_dist(sig, a, b) * 1.2)


def _classify_span(sig: VideoSignals, a: int, b: int) -> tuple[str, dict]:
    """Frames a (last clean outgoing) .. b (first clean incoming), b - a >= 2."""
    A = sig.thumbs[a].astype(np.float32)
    B = sig.thumbs[b].astype(np.float32)
    mids = range(a + 1, b)
    la, lb = float(sig.luma[a]), float(sig.luma[b])
    mid_l = sig.luma[a + 1:b]
    detail = {"luma_out": round(la, 3), "luma_in": round(lb, 3)}

    if la < BLACK and lb >= BLACK:
        return "fade_in", detail
    if lb < BLACK and la >= BLACK:
        return "fade_out", detail
    if len(mid_l) and mid_l.min() < BLACK and min(la, lb) >= BLACK:
        return "dip_to_black", detail
    if len(mid_l) and mid_l.max() > max(la, lb) + 0.25:
        return ("dip_to_white" if mid_l.max() > 0.85 else "flash"), detail

    base = float(np.abs(B - A).mean()) + 1e-6
    # Only pixels where the two shots really differ can say whether a frame
    # is part-A-part-B (wipe) or a mix of both (dissolve).
    differs = np.abs(B - A) > 40
    blend_fits, wipe_fits, alphas = [], [], []
    for m in mids:
        M = sig.thumbs[m].astype(np.float32)
        d = B - A
        alpha = float(np.clip(((M - A) * d).sum() / ((d * d).sum() + 1e-6), 0, 1))
        alphas.append(alpha)
        blend_fits.append(float(np.abs(M - (A + alpha * d)).mean()) / base)
        if differs.sum() >= 20:
            err_a, err_b = np.abs(M - A)[differs], np.abs(M - B)[differs]
            wipe_fits.append((float((np.minimum(err_a, err_b) < 18).mean()), float((err_b < err_a).mean())))
    detail["blend_residual"] = round(float(np.median(blend_fits)), 3)

    if wipe_fits:
        explained = float(np.mean([e for e, _ in wipe_fits]))
        fracs = [f for _, f in wipe_fits]
        detail["wipe_explained"] = round(explained, 3)
        if (explained > 0.7 and all(y >= x - 0.1 for x, y in zip(fracs, fracs[1:]))
                and fracs[-1] - fracs[0] > 0.3):
            return "wipe", detail
    monotonic = all(y >= x - 0.15 for x, y in zip(alphas, alphas[1:]))
    if np.median(blend_fits) < 0.35 and monotonic:
        return "dissolve", detail
    return "stylized", detail


def detect(sig: VideoSignals, fps: float) -> list[Transition]:
    n = sig.n
    if n < 3:
        return []
    score = _change_score(sig)
    local = _local_median(score, LOCAL_WIN)
    elevated = (score >= ELEVATED_FLOOR) & (score >= ELEVATED_RATIO * np.maximum(local, 0.01))
    elevated[0] = False

    # -- fast boundaries: contiguous runs of elevated frames ------------------
    fast: list[Transition] = []
    i = 1
    while i < n:
        if not elevated[i]:
            i += 1
            continue
        j = i
        while True:
            if j + 1 < n and elevated[j + 1]:
                j += 1
                continue
            # A fast dissolve or wipe can have a frame or two that change
            # less than the threshold mid-way; bridge them while the picture
            # is still moving. Frozen frames (a held stinger graphic) break it.
            gap = 0
            while (j + gap + 1 < n and gap < 2 and not elevated[j + gap + 1]
                   and score[j + gap + 1] > max(3 * local[j + gap + 1], 0.03)):
                gap += 1
            if gap and j + gap + 1 < n and elevated[j + gap + 1]:
                j += gap + 1
                continue
            break
        a, b = i - 1, j          # last clean outgoing frame, first clean incoming
        strength = _shot_distance(sig, a, b)
        if j == i:
            # A punch-in (same camera, reframed) barely changes the colours,
            # so a modest change far above a very still norm also counts.
            if strength >= BOUNDARY_MIN or (strength >= 0.07 and score[i] >= 12 * max(local[i], 0.004)):
                fast.append(Transition("cut", i, i, round(strength, 3)))
        elif strength >= BOUNDARY_MIN or sig.luma[a + 1:b + 1].max() > max(sig.luma[a], sig.luma[b]) + 0.25:
            kind, detail = _classify_span(sig, a, b) if b - a >= 2 else ("cut", {})
            if strength < BOUNDARY_MIN:
                kind = "flash_in_shot"
            fast.append(Transition(kind, a + 1, b, round(strength, 3), detail))
        i = j + 1

    # -- stingers: two fast transitions around a short held graphic -----------
    plain = ("cut", "flash_in_shot", "fade_in", "fade_out", "dip_to_black")
    merged: list[Transition] = []
    for t in fast:
        prev = merged[-1] if merged else None
        if (prev and prev.kind not in plain and t.kind not in plain
                and 0 <= t.start - prev.end <= STINGER_MAX_HOLD):
            outgoing = max(prev.start - 1, 0)
            merged[-1] = Transition(
                "stinger", prev.start, t.end, round(_shot_distance(sig, outgoing, t.end), 3),
                {"parts": [prev.kind, t.kind], "hold_frames": t.start - prev.end},
            )
        else:
            merged.append(t)
    fast = merged

    # -- slow boundaries: dissolves and fades ---------------------------------
    taken = np.zeros(n, dtype=bool)
    for t in fast:
        taken[max(0, t.start - SLOW_K):min(n, t.end + SLOW_K + 1)] = True
    k = SLOW_K
    across = np.zeros(n, dtype=np.float32)
    for t in range(k, n - k):
        if not taken[t]:
            across[t] = max(_hist_dist(sig, t - k, t + k), _pix_dist(sig, t - k, t + k) * 1.2)
    slow: list[Transition] = []
    t = k
    while t < n - k:
        if across[t] < SLOW_MIN:
            t += 1
            continue
        u = t
        while u + 1 < n - k and across[u + 1] >= SLOW_MIN * 0.8:
            u += 1
        centre = int(t + np.argmax(across[t:u + 1]))
        # Widen until the frames on either side stop changing.
        a = centre
        while a > 0 and score[a] > max(local[a] * 1.5, 0.012) and centre - a < 3 * k:
            a -= 1
        b = centre
        while b + 1 < n and score[b + 1] > max(local[b + 1] * 1.5, 0.012) and b - centre < 3 * k:
            b += 1
        a, b = max(a - 1, 0), min(b + 1, n - 1)
        if b - a >= 4 and _shot_distance(sig, a, b) >= SLOW_MIN:
            kind, detail = _classify_span(sig, a, b)
            if kind in ("dissolve", "wipe", "fade_in", "fade_out", "dip_to_black", "dip_to_white"):
                slow.append(Transition(kind, a + 1, b, round(_shot_distance(sig, a, b), 3), detail))
        t = u + 1

    # -- dips: a fade out then a fade in through black. Either half may have
    # come from either detector, so this runs on the combined list.
    dipped: list[Transition] = []
    for t in sorted(fast + slow, key=lambda tr: tr.start):
        prev = dipped[-1] if dipped else None
        if prev and prev.kind == "fade_out" and t.kind == "fade_in" and t.start - prev.end <= 24:
            dipped[-1] = Transition("dip_to_black", prev.start, t.end,
                                    round(_shot_distance(sig, max(prev.start - 1, 0), t.end), 3),
                                    {"black_frames": t.start - prev.end})
        else:
            dipped.append(t)
    return dipped


@dataclass
class Shot:
    index: int
    start: int           # first frame
    end: int             # exclusive
    transition_in: str
    transition_out: str
    luma: float
    motion: float        # mean frame-to-frame change inside the shot, 0..1
    key_frame: int

    def to_dict(self, fps: float) -> dict:
        d = asdict(self)
        d["start_time"] = round(self.start / fps, 3)
        d["duration"] = round((self.end - self.start) / fps, 3)
        d["frames"] = self.end - self.start
        return d


def shots_from_transitions(sig: VideoSignals, transitions: list[Transition]) -> list[Shot]:
    """Shots are what lies between boundaries. A transition's frames are
    split at its midpoint, the way an editor's cut point sits in a dissolve."""
    n = sig.n
    bounds = []
    for t in transitions:
        if t.kind == "flash_in_shot":
            continue
        bounds.append(((t.start + t.end) // 2 if t.end > t.start else t.start, t.kind))
    shots = []
    starts = [(0, "start")] + bounds
    for idx, (s, kind_in) in enumerate(starts):
        e = starts[idx + 1][0] if idx + 1 < len(starts) else n
        if e <= s:
            continue
        kind_out = starts[idx + 1][1] if idx + 1 < len(starts) else "end"
        seg = slice(s + 1, e) if e - s > 1 else slice(s, e)
        shots.append(Shot(
            index=len(shots), start=s, end=e, transition_in=kind_in, transition_out=kind_out,
            luma=round(float(sig.luma[s:e].mean()), 3),
            motion=round(float(sig.d_pix[seg].mean()) if e - s > 1 else 0.0, 4),
            key_frame=s + (e - s) // 2,
        ))
    return shots
