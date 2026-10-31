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

import os
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
    role: Optional[str] = None   # wide | close


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


def sync(cameras: list[Source], lavs: list[Source], window: float = 240.0) -> None:
    ref = cameras[0]
    ref_audio = _audio(ref.path, window * 2)
    ref.start, ref.confidence = 0.0, 1.0
    for s in cameras[1:] + lavs:
        if not s.info["has_audio"]:
            raise PanelError(f"{s.name} has no audio to sync from.")
        off, conf = offset_seconds(ref_audio, _audio(s.path, window * 2))
        s.start, s.confidence = off, conf


def activity(lavs: list[Source], length: float, origin: float) -> dict[str, np.ndarray]:
    """Per-person talking mask on the shared clock, one value per HOP."""
    frames = int(np.ceil(length / HOP))
    levels = {}
    for s in lavs:
        x = _audio(s.path)
        hop = int(HOP * SR)
        rms = np.sqrt(np.add.reduceat(x[: len(x) // hop * hop] ** 2, np.arange(0, len(x) // hop * hop, hop)) / hop)
        db = 20 * np.log10(rms + 1e-6)
        out = np.full(frames, -120.0)
        first = int(round((s.start - origin) / HOP))
        lo, hi = max(first, 0), min(first + len(db), frames)
        if hi > lo:
            out[lo:hi] = db[lo - first: hi - first]
        levels[s.person or s.name] = out
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


def plan_cut(cameras: list[Source], masks: dict[str, np.ndarray], length: float) -> list[tuple]:
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
    # Long answers: a 2.5 s wide break in the middle, at the quietest moment.
    out = []
    for a, b, cam, reason in segs:
        dur = (b - a) * HOP
        if cam != wide and dur > LONG_ANSWER:
            mid = (a + b) // 2
            br = int(2.5 / HOP)
            out += [(a * HOP, mid * HOP, cam, reason), (mid * HOP, (mid + br) * HOP, wide, "wide break in a long answer"),
                    ((mid + br) * HOP, b * HOP, cam, reason)]
        else:
            out.append((a * HOP, b * HOP, cam, reason))
    return out


def build_plan(camera_paths: list[str], lav_paths: list[str], *, people: Optional[list[str]] = None,
               mapping: Optional[dict] = None, wide: Optional[str] = None) -> PanelPlan:
    cams = [Source(Path(p).stem, p, "camera", _probe(p)) for p in camera_paths]
    lavs = [Source(Path(p).stem, p, "lav", _probe(p)) for p in lav_paths]
    if not cams:
        raise PanelError("Need at least one camera.")
    for i, lav in enumerate(lavs):
        lav.person = (people[i] if people and i < len(people) else
                      lav.name.replace("LAV_", "").replace("lav_", "").replace("_", " ").strip() or f"Speaker {i + 1}")
    sync(cams, lavs)
    origin = min(s.start for s in cams + lavs)
    end = max(s.start + s.info["duration"] for s in cams)
    start_all = max(s.start for s in cams)          # every camera rolling from here
    length = end - origin
    masks = activity(lavs, length, origin) if lavs else {}
    notes = assign_people(cams, masks, length, origin, mapping, wide)
    fps = cams[0].info["fps"]
    plan = PanelPlan(cams, lavs, fps, length, origin, notes=notes)
    plan.speech = {p: [(a * HOP + origin, b * HOP + origin) for a, b in _runs(m)] for p, m in masks.items()}
    cuts = plan_cut(cams, masks, length)
    lo = start_all - origin
    hi = min(s.start + s.info["duration"] for s in cams) - origin
    clipped = [(max(a, lo) + origin, min(b, hi) + origin, cam, why) for a, b, cam, why in cuts
               if min(b, hi) - max(a, lo) > 1 / float(fps)]
    merged = []
    for a, b, cam, why in clipped:   # same angle twice in a row is one shot
        if merged and merged[-1][2] == cam:
            merged[-1] = (merged[-1][0], b, cam, merged[-1][3])
        else:
            merged.append((a, b, cam, why))
    plan.cuts = merged
    for s in cams + lavs:
        if s.confidence < 0.2:
            plan.notes.append(f"Sync for {s.name} is uncertain (confidence {s.confidence:.2f}) -- check it.")
    return plan


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
    root = ET.Element("fcpxml", {"version": "1.10"})
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", {"id": "r0", "frameDuration": _ts(1 / fps), "width": str(cam0["width"] or 1920),
                                  "height": str(cam0["height"] or 1080)})
    refs = {}
    for n, s in enumerate(plan.cameras + plan.lavs, 1):
        rid = f"a{n}"
        refs[s.name] = rid
        attrs = {"id": rid, "name": s.name, "start": "0s", "duration": _ts(_frames(s.info["duration"], fps)),
                 "hasAudio": "1" if s.info["has_audio"] else "0", "audioSources": "1",
                 "audioChannels": str(s.info["channels"]), "audioRate": str(s.info["sample_rate"])}
        if s.info["has_video"]:
            attrs.update({"hasVideo": "1", "format": "r0"})
        asset = ET.SubElement(res, "asset", attrs)
        ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": Path(s.path).resolve().as_uri()})
    media = ET.SubElement(res, "media", {"id": "mc1", "name": f"{name} Multicam"})
    mc = ET.SubElement(media, "multicam", {"format": "r0", "tcStart": "0s", "tcFormat": "NDF"})
    angle_ids = {}
    for n, cam in enumerate(plan.cameras, 1):
        aid = f"angle{n}"
        angle_ids[cam.name] = aid
        angle = ET.SubElement(mc, "mc-angle", {"name": cam.name, "angleID": aid})
        off = _frames(cam.start - plan.origin, fps)
        if off > 0:
            ET.SubElement(angle, "gap", {"name": "Gap", "offset": "0s", "start": "0s", "duration": _ts(off)})
        ET.SubElement(angle, "asset-clip", {"ref": refs[cam.name], "offset": _ts(off), "name": cam.name,
                                             "start": "0s", "duration": _ts(_frames(cam.info["duration"], fps)),
                                             "format": "r0", "tcFormat": "NDF"})
    lib = ET.SubElement(root, "library")
    event = ET.SubElement(lib, "event", {"name": name})
    project = ET.SubElement(event, "project", {"name": name})
    total = _frames(plan.cuts[-1][1] - plan.cuts[0][0], fps) if plan.cuts else Fraction(0)
    seq = ET.SubElement(project, "sequence", {"format": "r0", "duration": _ts(total), "tcStart": "3600/1s",
                                              "tcFormat": "NDF", "audioLayout": "stereo", "audioRate": "48k"})
    spine = ET.SubElement(seq, "spine")
    t0 = plan.cuts[0][0] if plan.cuts else plan.origin
    rec = Fraction(3600)
    placed = []   # (element, timeline position from the cut's start, local start, duration)
    for a, b, cam, why in plan.cuts:
        start = _frames(a - plan.origin, fps)
        dur = _frames(b - plan.origin, fps) - start
        if dur <= 0:
            continue
        clip = ET.SubElement(spine, "mc-clip", {"ref": "mc1", "offset": _ts(rec), "name": f"{cam} -- {why}",
                                                "start": _ts(start), "duration": _ts(dur)})
        ET.SubElement(clip, "mc-source", {"angleID": angle_ids[cam], "srcEnable": "video"})
        placed.append((clip, rec - 3600, start, dur))
        rec += dur
    # Every lav on its own track under the cut, from the moment it was
    # recording: attached to whichever cut is playing then, at that cut's
    # local time, so it stays in sync even if it started after the cut does.
    for lane, lav in enumerate(plan.lavs, 1):
        when = _frames(max(lav.start, t0) - t0, fps)                  # on the cut timeline
        src_in = _frames(max(t0 - lav.start, 0), fps)                 # into the lav file
        length = min(total - when, _frames(lav.info["duration"], fps) - src_in)
        if length <= 0:
            continue
        host = next((c for c in placed if c[1] <= when < c[1] + c[3]), placed[-1] if placed else None)
        if host is None:
            continue
        clip, host_pos, host_start, _ = host
        ET.SubElement(clip, "asset-clip", {
            "ref": refs[lav.name], "lane": str(-lane), "offset": _ts(host_start + (when - host_pos)),
            "name": f"{lav.person} lav", "start": _ts(src_in), "duration": _ts(length),
            "audioRole": "dialogue"})
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode")


def import_to_resolve(h, plan: PanelPlan, name: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="theodore-panel-")) / "panel.fcpxml"
    tmp.write_text(to_fcpxml(plan, name), encoding="utf-8")
    pool = h.project.GetMediaPool()
    tl = pool.ImportTimelineFromFile(str(tmp), {"timelineName": name, "importSourceClips": True})
    if not tl:
        raise PanelError(f"Resolve would not import the multicam FCPXML ({tmp}).")
    h.project.SetCurrentTimeline(tl)
    return {"timeline": tl.GetName(), "fcpxml": str(tmp)}


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
