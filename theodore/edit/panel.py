"""Multicam panels and interviews: sync, multicam, AI angle cut -- all of it.

Cameras and lavs start recording at different moments and nobody jams
timecode on an indie shoot, so everything is lined up from the sound itself:

1. Sync. Every file's audio is cross-correlated (GCC-PHAT, 8 kHz) against a
   reference camera's scratch track -> each file's start time on one clock.
2. Who's talking. Each lav, placed on that clock, gives a speech-activity
   curve; a person is "talking" when their lav is clearly loudest (bleed from
   the others sits well below), smoothed so breaths don't flicker.
3. Who's on which camera. Unless told, on-camera motion (mouths, gestures)
   is correlated with each lav's speech; the best assignment wins. The wide
   is the camera that tracks nobody in particular.
4. The cut, the way a multicam editor cuts a panel: open and close on the
   wide, the speaker's angle while they talk, the wide on cross-talk and
   quick exchanges, a wide break inside long answers, no shot under ~1.5 s.
5. Delivered as FCPXML -- a real multicam clip (camera angles, synced) and a
   timeline of multicam cuts with every lav on its own track underneath -- and
   imported into Resolve, so every angle choice stays switchable there.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import permutations
from pathlib import Path
from typing import Optional

import numpy as np

SR = 8000
HOP = 0.05  # seconds per activity frame
MIN_SHOT = 1.5
LONG_ANSWER = 12.0
LEAD = 0.35            # a cut to a new speaker lands this early: the eye finds them as the voice arrives
REACTION_AFTER = 7.0   # a single angle held this long gets a reaction shot of someone listening
REACTION_LEN = 2.0
WIDE_MAX = 14.0         # a wide held longer than this gets cutaways
REACTION_EVERY = 8.0   # long holds get a reaction about this often


class PanelError(RuntimeError):
    pass


def _audio(path: str, seconds: Optional[float] = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR)]
    if seconds:
        cmd += ["-t", f"{seconds:.1f}"]
    res = subprocess.run(cmd + ["-f", "f32le", "-"], capture_output=True, timeout=1800)
    if res.returncode != 0 or not res.stdout:
        raise PanelError(f"Could not read audio from {path}")
    return np.frombuffer(res.stdout, dtype=np.float32)


def _probe(path: str) -> dict:
    import json
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                         capture_output=True, text=True, timeout=120).stdout
    data = json.loads(out or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, _, den = ((v or {}).get("r_frame_rate") or "24/1").partition("/")
    return {"duration": float(data.get("format", {}).get("duration") or 0), "has_video": v is not None,
            "has_audio": a is not None, "fps": Fraction(int(num), int(den or 1)),
            "width": int((v or {}).get("width") or 0), "height": int((v or {}).get("height") or 0),
            "channels": int((a or {}).get("channels") or 1), "sample_rate": int((a or {}).get("sample_rate") or 48000)}


def offset_seconds(ref: np.ndarray, other: np.ndarray, max_shift: float = 600.0) -> tuple[float, float]:
    """How many seconds after `ref` starts `other` starts (negative: before), and a 0..1 confidence."""
    n = 1 << int(np.ceil(np.log2(len(ref) + len(other))))
    a, b = np.fft.rfft(ref, n), np.fft.rfft(other, n)
    cross = a * np.conj(b)
    cc = np.fft.irfft(cross / (np.abs(cross) + 1e-9), n)   # GCC-PHAT: sharp peak, robust to EQ/level
    lag_max = int(max_shift * SR)
    cc = np.concatenate([cc[-min(lag_max, len(other)):], cc[:min(lag_max, len(ref))]])
    k = int(np.argmax(cc))
    lag = k - min(lag_max, len(other))
    peak = cc[k]
    conf = float(peak / (np.sort(np.abs(cc))[-50:].mean() * 3 + 1e-9))
    return lag / SR, min(conf, 1.0)


@dataclass
class Source:
    name: str
    path: str
    kind: str              # camera | lav
    info: dict
    start: float = 0.0     # on the shared clock (seconds)
    confidence: float = 1.0
    person: Optional[str] = None
    role: Optional[str] = None   # wide | close | roam (pans between people)
    parts: Optional[list] = None  # several clips on one camera/mic: [{path, start, duration, tc, info, name}]
    tc: float = 0.0              # the file's own start timecode, in seconds


def spans(src: "Source") -> list[dict]:
    """The clips behind a camera or mic, on the shared clock: {path, start, duration, tc, info, name}."""
    if src.parts:
        return sorted(src.parts, key=lambda p: p["start"])
    return [{"path": src.path, "start": src.start, "duration": src.info["duration"], "tc": src.tc, "info": src.info,
             "name": src.name}]


@dataclass
class PanelPlan:
    cameras: list
    lavs: list
    fps: Fraction
    length: float
    origin: float
    cuts: list = field(default_factory=list)      # (start, end, camera name, why) on the shared clock
    speech: dict = field(default_factory=dict)    # person -> [(start, end)]
    notes: list = field(default_factory=list)
    size: Optional[tuple] = None                  # timeline (width, height); default the first camera's
    attach_lavs: bool = True                      # False: the caller places the mics itself, on tracks of its choosing


def sync(cameras: list[Source], lavs: list[Source], window: float = 240.0) -> list[str]:
    """Line everything up from the sound. A camera with no sound can't be synced that way: it is
    assumed to start with the reference (true of frame-locked rigs) and the notes say so. Returns notes."""
    notes = []
    hearing = [c for c in cameras if c.info["has_audio"]]
    for c in cameras:
        c.start, c.confidence = 0.0, 1.0
    if not hearing:
        for s in lavs:
            s.start, s.confidence = 0.0, 1.0
        notes.append("No camera recorded sound, so nothing could be synced from audio: every camera and mic is "
                     "assumed to start together (right for frame-locked rigs). Check lip sync on the first cut.")
        return notes
    ref = hearing[0]
    ref_audio = _audio(ref.path, window * 2)
    for s in [c for c in hearing if c is not ref] + lavs:
        if not s.info["has_audio"]:
            raise PanelError(f"{s.name} has no audio to sync from.")
        off, conf = offset_seconds(ref_audio, _audio(s.path, window * 2))
        s.start, s.confidence = off, conf
    silent = [c.name for c in cameras if not c.info["has_audio"]]
    if silent:
        notes.append(f"{', '.join(silent)} recorded no sound: assumed to start with {ref.name}. Check lip sync.")
    return notes


def activity(lavs: list[Source], length: float, origin: float) -> dict[str, np.ndarray]:
    """Per-person talking mask on the shared clock, one value per HOP."""
    frames = int(np.ceil(length / HOP))
    levels = {}
    for s in lavs:
        out = levels.setdefault(s.person or s.name, np.full(frames, -120.0))
        for sp in spans(s):
            x = _audio(sp["path"])
            hop = int(HOP * SR)
            rms = np.sqrt(np.add.reduceat(x[: len(x) // hop * hop] ** 2, np.arange(0, len(x) // hop * hop, hop)) / hop)
            db = 20 * np.log10(rms + 1e-6)
            first = int(round((sp["start"] - origin) / HOP))
            lo, hi = max(first, 0), min(first + len(db), frames)
            if hi > lo:
                out[lo:hi] = np.maximum(out[lo:hi], db[lo - first: hi - first])
    names = list(levels)
    stack = np.stack([levels[n] for n in names])
    floor = np.percentile(stack, 20, axis=1, keepdims=True)
    masks = {}
    for i, n in enumerate(names):
        others = np.delete(stack, i, axis=0).max(axis=0) if len(names) > 1 else np.full(frames, -120.0)
        on = (stack[i] > floor[i] + 12) & (stack[i] > others + 6)
        # Hangover: bridge gaps under 0.5 s (breaths, short pauses), drop blips under 0.3 s.
        on = _close_gaps(on, int(0.5 / HOP))
        on = _drop_short(on, int(0.3 / HOP))
        masks[n] = on
    return masks


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0]))


def _close_gaps(mask, n):
    out = mask.copy()
    runs = _runs(mask)
    for (a0, a1), (b0, _b1) in zip(runs, runs[1:]):
        if b0 - a1 <= n:
            out[a1:b0] = True
    return out


def _drop_short(mask, n):
    out = mask.copy()
    for a, b in _runs(mask):
        if b - a < n:
            out[a:b] = False
    return out


def _motion(cam: Source, length: float, origin: float, fps: int = 6) -> np.ndarray:
    """Centre-weighted frame-to-frame motion of a camera on the shared clock."""
    w, h = 64, 36
    res = subprocess.run(["ffmpeg", "-v", "error", "-i", cam.path, "-vf",
                          f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, timeout=1800)
    frames = np.frombuffer(res.stdout, dtype=np.uint8)
    frames = frames[: len(frames) // (w * h) * (w * h)].reshape(-1, h, w).astype(np.float32)
    if len(frames) < 3:
        return np.zeros(int(np.ceil(length / HOP)))
    centre = frames[:, h // 6: h * 5 // 6, w // 4: w * 3 // 4]
    m = np.abs(np.diff(centre, axis=0)).mean(axis=(1, 2))
    t = (np.arange(len(m)) + 1) / fps + cam.start - origin
    grid = np.arange(int(np.ceil(length / HOP))) * HOP
    return np.interp(grid, t, m, left=0, right=0)


def assign_people(cameras: list[Source], masks: dict[str, np.ndarray], length: float, origin: float,
                  mapping: Optional[dict] = None, wide: Optional[str] = None) -> list[str]:
    notes = []
    people = list(masks)
    if mapping:
        for cam in cameras:
            for person, cam_name in mapping.items():
                if cam_name.lower() in cam.name.lower():
                    cam.person, cam.role = person, "close"
    if wide:
        for cam in cameras:
            if wide.lower() in cam.name.lower():
                cam.role, cam.person = "wide", None
    unassigned = [c for c in cameras if c.role is None]
    free_people = [p for p in people if p not in {c.person for c in cameras}]
    if unassigned and free_people:
        motion = {c.name: _motion(c, length, origin) for c in unassigned}
        best, best_score = None, -1e9
        k = min(len(unassigned), len(free_people))
        for cams in permutations(unassigned, k):
            score = 0.0
            for cam, person in zip(cams, free_people):
                mo, sp = motion[cam.name], masks[person].astype(float)
                score += float(np.corrcoef(mo, sp)[0, 1]) if mo.std() > 0 and sp.std() > 0 else 0.0
            if score > best_score:
                best, best_score = cams, score
        strong = best_score / max(k, 1) > 0.15
        if strong:
            for cam, person in zip(best, free_people):
                cam.person, cam.role = person, "close"
            notes.append(f"Matched cameras to speakers from on-camera motion (score {best_score / k:.2f}).")
        leftovers = [c for c in unassigned if c.role is None]
        if leftovers and not any(c.role == "wide" for c in cameras):
            leftovers[0].role = "wide"
            leftovers = leftovers[1:]
        if not strong:
            for cam, person in zip(leftovers, [p for p in free_people if p not in {c.person for c in cameras}]):
                cam.person, cam.role = person, "close"
            notes.append("Couldn't tell from the picture who each camera is on -- assigned in order. "
                         "Say e.g. 'Cam B is on Daniel' to fix it, and I'll recut.")
    for cam in cameras:
        if cam.role is None:
            cam.role = "wide" if not any(c.role == "wide" for c in cameras) else "close"
    return notes


def plan_cut(cameras: list[Source], masks: dict[str, np.ndarray], length: float,
             framing: Optional[dict] = None) -> list[tuple]:
    """`framing`: for a roaming camera, person -> mask of when it is framing that person."""
    roam = next((c.name for c in cameras if c.role == "roam"), None)
    wide = next((c.name for c in cameras if c.role == "wide"), cameras[0].name)
    by_person = {c.person: c.name for c in cameras if c.person}
    frames = int(np.ceil(length / HOP))
    choice, why = [wide] * frames, ["wide"] * frames
    talking = np.stack([masks[p] for p in masks]) if masks else np.zeros((0, frames), bool)
    names = list(masks)
    for t in range(frames):
        who = [names[i] for i in range(len(names)) if talking[i, t]]
        if len(who) == 1 and who[0] in by_person:
            choice[t], why[t] = by_person[who[0]], f"{who[0]} talking"
        elif len(who) == 1 and roam and framing and framing.get(who[0]) is not None and framing[who[0]][t]:
            choice[t], why[t] = roam, f"{who[0]} talking"      # the pan camera is on them right now
        elif len(who) > 1:
            choice[t], why[t] = wide, "cross-talk"
    # Open and close on the wide.
    for t in range(min(frames, int(3 / HOP))):
        choice[t], why[t] = wide, "opening wide"
    for t in range(max(0, frames - int(2 / HOP)), frames):
        choice[t], why[t] = wide, "closing wide"
    segs = []
    for t in range(frames):
        if segs and segs[-1][2] == choice[t]:
            segs[-1][1] = t + 1
        else:
            segs.append([t, t + 1, choice[t], why[t]])
    # No shot under MIN_SHOT: fold it into its longer neighbour.
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if (s[1] - s[0]) * HOP < MIN_SHOT:
                j = i - 1 if i > 0 and (i == len(segs) - 1 or segs[i - 1][1] - segs[i - 1][0] >= segs[i + 1][1] - segs[i + 1][0]) else i + 1
                lo, hi = min(i, j), max(i, j)
                keep = segs[j]
                segs[lo:hi + 1] = [[segs[lo][0], segs[hi][1], keep[2], keep[3]]]
                changed = True
                break
    # Folding can leave neighbours on the same angle: they are one shot, and one long hold needs its reactions.
    merged_segs = []
    for seg in segs:
        if merged_segs and merged_segs[-1][2] == seg[2]:
            merged_segs[-1][1] = seg[1]
        else:
            merged_segs.append(seg)
    segs = merged_segs
    # Cut a little BEFORE the new speaker's first word (the sound of the lav is separate, so only the
    # picture moves) -- never so far that the shot we leave drops under MIN_SHOT.
    lead = int(round(LEAD / HOP))
    for i in range(1, len(segs)):
        # (not for a roaming camera: it is only on them from the moment it arrives)
        if segs[i][2] not in (wide, roam) and segs[i][2] != segs[i - 1][2] and "talking" in segs[i][3]:
            room = (segs[i - 1][1] - segs[i - 1][0]) - int(round(MIN_SHOT / HOP))
            shift = max(0, min(lead, room))
            segs[i][0] -= shift
            segs[i - 1][1] -= shift
    listeners = {c.person: c.name for c in cameras if c.person and c.role == "close"}
    reacted = 0
    # Long answers: cut away to someone listening (a reaction), else a 2.5 s wide break in the middle.
    out = []
    for a, b, cam, reason in segs:
        dur = (b - a) * HOP
        speaker = reason.replace(" talking", "") if reason.endswith(" talking") else None
        others = [n for p, n in listeners.items() if p != speaker and n != cam]
        if speaker and others and dur > REACTION_AFTER:
            # A reaction every ~REACTION_EVERY seconds of a long hold, rotating through the listeners.
            parts = max(2, round(dur / REACTION_EVERY))
            half = int(REACTION_LEN / HOP) // 2
            pieces, cursor = [], a
            for k in range(1, parts):
                mid = a + (b - a) * k // parts
                cut_to = others[reacted % len(others)]
                who = next(p for p, n in listeners.items() if n == cut_to)
                reacted += 1
                pieces += [(cursor * HOP, (mid - half) * HOP, cam, reason),
                           ((mid - half) * HOP, (mid + half) * HOP, cut_to, f"reaction: {who} listening")]
                cursor = mid + half
            pieces.append((cursor * HOP, b * HOP, cam, reason))
            out += pieces
        elif cam == wide and dur > WIDE_MAX and listeners:
            # A wide held this long (cross-talk, or a speaker with no camera) cuts away to people in the room.
            # The opening 3 s and closing 2 s of the cut stay on the wide.
            lo = a + (int(3 / HOP) if a == 0 else 0)
            hi = b - (int(2 / HOP) if b == frames else 0)
            names = list(listeners.items())
            parts = max(2, round((hi - lo) * HOP / REACTION_EVERY))
            half = int(REACTION_LEN / HOP) // 2
            pieces, cursor = [], a
            for k in range(1, parts):
                mid = lo + (hi - lo) * k // parts
                who, cut_to = names[reacted % len(names)]
                reacted += 1
                pieces += [(cursor * HOP, (mid - half) * HOP, cam, reason),
                           ((mid - half) * HOP, (mid + half) * HOP, cut_to, f"cutaway: {who} listening")]
                cursor = mid + half
            pieces.append((cursor * HOP, b * HOP, cam, reason))
            out += pieces
        elif cam != wide and dur > LONG_ANSWER:
            mid = (a + b) // 2
            br = int(2.5 / HOP)
            out += [(a * HOP, mid * HOP, cam, reason), (mid * HOP, (mid + br) * HOP, wide, "wide break in a long answer"),
                    ((mid + br) * HOP, b * HOP, cam, reason)]
        else:
            out.append((a * HOP, b * HOP, cam, reason))
    return out


FRAME_EVERY = 2.0
FRAMING_PROMPT = """These numbered frames come from ONE camera that pans between two people sitting side by side. \
For each frame say who the shot is centred on: "left" (the person on the viewer's left), "right" (the person on the \
viewer's right), "both" (both clearly in frame, neither favoured), or "neither" (mid-pan blur, a wall, something else). \
Return ONLY JSON: {"frames": [{"n": 1, "on": "left"}, ...]} covering every frame."""


def classify_frames(jpgs: list, client=None) -> list[str]:
    """Who each frame is centred on: left | right | both | neither (Claude looks at them in batches)."""
    import base64
    from theodore.analyze.claude_client import CostTracker, call_json
    from theodore.edit.reframe import VISION_MODEL
    from theodore.llm import make_client
    client = client or make_client()
    out: list[str] = []
    for i in range(0, len(jpgs), 10):
        batch = jpgs[i:i + 10]
        content = []
        for n, f in enumerate(batch, 1):
            content += [{"type": "text", "text": f"Frame {n}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(Path(f).read_bytes()).decode()}}]
        content.append({"type": "text", "text": FRAMING_PROMPT})
        data = call_json(client, pass_name="roam-framing", model=VISION_MODEL,
                         system="You judge camera framing precisely.", user=content, max_tokens=800,
                         cost_tracker=CostTracker())
        got = {int(f.get("n", 0)): str(f.get("on", "neither")).lower() for f in data.get("frames", [])}
        out += [got.get(n, "neither") if got.get(n) in ("left", "right", "both", "neither") else "neither"
                for n in range(1, len(batch) + 1)]
    return out


def roam_framing(cam: Source, left_to_right: list[str], length: float, origin: float, client=None,
                 every: float = FRAME_EVERY, classify=classify_frames) -> dict:
    """person -> bool mask (one per HOP on the shared clock) of when the roaming camera is framing them.
    left_to_right names the two people as they sit, left first."""
    if len(left_to_right) != 2:
        raise PanelError("A roaming camera needs the two people's names, left to right.")
    tmp = Path(tempfile.mkdtemp(prefix="theodore-roam-"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", cam.path, "-vf", f"fps=1/{every},scale=384:-2",
                    "-q:v", "5", str(tmp / "%05d.jpg")], check=True, timeout=3600)
    jpgs = sorted(tmp.glob("*.jpg"))
    on = classify(jpgs, client) if jpgs else []
    n = int(np.ceil(length / HOP))
    masks = {p: np.zeros(n, bool) for p in left_to_right}
    side = {"left": left_to_right[0], "right": left_to_right[1]}
    for k, who in enumerate(on):
        if who not in side:
            continue
        first = int(round((k * every + cam.start - origin) / HOP))
        last = int(round(((k + 1) * every + cam.start - origin) / HOP))
        lo, hi = max(first, 0), min(last, n)
        if hi > lo:
            masks[side[who]][lo:hi] = True
    shutil.rmtree(tmp, ignore_errors=True)
    return masks


SETTLE = 1.2   # a camera that follows the speaker has arrived on them this long after they start
HOLD = 0.6     # ...and stays on them a moment after they stop


def follow_framing(masks: dict, settle: float = SETTLE, hold: float = HOLD) -> dict:
    """For a camera whose operator pans to whoever speaks: person -> mask of when it is on them. It is not
    there yet at the start of each turn (it is moving), so short interjections never get that camera."""
    out = {}
    for who, m in masks.items():
        f = np.zeros(len(m), bool)
        for lo, hi in _runs(m):
            start = lo + int(round(settle / HOP))
            if start < hi:
                f[start:min(hi + int(round(hold / HOP)), len(f))] = True
        out[who] = f
    return out


def availability(cams: list, length: float, origin: float) -> dict:
    """camera name -> mask (HOP) of when it has picture on the shared clock."""
    n = int(np.ceil(length / HOP))
    out = {}
    for c in cams:
        m = np.zeros(n, bool)
        for sp in spans(c):
            lo = max(int(round((sp["start"] - origin) / HOP)), 0)
            hi = min(int(round((sp["start"] + sp["duration"] - origin) / HOP)), n)
            if hi > lo:
                m[lo:hi] = True
        out[c.name] = m
    return out


def plan_cut_available(cameras: list, masks: dict, length: float, framing: Optional[dict] = None,
                       available: Optional[dict] = None) -> list[tuple]:
    """plan_cut for a shoot where cameras have gaps: stretches with no picture at all are left out, and where
    only one camera has picture the cut uses that one. Times are relative to the shared-clock origin."""
    if not available:
        return plan_cut(cameras, masks, length, framing)
    n = int(np.ceil(length / HOP))
    any_pic = np.zeros(n, bool)
    for m in available.values():
        any_pic |= m[:n]
    out: list[tuple] = []
    for lo, hi in _runs(any_pic):
        if (hi - lo) * HOP < MIN_SHOT:
            continue
        sl = slice(lo, hi)
        sub_masks = {p: m[sl] for p, m in masks.items()}
        sub_framing = {p: m[sl] for p, m in framing.items()} if framing else None
        have = [c for c in cameras if available[c.name][sl].any()]
        sub = plan_cut(have, sub_masks, (hi - lo) * HOP, sub_framing)
        out += _repair([(a + lo * HOP, b + lo * HOP, cam, why) for a, b, cam, why in sub], available, cameras)
    return out


def _repair(cuts: list, available: dict, cameras: list) -> list:
    """Wherever a cut's camera has no picture, show one that does."""
    order = [c.name for c in sorted(cameras, key=lambda c: {"wide": 0, "roam": 1}.get(c.role, 2))]
    fixed = []
    for a, b, cam, why in cuts:
        ia, ib = int(round(a / HOP)), int(round(b / HOP))
        mask = available[cam][ia:ib]
        if mask.all():
            fixed.append((a, b, cam, why))
            continue
        pos = ia
        for on, group in itertools.groupby(mask):
            n = len(list(group))
            lo, hi = pos, pos + n
            pos = hi
            if on:
                fixed.append((lo * HOP, hi * HOP, cam, why))
                continue
            sub = next((c for c in order if c != cam and available[c][lo:hi].all()), None)
            if sub:
                fixed.append((lo * HOP, hi * HOP, sub, f"{cam} has no picture here"))
    merged: list = []
    for a, b, cam, why in fixed:
        if merged and merged[-1][2] == cam and abs(merged[-1][1] - a) < 1e-6:
            merged[-1] = (merged[-1][0], b, cam, merged[-1][3])
        elif b - a > 1e-6:
            merged.append((a, b, cam, why))
    return merged


def plan_from_sources(cams: list, lavs: list, *, mapping: Optional[dict] = None, wide: Optional[str] = None,
                      roam: Optional[str] = None, left_to_right: Optional[list] = None, framer=None,
                      follow: bool = False, notes: Optional[list] = None, size: Optional[tuple] = None) -> PanelPlan:
    """The cut, from cameras and mics whose position on the shared clock is already known (each Source has
    its `start`, or its `parts`)."""
    if not cams:
        raise PanelError("Need at least one camera.")
    every = [sp for s in cams + lavs for sp in spans(s)]
    origin = min(sp["start"] for sp in every)
    length = max(sp["start"] + sp["duration"] for sp in every) - origin
    masks = activity(lavs, length, origin) if lavs else {}
    roam_cam = None
    if roam:
        roam_cam = next((c for c in cams if roam.lower() in c.name.lower()), None)
        if roam_cam is None:
            raise PanelError(f"No camera matches roam={roam!r}.")
        roam_cam.role, roam_cam.person = "roam", None
    found = assign_people(cams, masks, length, origin, mapping, wide)
    wide_cam = next((c for c in cams if c.role == "wide"), cams[0])
    fps = wide_cam.info["fps"]
    plan = PanelPlan(cams, lavs, fps, length, origin, notes=list(notes or []) + found, size=size)
    plan.speech = {p: [(a * HOP + origin, b * HOP + origin) for a, b in _runs(m)] for p, m in masks.items()}
    avail = availability(cams, length, origin)
    framing = None
    if roam_cam is not None:
        if follow:
            framing = follow_framing(masks)
        else:
            if not left_to_right or any(p not in masks for p in left_to_right):
                raise PanelError("Roaming camera: give left_to_right as the two people's names (as the lavs are named).")
            framing = (framer or roam_framing)(roam_cam, left_to_right, length, origin)
        framing = {p: m & avail[roam_cam.name][:len(m)] for p, m in framing.items()}
        talk = sum(int(m.sum()) for m in masks.values()) or 1
        on_them = sum(int((masks[p] & framing[p]).sum()) for p in framing if p in masks)
        plan.notes.append(f"The roaming camera ({roam_cam.name}) is on the speaker for about {on_them * 100 // talk}% of the "
                          "talking; the wide covers the rest.")
    cuts = plan_cut_available(cams, masks, length, framing, avail)
    plan.cuts = [(a + origin, b + origin, cam, why) for a, b, cam, why in cuts if b - a > 1 / float(fps)]
    for s in cams + lavs:
        if s.confidence < 0.2:
            plan.notes.append(f"Sync for {s.name} is uncertain (confidence {s.confidence:.2f}) -- check it.")
    return plan


def build_plan(camera_paths: list[str], lav_paths: list[str], *, people: Optional[list[str]] = None,
               mapping: Optional[dict] = None, wide: Optional[str] = None, roam: Optional[str] = None,
               left_to_right: Optional[list[str]] = None, framer=None, follow: bool = False) -> PanelPlan:
    cams = [Source(Path(p).stem, p, "camera", _probe(p)) for p in camera_paths]
    lavs = [Source(Path(p).stem, p, "lav", _probe(p)) for p in lav_paths]
    if not cams:
        raise PanelError("Need at least one camera.")
    for i, lav in enumerate(lavs):
        lav.person = (people[i] if people and i < len(people) else
                      lav.name.replace("LAV_", "").replace("lav_", "").replace("_", " ").strip() or f"Speaker {i + 1}")
    sync_notes = sync(cams, lavs)
    return plan_from_sources(cams, lavs, mapping=mapping, wide=wide, roam=roam, left_to_right=left_to_right,
                             framer=framer, follow=follow, notes=sync_notes)


# ---------------------------------------------------------------------------
# FCPXML: a real multicam clip + a cut timeline, imported into Resolve
# ---------------------------------------------------------------------------

def _frames(t: float, fps: Fraction) -> Fraction:
    return Fraction(round(Fraction(t) * fps)) / fps


def _ts(v: Fraction) -> str:
    v = Fraction(v)
    return f"{v.numerator}/{v.denominator}s"


def to_fcpxml(plan: PanelPlan, name: str) -> str:
    fps = plan.fps
    cam0 = plan.cameras[0].info
    size = plan.size or (cam0["width"] or 1920, cam0["height"] or 1080)
    root = ET.Element("fcpxml", {"version": "1.10"})
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", {"id": "r0", "frameDuration": _ts(1 / fps), "width": str(size[0]), "height": str(size[1])})
    formats: dict = {}

    def fmt(info) -> str:
        key = (Fraction(info["fps"]), info["width"], info["height"])
        if key == (Fraction(fps), size[0], size[1]):
            return "r0"
        if key not in formats:
            formats[key] = f"f{len(formats) + 1}"
            ET.SubElement(res, "format", {"id": formats[key], "frameDuration": _ts(1 / Fraction(info["fps"])),
                                          "width": str(info["width"] or size[0]), "height": str(info["height"] or size[1])})
        return formats[key]

    refs: dict = {}      # (source name, part index) -> asset id
    n = 0
    for src in plan.cameras + plan.lavs:
        for k, sp in enumerate(spans(src)):
            n += 1
            rid = f"a{n}"
            refs[(src.name, k)] = rid
            info = sp["info"]
            attrs = {"id": rid, "name": sp.get("name") or src.name, "start": _ts(Fraction(sp.get("asset_tc", sp["tc"]))),
                     "duration": _ts(_frames(sp.get("asset_duration", sp["duration"]),
                                             Fraction(info["fps"]) if info["has_video"] else fps)),
                     "hasAudio": "1" if info["has_audio"] else "0", "audioSources": "1",
                     "audioChannels": str(info["channels"]), "audioRate": str(info["sample_rate"])}
            if info["has_video"]:
                attrs.update({"hasVideo": "1", "format": fmt(info)})
            asset = ET.SubElement(res, "asset", attrs)
            ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": Path(sp["path"]).resolve().as_uri()})
    media = ET.SubElement(res, "media", {"id": "mc1", "name": f"{name} Multicam"})
    mc = ET.SubElement(media, "multicam", {"format": "r0", "tcStart": "0s", "tcFormat": "NDF"})
    angle_ids = {}
    for k, cam in enumerate(plan.cameras, 1):
        aid = f"angle{k}"
        angle_ids[cam.name] = aid
        angle = ET.SubElement(mc, "mc-angle", {"name": cam.name, "angleID": aid})
        cursor = Fraction(0)
        for j, sp in enumerate(spans(cam)):
            off = _frames(sp["start"] - plan.origin, fps)
            if off > cursor:
                ET.SubElement(angle, "gap", {"name": "Gap", "offset": _ts(cursor), "start": "0s", "duration": _ts(off - cursor)})
            dur = _frames(sp["duration"], fps)
            el = ET.SubElement(angle, "asset-clip", {"ref": refs[(cam.name, j)], "offset": _ts(off), "name": sp.get("name") or cam.name,
                                                      "start": _ts(Fraction(sp["tc"])), "duration": _ts(dur),
                                                      "format": fmt(sp["info"]), "tcFormat": "NDF"})
            # A camera whose own rate differs from the timeline's (24 fps in a 23.976 timeline) must be conformed to
            # REAL time, as Resolve does when you drop it on a timeline: it is played faster by the ratio of the
            # rates, so frame n still lands at n/rate seconds. Played frame for frame it falls ~0.1% behind the sound
            # (1.7 s over 28 minutes).
            ratio = Fraction(sp["info"]["fps"]) / Fraction(fps)
            # Only for a FASTER camera: a 23.976 RED in a 24 fps multicam with this timeMap came in as one
            # frozen frame (Creative Couch, 2026-11-13); without it Resolve plays it in real time, frame exact.
            if sp["info"]["has_video"] and float(ratio) - 1 > 1e-4:
                tm = ET.SubElement(el, "timeMap")
                tc0 = Fraction(sp["tc"])
                ET.SubElement(tm, "timept", {"time": "0s", "value": _ts(tc0), "interp": "linear"})
                ET.SubElement(tm, "timept", {"time": _ts(dur), "value": _ts(tc0 + dur * ratio), "interp": "linear"})
            cursor = off + dur
    lib = ET.SubElement(root, "library")
    event = ET.SubElement(lib, "event", {"name": name})
    project = ET.SubElement(event, "project", {"name": name})
    seq = ET.SubElement(project, "sequence", {"format": "r0", "duration": "0s", "tcStart": "3600/1s",
                                              "tcFormat": "NDF", "audioLayout": "stereo", "audioRate": "48k"})
    spine = ET.SubElement(seq, "spine")
    rec = Fraction(3600)
    placed = []   # dict(el, a, b, rec, local, dur) per cut
    for a, b, cam, why in plan.cuts:
        local = _frames(a - plan.origin, fps)
        dur = _frames(b - plan.origin, fps) - local
        if dur <= 0:
            continue
        clip = ET.SubElement(spine, "mc-clip", {"ref": "mc1", "offset": _ts(rec), "name": f"{cam} -- {why}",
                                                "start": _ts(local), "duration": _ts(dur)})
        ET.SubElement(clip, "mc-source", {"angleID": angle_ids[cam], "srcEnable": "video"})
        placed.append({"el": clip, "a": a, "b": b, "rec": rec - 3600, "local": local, "dur": dur})
        rec += dur
    seq.set("duration", _ts(rec - 3600))
    # Sections: runs of cuts with nothing skipped between them. A mic is attached per section, at the exact
    # place its sound belongs, so a stretch left out of the picture never pushes the sound out of sync.
    sections, cur = [], []
    for c in placed:
        if cur and c["a"] - cur[-1]["b"] > 1.5 / float(fps):
            sections.append(cur)
            cur = []
        cur.append(c)
    if cur:
        sections.append(cur)
    for lane, lav in enumerate(plan.lavs if plan.attach_lavs else [], 1):
        for j, sp in enumerate(spans(lav)):
            for sec in sections:
                lo, hi = max(sp["start"], sec[0]["a"]), min(sp["start"] + sp["duration"], sec[-1]["b"])
                if hi - lo < 2 / float(fps):
                    continue
                host = next((c for c in sec if c["a"] <= lo < c["b"]), None)
                if host is None:
                    continue
                # Everything in the multicam's own frame grid: neighbouring clips of one mic then share an exact
                # boundary (a one-frame overlap would make Resolve push the next clip onto another track).
                f_lo, f_hi = _frames(lo - plan.origin, fps), _frames(hi - plan.origin, fps)
                if f_hi <= f_lo:
                    continue
                ET.SubElement(host["el"], "asset-clip", {
                    "ref": refs[(lav.name, j)], "lane": str(-lane), "offset": _ts(f_lo),
                    "name": f"{lav.person} lav", "start": _ts(Fraction(sp["tc"]) + _frames(lo - sp["start"], fps)),
                    "duration": _ts(f_hi - f_lo), "audioRole": "dialogue"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode")


def import_to_resolve(h, plan: PanelPlan, name: str, import_sources: bool = True) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="theodore-panel-")) / "panel.fcpxml"
    tmp.write_text(to_fcpxml(plan, name), encoding="utf-8")
    pool = h.project.GetMediaPool()
    tl = pool.ImportTimelineFromFile(str(tmp), {"timelineName": name, "importSourceClips": bool(import_sources)})
    if not tl:
        raise PanelError(f"Resolve would not import the multicam FCPXML ({tmp}).")
    h.project.SetCurrentTimeline(tl)
    saved = save_speakers(plan, tl.GetName())
    return {"timeline": tl.GetName(), "fcpxml": str(tmp), "speakers_saved": str(saved) if saved else None}


def save_speakers(plan: PanelPlan, timeline_name: str) -> Optional[Path]:
    """Who talks when, in seconds from the start of the cut timeline, kept so the story engine can tell
    which person says each line. Nothing to save without lavs."""
    if not plan.speech or not plan.cuts:
        return None
    from theodore import config
    t0 = plan.cuts[0][0]
    out = Path(config.DATA_ROOT) / "multicam" / (re.sub(r"[^\w. -]+", "_", timeline_name) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"timeline": timeline_name, "speech": {
        p: [[round(a - t0, 3), round(b - t0, 3)] for a, b in segs] for p, segs in plan.speech.items()}}))
    return out


def load_speakers(timeline_name: str) -> Optional[dict]:
    from theodore import config
    f = Path(config.DATA_ROOT) / "multicam" / (re.sub(r"[^\w. -]+", "_", timeline_name) + ".json")
    try:
        return json.loads(f.read_text())["speech"]
    except (OSError, ValueError, KeyError):
        return None


def speaker_at(speech: dict, start: float, end: float) -> Optional[str]:
    """The person who talks for most of [start, end) seconds, or None if nobody clearly does."""
    best, best_len = None, 0.0
    for person, segs in speech.items():
        total = sum(max(0.0, min(end, b) - max(start, a)) for a, b in segs)
        if total > best_len:
            best, best_len = person, total
    return best if best_len >= 0.4 * (end - start) else None


def summary(plan: PanelPlan) -> dict:
    counts = {}
    for a, b, cam, _ in plan.cuts:
        counts[cam] = round(counts.get(cam, 0) + (b - a), 1)
    return {
        "sync": {s.name: {"starts_at": round(s.start - plan.origin, 3), "confidence": round(s.confidence, 2)}
                 for s in plan.cameras + plan.lavs},
        "cameras": {c.name: {"role": c.role, "on": c.person} for c in plan.cameras},
        "cuts": len(plan.cuts), "seconds_per_angle": counts,
        "speaking_seconds": {p: round(sum(b - a for a, b in segs), 1) for p, segs in plan.speech.items()},
        "notes": plan.notes,
    }


def render_preview(plan: PanelPlan, out_path, *, height: int = 576) -> Path:
    """A watchable video of the plan's cuts straight from the source files (no Resolve): the chosen angle
    for every shot, the lavs mixed underneath. Low-res on purpose: it is for reviewing."""
    work = Path(tempfile.mkdtemp(prefix="theodore-preview-"))
    paths = {c.name: c for c in plan.cameras}
    t0, t1 = plan.cuts[0][0], plan.cuts[-1][1]
    width = int(height * 16 / 9) // 2 * 2
    lines = []
    for i, (a, b, cam, _why) in enumerate(plan.cuts):
        seg = work / f"{i:04d}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, a - paths[cam].start):.3f}",
                        "-t", f"{b - a:.3f}", "-i", paths[cam].path, "-an", "-vf",
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=25",
                        "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(seg)],
                       check=True, timeout=600)
        lines.append(f"file '{seg}'")
    (work / "list.txt").write_text("\n".join(lines))
    video = work / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                    "-c", "copy", str(video)], check=True, timeout=600)
    voices = plan.lavs or [c for c in plan.cameras if c.info["has_audio"]][:1]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not voices:
        shutil.copy(video, out)
        return out
    ins, chains = [], []
    for n, v in enumerate(voices):
        ins += ["-i", v.path]
        lead = t0 - v.start            # >= 0: skip into the file; < 0: the mic started later, so delay it
        chains.append(f"[{n}]atrim=start={max(lead, 0):.3f},asetpts=PTS-STARTPTS,"
                      f"adelay={int(max(-lead, 0) * 1000)}:all=1[v{n}]")
    mix = "".join(f"[v{n}]" for n in range(len(voices)))
    chains.append(f"{mix}amix=inputs={len(voices)}:normalize=0,dynaudnorm,atrim=end={t1 - t0:.3f}[a]")
    audio = work / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(chains), "-map", "[a]",
                    "-ar", "44100", str(audio)], check=True, timeout=1800)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio), "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out)],
                   check=True, timeout=600)
    shutil.rmtree(work, ignore_errors=True)
    return out


def multicam_from_bins(h, *, camera_bin=None, cameras=None, lav_bin=None, lavs=None, people=None,
                       camera_for=None, wide=None, name="Panel Multicam", preview: bool = True, roam=None,
                       left_to_right=None) -> dict:
    """Find the cameras and lavs in the media pool, sync, cut and import: the whole multicam build."""
    from theodore.edit import interview
    try:
        cams = interview.find_clips(h, cameras, camera_bin)
        lav_clips = interview.find_clips(h, lavs, lav_bin) if (lavs or lav_bin) else []
    except interview.InterviewError as exc:
        raise PanelError(str(exc))
    # Resolve lists bin clips in its own order; pair people with mics by file name, never by position.
    paths = lambda clips: sorted((c.GetClipProperty("File Path") for c in clips if c.GetClipProperty("File Path")),
                                 key=lambda p: Path(p).name.lower())
    plan = build_plan(paths(cams), paths(lav_clips), people=people, mapping=camera_for, wide=wide, roam=roam,
                      left_to_right=left_to_right)
    made = {**import_to_resolve(h, plan, name), **summary(plan)}
    if preview:
        from theodore import config, share
        try:
            video = render_preview(plan, Path(config.DATA_ROOT) / "previews" / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', name)}.mp4")
            made["preview"] = {"video": str(video), **share.copy_for_phone(video)}
        except Exception as exc:  # noqa: BLE001  the timeline is built; the preview is a bonus
            made["preview"] = {"video": None, "error": f"{type(exc).__name__}: {exc}"}
    return made
