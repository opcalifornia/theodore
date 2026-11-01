"""A multicam built from a timeline the editor has already lined up in Resolve.

The editor syncs by hand (or I do) on a timeline: the wide on V1, the second camera on V2, a mic per track.
That timeline IS the sync: where each clip sits is where it belongs. This reads it, plans the cut from the
mics (who is talking) and imports a real multicam clip plus the cut timeline, into the Timelines bin.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from theodore.edit import panel


class MulticamError(RuntimeError):
    pass


def _fps(text) -> Fraction:
    v = float(text)
    return Fraction(24000, 1001) if abs(v - 23.976) < 0.01 else Fraction(30000, 1001) if abs(v - 29.97) < 0.01 \
        else Fraction(round(v)) if abs(v - round(v)) < 0.01 else Fraction(v).limit_denominator(1001)


def _file_fps(path) -> Optional[Fraction]:
    """The frame rate written in the file itself (ffprobe), or None when ffprobe can't read that format."""
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60).stdout.strip()
        num, _, den = out.partition("/")
        v = Fraction(int(num), int(den or 1))
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _tc_seconds(tc: str, fps: Fraction) -> Fraction:
    """HH:MM:SS:FF (non-drop) -> seconds: timecode counts whole frames at the nominal rate."""
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    nominal = round(float(fps))
    return Fraction(((h * 60 + m) * 60 + s) * nominal + f) / fps


def read_part(item, base_frame: int, tl_fps: Fraction, video: bool) -> dict:
    mp = item.GetMediaPoolItem()
    if mp is None:
        raise MulticamError(f"{item.GetName()} has no media pool clip.")
    props = mp.GetClipProperty() or {}
    fps = _fps(props.get("FPS") or float(tl_fps))
    if video:
        # Resolve reports a clip at the PROJECT's rate once it is conformed (the Alexa's 24 fps files read as
        # 23.976). The file's own rate is the truth: using the conformed one drifts the picture ~0.1% (1.7 s over
        # 28 minutes) against the sound.
        real = _file_fps(props.get("File Path"))
        if real:
            fps = real
    start = Fraction(item.GetStart() - base_frame) / tl_fps
    dur = Fraction(item.GetEnd() - item.GetStart()) / tl_fps
    offset = Fraction(item.GetLeftOffset() or 0) / fps          # trimmed in: the file's own position
    tc = _tc_seconds(props.get("Start TC") or "00:00:00:00", fps) + offset
    res = (props.get("Resolution") or "0x0").lower().split("x")
    ch = int(props.get("Audio Ch") or 0)
    info = {"duration": float(dur), "has_video": video, "has_audio": ch > 0, "fps": fps,
            "width": int(res[0]) if video else 0, "height": int(res[1]) if video else 0,
            "channels": max(ch, 1), "sample_rate": int(float(props.get("Sample Rate") or 48000))}
    return {"path": props.get("File Path"), "start": float(start), "duration": float(dur), "tc": float(tc),
            "info": info, "name": item.GetName(), "mpi": mp, "left": int(item.GetLeftOffset() or 0),
            "clip_fps": float(fps)}


def sources_from_timeline(tl, *, cameras: dict, mics: dict) -> tuple[list, list]:
    """cameras: {video track number: (name, role)}; mics: {audio track number: person}."""
    base = int(tl.GetStartFrame())
    tl_fps = _fps(tl.GetSetting("timelineFrameRate"))
    cams, lavs = [], []
    for track, (name, role) in cameras.items():
        parts = [read_part(it, base, tl_fps, True) for it in tl.GetItemListInTrack("video", track) or []]
        if not parts:
            raise MulticamError(f"Video track V{track} ({name}) is empty.")
        cams.append(panel.Source(name, parts[0]["path"], "camera", parts[0]["info"], start=parts[0]["start"],
                                 role=role, parts=parts))
    for track, person in mics.items():
        parts = [read_part(it, base, tl_fps, False) for it in tl.GetItemListInTrack("audio", track) or []]
        if not parts:
            raise MulticamError(f"Audio track A{track} ({person}) is empty.")
        lavs.append(panel.Source(f"Lav {person}", parts[0]["path"], "lav", parts[0]["info"], start=parts[0]["start"],
                                 person=person, parts=parts))
    return cams, lavs


def find_timeline(project, name: str):
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl.GetName() == name:
            return tl
    raise MulticamError(f"No timeline named {name!r}.")


def _bin(pool, root, name):
    found = next((f for f in root.GetSubFolderList() or [] if f.GetName() == name), None)
    return found or pool.AddSubFolder(root, name)


def clean_previous(h, name: str) -> list:
    """Remove an earlier attempt: the timeline called `name` and everything this builder put in the Multicam bin."""
    project = h.project
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    gone = []
    for i in range(int(project.GetTimelineCount() or 0), 0, -1):
        tl = project.GetTimelineByIndex(i)
        if tl.GetName() == name:
            gone.append(name)
            pool.DeleteTimelines([tl])
    bin_ = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Multicam"), None)
    if bin_ and (bin_.GetClipList() or []):
        gone += [c.GetName() for c in bin_.GetClipList()]
        pool.DeleteClips(list(bin_.GetClipList()))
    return gone


def _sections(plan) -> list:
    """[(clock start, clock end, position on the new timeline in seconds)] for each stretch of cuts with nothing
    skipped between them."""
    out, pos, cur = [], 0.0, None
    for a, b, _cam, _why in plan.cuts:
        if cur and a - cur[1] <= 1.5 / float(plan.fps):
            cur[1] = b
        else:
            cur = [a, b, pos]
            out.append(cur)
        pos += b - a
    return out


def place_mics(h, tl, plan, mics: dict) -> dict:
    """Each mic's clips onto its own named audio track, at the right place, with Resolve's own clip placement
    (the way the synced timeline was built), so a mic never wanders onto another man's track."""
    pool = h.project.GetMediaPool()
    fps = float(plan.fps)
    base = int(tl.GetStartFrame())
    while int(tl.GetTrackCount("audio") or 0) < len(plan.lavs):
        tl.AddTrack("audio", "mono")
    placed, failed = 0, []
    for track, lav in enumerate(plan.lavs, 1):
        tl.SetTrackName("audio", track, f"Lav {lav.person}")
        for sp in panel.spans(lav):
            for a, b, pos in _sections(plan):
                lo, hi = max(sp["start"], a), min(sp["start"] + sp["duration"], b)
                if hi - lo < 2 / fps:
                    continue
                first = sp["left"] + int(round((lo - sp["start"]) * sp["clip_fps"]))
                last = sp["left"] + int(round((hi - sp["start"]) * sp["clip_fps"]))
                got = pool.AppendToTimeline([{"mediaPoolItem": sp["mpi"], "startFrame": first, "endFrame": last,
                                              "recordFrame": base + int(round((pos + lo - a) * fps)),
                                              "trackIndex": track, "mediaType": 2}])
                if got:
                    placed += 1
                else:
                    failed.append(f"{lav.person} {sp['name']}")
    return {"mic_clips_placed": placed, "mic_clips_failed": failed}


def _import_plan(h, plan, name: str, mics: dict, multicam_name: Optional[str]) -> dict:
    """Import the plan as a multicam clip + timeline into the Timelines bin; extras go to the Multicam bin."""
    project = h.project
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    timelines, multicam = _bin(pool, root, "Timelines"), _bin(pool, root, "Multicam")
    before = {c.GetName() for f in (timelines, multicam, root) for c in f.GetClipList() or []}
    plan.attach_lavs = False        # the mics are placed below, on their own tracks
    pool.SetCurrentFolder(timelines)
    try:
        made = panel.import_to_resolve(h, plan, name, import_sources=True)
    finally:
        pool.SetCurrentFolder(root)
    new_tl = find_timeline(project, made["timeline"])
    stray = [c for f in (timelines, root) for c in f.GetClipList() or []
             if c.GetName() not in before and c.GetName() != made["timeline"]]
    moved = bool(stray) and pool.MoveClips(stray, multicam)
    if multicam_name:
        for c in stray:
            if (c.GetClipProperty("Type") or "").lower().startswith("multi") or " -- " in c.GetName():
                c.SetClipProperty("Clip Name", multicam_name)
    project.SetCurrentTimeline(new_tl)
    new_tl.SetTrackName("video", 1, "Multicam")
    mics_out = place_mics(h, new_tl, plan, mics)
    return {**made, **panel.summary(plan), **mics_out, "moved_to_multicam_bin": [c.GetName() for c in stray] if moved else []}


def build(h, source_timeline: str, name: str, *, cameras: dict, mics: dict, size: Optional[tuple] = None,
          follow: bool = True, multicam_name: Optional[str] = None) -> dict:
    """Plan the CUT from `source_timeline` and import it: multicam clip + cut timeline."""
    tl = find_timeline(h.project, source_timeline)
    cams, lavs = sources_from_timeline(tl, cameras=cameras, mics=mics)
    roam = next((c.name for c in cams if c.role == "roam"), None)
    wide = next((c.name for c in cams if c.role == "wide"), None)
    plan = panel.plan_from_sources(cams, lavs, wide=wide, roam=roam, follow=follow, size=size)
    if not plan.cuts:
        raise MulticamError("The plan has no cuts: check the camera and mic tracks have clips.")
    out = _import_plan(h, plan, name, mics, multicam_name)
    out["cuts_list"] = [(round(a - plan.origin, 2), round(b - plan.origin, 2), cam, why) for a, b, cam, why in plan.cuts]
    return out


def build_uncut(h, source_timeline: str, name: str, *, cameras: dict, mics: dict, size: Optional[tuple] = None,
                multicam_name: Optional[str] = None) -> dict:
    """Just the multicam: every angle and mic lined up as on `source_timeline`, one uncut clip on a timeline,
    ready for the editor to say how to cut it."""
    tl = find_timeline(h.project, source_timeline)
    cams, lavs = sources_from_timeline(tl, cameras=cameras, mics=mics)
    every = [sp for s in cams + lavs for sp in panel.spans(s)]
    origin = min(sp["start"] for sp in every)
    end = max(sp["start"] + sp["duration"] for sp in every)
    wide = next((c for c in cams if c.role == "wide"), cams[0])
    plan = panel.PanelPlan(cams, lavs, Fraction(wide.info["fps"]), end - origin, origin,
                           cuts=[(origin, end, wide.name, "Multicam, uncut")], size=size)
    return _import_plan(h, plan, name, mics, multicam_name)
