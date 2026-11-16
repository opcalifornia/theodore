"""Place the picked lines on a NEW Resolve timeline (never the editor's): butt-joined dialogue, music in swell/duck
sections with 1 s crossfades, quick B-roll for the cold open, the swells and cutaways over the joins.

Every placement is read back; nothing is deleted except a trial placement that landed on the wrong frame."""
from __future__ import annotations

import os

from theodore.resolve.timeline_ops import _place_exact


def _find_item(pool, path):
    def walk(f):
        for c in f.GetClipList() or []:
            if (c.GetClipProperty("File Path") or "") == path: return c
        for s in f.GetSubFolderList() or []:
            r = walk(s)
            if r: return r
    return walk(pool.GetRootFolder())


def _import(pool, path, folder="Mr.H Media"):
    it = _find_item(pool, path)
    if it: return it
    root = pool.GetRootFolder()
    fol = next((f for f in root.GetSubFolderList() or [] if f.GetName() == folder), None) or pool.AddSubFolder(root, folder)
    pool.SetCurrentFolder(fol)
    got = pool.ImportMedia([path]) or []
    return got[0] if got else None


def plan(sel: dict, fps: float, cold_open_s=12.0, swell_s=11.8, outro_s=12.0) -> dict:
    """Frame plan relative to timeline start. A line with "swell_before": true gets a swell (music up, quick B-roll) first."""
    f = lambda s: int(round(s * fps)); cur = f(cold_open_s); places, swells = [], [(0, f(cold_open_s))]
    for i, ln in enumerate(sel["lines"]):
        if ln.get("swell_before"):
            swells.append((cur, cur + f(swell_s))); cur += f(swell_s)
        n = f(ln["end"] - ln["start"]); places.append(dict(i=i, rec=cur, n=n, src=f(ln["start"]), line=ln)); cur += n
    swells.append((cur, cur + f(outro_s))); total = cur + f(outro_s)
    # music sections: swell = loud, speech between swells = ducked
    secs, pos = [], 0
    for a, b in swells:
        if a > pos: secs.append((pos, a, "duck"))
        secs.append((a, b, "swell")); pos = b
    return dict(places=places, swells=swells, total=total, music=secs)


def build(h, sel: dict, name: str, *, music: str | None = None, broll_bins: list[str] | None = None, duck_db=-16.0, swell_db=0.0,
          dry_run=True, cold_open_s=12.0, swell_s=11.8, outro_s=12.0, broll_s=1.5) -> dict:
    project, pm = h.project, h.project_manager; pool = project.GetMediaPool()
    fps = float(project.GetSetting("timelineFrameRate") or 23.976)
    P = plan(sel, fps, cold_open_s, swell_s, outro_s)
    info = dict(fps=fps, length_s=round(P["total"] / fps, 1), lines=len(P["places"]), swells=len(P["swells"]), music_sections=len(P["music"]), dry_run=dry_run)
    if dry_run: return dict(info, plan=P)
    root = pool.GetRootFolder()
    tlb = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Timelines"), None) or pool.AddSubFolder(root, "Timelines")
    existing = {project.GetTimelineByIndex(i).GetName() for i in range(1, int(project.GetTimelineCount()) + 1)}
    base, n = name, 2
    while name in existing: name, n = f"{base} {n}", n + 1
    pool.SetCurrentFolder(tlb)
    tl = pool.CreateEmptyTimeline(name)
    assert tl and project.SetCurrentTimeline(tl) and project.GetCurrentTimeline().GetName() == name, "could not make/open the new timeline"
    tl = project.GetCurrentTimeline(); T0 = int(tl.GetStartFrame())
    while int(tl.GetTrackCount("audio")) < 3: tl.AddTrack("audio", "stereo")
    while int(tl.GetTrackCount("video")) < 2: tl.AddTrack("video")
    for k, nm in ((1, "DIALOGUE (value lines)"), (2, "MUSIC A"), (3, "MUSIC B")): tl.SetTrackName("audio", k, nm)
    tl.SetTrackName("video", 1, "OPENING / SWELL MONTAGE"); tl.SetTrackName("video", 2, "CUTAWAYS 1.5 s")
    out = dict(info, timeline=name, failed=[], dialogue=[], music=[], broll=0)
    items = {}
    for pl in P["places"]:
        path = pl["line"]["file"]
        if path not in items: items[path] = _import(pool, path)
        it = items[path]
        got = _place_exact(tl, pool, {"mediaPoolItem": it, "startFrame": pl["src"], "endFrame": pl["src"] + pl["n"] - 1,
                                      "recordFrame": T0 + pl["rec"], "trackIndex": 1, "mediaType": 2}, pl["n"]) if it else None
        if got is None: out["failed"].append(("line", pl["i"])); continue
        out["dialogue"].append((pl["i"], round(float(got.GetSourceStartTime()), 2), round(pl["line"]["start"], 2)))
    if music:
        mi = _import(pool, music); X = 12      # 12 frames = 0.5 s each side -> 1 s crossfade
        for k, (a, b, kind) in enumerate(P["music"]):
            a2, b2 = max(0, a - (X // 2 if k else 0)), b + (X // 2 if k < len(P["music"]) - 1 else 0)
            tr = 2 if k % 2 == 0 else 3
            got = _place_exact(tl, pool, {"mediaPoolItem": mi, "startFrame": a2, "endFrame": b2 - 1, "recordFrame": T0 + a2, "trackIndex": tr, "mediaType": 2}, b2 - a2)
            if got is None: out["failed"].append(("music", k)); continue
            db = swell_db if kind == "swell" else duck_db; got.SetProperty("AudioVolume", db)
            got.SetFades({"FadeIn": float(X if k else 0), "FadeOut": float(22 if k == len(P["music"]) - 1 else (X if True else 0))})
            out["music"].append((k, kind, db, round(float(got.GetProperty("AudioVolume")), 1)))
    # B-roll: quick montage for the cold open and every swell (V1), cutaways over every other join (V2)
    clips = []
    for bn in broll_bins or []:
        fol = next((f for f in root.GetSubFolderList() or [] if f.GetName() == bn), None)
        for c in (fol.GetClipList() if fol else []) or []:
            p = c.GetClipProperty("File Path") or ""
            if p and os.path.exists(p) and not p.lower().endswith((".wav", ".mp3")): clips.append(c)
    if clips:
        L = max(1, int(round(broll_s * fps))); idx = 0
        def put(rec, track):
            nonlocal idx
            c = clips[idx % len(clips)]; idx += 1
            fr = int(c.GetClipProperty("Frames") or 0)
            if fr <= L + 2: return False
            st = max(0, min(fr - L - 1, fr // 2 - L // 2))
            got = _place_exact(tl, pool, {"mediaPoolItem": c, "startFrame": st, "endFrame": st + L - 1, "recordFrame": T0 + rec, "trackIndex": track, "mediaType": 1}, L)
            if got is None: out["failed"].append(("broll", rec)); return False
            out["broll"] += 1; return True
        for a, b in P["swells"]:
            for rec in range(a, b - L + 1, L): put(rec, 1)
        step = int(round(2 * broll_s * fps))          # 1.5 s of B-roll, 1.5 s of speaker: about half the talking is covered
        for pl in P["places"]:
            t = pl["rec"] + int(round(0.8 * fps))
            while t + L <= pl["rec"] + pl["n"] - int(round(0.4 * fps)):
                put(t, 2); t += step
    project.SetCurrentTimeline(tl)
    return out
