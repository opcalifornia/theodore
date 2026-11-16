"""Super Smash proof v3: scene/take names on everything, lav offsets exact to the frame (read back), C013 lav placed by
measured offset. One multicam per shot, angles = takes, the lav inside every take. Nothing is deleted: v2 is renamed and filed.
Usage: build_takes_v3.py '{"5":-2,...}'  measured offsets in frames override the Lined-up ones (video frame = lav frame + d)"""
import os, statistics, sys
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _place_exact
FPS = 23.976
CUES = {4: [10.5, 19.3], 5: [15.5, 31.0, 51.7], 6: [5.2, 20.7], 7: [7.7, 31.2, 58.3], 8: "manual:0,66", 9: [21.2, 45.8],
        10: [10.6, 29.7], 11: [39.3, 52.1, 65.1, 74.9, 95.0, 110.7], 12: [30.1, 44.2, 60.2], 13: [10.8, 40.0, 47.2, 69.2, 79.0], 14: None}
ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
SCENE = {n: f"1{chr(65 + i)}" for i, n in enumerate(ORDER)}      # editor's naming: scene 1, shots A, B, C ... (C004 = 1A); takes are "Scene 1A Take 2"

def starts(n):
    c = CUES[n]
    if c is None: return [0.0]
    if isinstance(c, str): return [float(x) for x in c.split(":")[1].split(",")]
    return [max(0.0, c[0] - 8)] + [x - 4 for x in c[1:]]

def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)

def sub(mp, parent, name):
    for s in parent.GetSubFolderList() or []:
        if s.GetName() == name: return s
    return mp.AddSubFolder(parent, name)

def place_lav(t, mp, W, ls, want, st):
    """Place the lav so its source start is EXACTLY ls (Resolve can land a frame off): read back, correct, retry."""
    adj = 0
    for _ in range(4):
        it = _place_exact(t, mp, {"mediaPoolItem": W, "startFrame": max(0, ls + adj), "endFrame": max(0, ls + adj) + want, "recordFrame": st, "mediaType": 2, "trackIndex": 1}, want)
        if it is None: return None, None
        err = int(it.GetSourceStartFrame()) - ls
        if err == 0: return it, 0
        t.DeleteClips([it], False); adj -= err
    return None, err

def main():
    import json; over = {int(k): int(v) for k, v in json.loads(sys.argv[1]).items()} if len(sys.argv) > 1 else {}
    h = connect(); r = getattr(h, "resolve", h)
    pj = r.GetProjectManager().GetCurrentProject(); mp = pj.GetMediaPool(); root = mp.GetRootFolder()
    assert pj.GetName() == "Super smash commercial 2027"
    tk = sub(mp, root, "Theodore Takes"); media = [s for s in tk.GetSubFolderList() if s.GetName().startswith("Media")][0]
    ang = sub(mp, tk, "Take angles"); mcb = sub(mp, tk, "Multicams"); old = sub(mp, tk, "Superseded v1"); tlb = sub(mp, root, "Timelines"); wk = sub(mp, tlb, "Working")
    # file v2 away (rename, never delete)
    for i in range(1, pj.GetTimelineCount() + 1):
        t = pj.GetTimelineByIndex(i); nm = t.GetName()
        if nm == "Theodore takes - Super Smash": t.SetName("Theodore checkpoint takes v2 (lav in takes, unnamed)")
        elif len(nm) == 7 and nm[0] == "C" and nm[4:6] == " T": t.SetName("v2 " + nm)
    for c in list(walk(root)):
        nm = c.GetName()
        if "multicam (" in nm and nm[0] == "C": c.SetClipProperty("Clip Name", "v2 " + nm)
    for c in list(walk(root)):
        nm = c.GetName()
        if nm.startswith("v2 ") or nm.startswith("Theodore checkpoint takes v2"): mp.MoveClips([c], wk if nm.startswith("Theodore checkpoint") else old)
    # the editor's own sync (Lined up) = the offsets
    lined = [pj.GetTimelineByIndex(i) for i in range(1, pj.GetTimelineCount() + 1) if pj.GetTimelineByIndex(i).GetName() == "Lined up"][0]
    vit = {it.GetName()[:9]: it for it in lined.GetItemListInTrack("video", 1)}; d = {}
    for it in lined.GetItemListInTrack("audio", 5):
        n = int(it.GetName()[4:6]); v = vit.get("R054_C%03d" % n)
        if v is not None: d[n] = (it.GetStart() - v.GetStart()) - it.GetSourceStartFrame() + v.GetSourceStartFrame()
    d.update(over)
    print("offsets used", d)
    cl = {c.GetName(): c for c in media.GetClipList()}
    V = {n: [c for k, c in cl.items() if k.startswith(f"R054_C{n:03d}_") or k.endswith(f"C{n:03d} camera")][0] for n in ORDER}
    W = {n: [c for k, c in cl.items() if k.startswith(f"DJI_{n:02d}_") or k.endswith(f"DJI_{n:02d} lav")][0] for n in ORDER}
    # label the media: scene number, camera/lav, and the Scene metadata field
    for n in ORDER:
        s = SCENE[n]
        V[n].SetClipProperty("Clip Name", f"Scene {s} camera (C{n:03d})"); W[n].SetClipProperty("Clip Name", f"Scene {s} lav (DJI_{n:02d})")
        for c in (V[n], W[n]):
            try: c.SetMetadata({"Scene": s})
            except Exception: pass
    out = {}; bad = []
    for n in ORDER:
        s = starts(n); L = int(V[n].GetClipProperty("Frames")); tls = []; dd = d[n]
        for k, a in enumerate(s):
            b = s[k + 1] if k + 1 < len(s) else None
            sf = max(0, int(a * FPS) + dd); ef = (int(b * FPS) + dd - 1) if b is not None else L - 1
            name = f"Scene {SCENE[n]} Take {k+1}"
            t = mp.CreateEmptyTimeline(name); pj.SetCurrentTimeline(t); st = int(t.GetStartFrame())
            mp.AppendToTimeline([{"mediaPoolItem": V[n], "startFrame": sf, "endFrame": ef, "mediaType": 1}])
            want = int(t.GetItemListInTrack("video", 1)[0].GetDuration()); vsrc = int(t.GetItemListInTrack("video", 1)[0].GetSourceStartFrame())
            ls = vsrc - dd
            if ls < 0: ls = 0
            lav, err = place_lav(t, mp, W[n], ls, want, st)
            ok = lav is not None
            if not ok: bad.append(name)
            tls.append((name, vsrc, ls, ok))
        out[n] = tls
    pool = {c.GetName(): c for c in walk(root)}
    for n in ORDER:
        for x in out[n]: mp.MoveClips([pool[x[0]]], ang)
    pool = {c.GetName(): c for c in walk(root)}; final = []
    for n in ORDER:
        names = [x[0] for x in out[n]]
        if len(names) > 1:
            nm = f"Scene {SCENE[n]}"
            print("multicam", nm, bool(mp.CreateMulticamClip([pool[x] for x in names], {"name": nm})))
            pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool[nm]], mcb); final.append((pool[nm], nm, len(names)))
        else:
            final.append((pool[names[0]], names[0], 1))
    tl = mp.CreateEmptyTimeline("Theodore takes - Super Smash"); pj.SetCurrentTimeline(tl); st = int(tl.GetStartFrame()); pos = 0
    for c, nm, nt in final:
        got = mp.AppendToTimeline([c]); dur = int(tl.GetItemListInTrack("video", 1)[-1].GetDuration())
        tl.AddMarker(pos, "Blue" if nt > 1 else "Green", nm.split(" - ")[0] if nt > 1 else nm.split(" (")[0], f"{nm} | {nt} take(s)", 1)
        pos += dur
    pool = {c.GetName(): c for c in walk(root)}
    mp.MoveClips([pool["Theodore takes - Super Smash"]], tlb)
    print("lav placement problems:", bad)
    for k, v in out.items(): print(k, [(a, vs, ls, ok) for a, vs, ls, ok in v])
main()
