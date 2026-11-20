"""Super Smash proof v2: one multicam per shot; every take is its own mini-timeline = picture + THE LAV for that take
(placed with the editor's own sync offsets from 'Lined up'), so choosing an angle brings its lav. Nothing is deleted:
the v1 build (camera audio only) is renamed and filed in Theodore Takes/Superseded."""
import os, statistics, sys
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _place_exact
sys.path.insert(0, os.path.dirname(__file__))
FPS = 23.976
CUES = {4: [10.5, 19.3], 5: [15.5, 31.0, 51.7], 6: [5.2, 20.7], 7: [7.7, 31.2, 58.3], 8: "manual:0,66", 9: [21.2, 45.8],
        10: [10.6, 29.7], 11: [39.3, 52.1, 65.1, 74.9, 95.0, 110.7], 12: [30.1, 44.2, 60.2], 13: [10.8, 40.0, 47.2, 69.2, 79.0], 14: None}
ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

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

def main():
    h = connect(); r = getattr(h, "resolve", h)
    pj = r.GetProjectManager().GetCurrentProject(); mp = pj.GetMediaPool(); root = mp.GetRootFolder()
    assert pj.GetName() == "Super smash commercial 2027"
    tk = sub(mp, root, "Theodore Takes"); media = [s for s in tk.GetSubFolderList() if s.GetName().startswith("Media")][0]
    ang = sub(mp, tk, "Take angles"); mcb = sub(mp, tk, "Multicams"); old = sub(mp, tk, "Superseded v1"); tlb = sub(mp, root, "Timelines"); wk = sub(mp, tlb, "Working")
    # 1. file v1 away (rename, never delete)
    for i in range(1, pj.GetTimelineCount() + 1):
        t = pj.GetTimelineByIndex(i); nm = t.GetName()
        if nm == "Theodore takes - Super Smash": t.SetName("Theodore checkpoint takes v1 (camera audio only)")
        elif len(nm) == 7 and nm[0] == "C" and nm[4:6] == " T": t.SetName("v1 " + nm)
    pool = list(walk(root))
    for c in pool:
        nm = c.GetName()
        if "multicam (" in nm and not nm.startswith("v1 "): c.SetClipProperty("Clip Name", "v1 " + nm)
    pool = list(walk(root))
    mv = [c for c in pool if c.GetName().startswith("v1 ") or c.GetName().startswith("Theodore checkpoint takes v1")]
    for c in mv:
        mp.MoveClips([c], wk if c.GetName().startswith("Theodore checkpoint") else old)
    # 2. offsets from the editor's sync
    lined = [pj.GetTimelineByIndex(i) for i in range(1, pj.GetTimelineCount() + 1) if pj.GetTimelineByIndex(i).GetName() == "Lined up"][0]
    vit = {it.GetName()[:9]: it for it in lined.GetItemListInTrack("video", 1)}; d = {}
    for it in lined.GetItemListInTrack("audio", 5):
        n = int(it.GetName()[4:6]); v = vit.get("R054_C%03d" % n)
        if v is not None: d[n] = (it.GetStart() - v.GetStart()) - it.GetSourceStartFrame() + v.GetSourceStartFrame()
    print("offsets", d)
    cl = {c.GetName(): c for c in media.GetClipList()}
    V = {n: [c for k, c in cl.items() if k.startswith(f"R054_C{n:03d}_")][0] for n in ORDER + [3]}
    W = {n: [c for k, c in cl.items() if k.startswith(f"DJI_{n:02d}_")][0] for n in ORDER}
    # 3. take mini-timelines (picture + lav)
    out = {}
    for n in ORDER:
        s = starts(n); L = int(V[n].GetClipProperty("Frames")); tls = []
        dd = d.get(n)
        for k, a in enumerate(s):
            b = s[k + 1] if k + 1 < len(s) else None
            sf = max(0, int(a * FPS) + (dd or 0)); ef = (int(b * FPS) + (dd or 0) - 1) if b is not None else L - 1
            name = f"C{n:03d} T{k+1}"
            t = mp.CreateEmptyTimeline(name); pj.SetCurrentTimeline(t); st = int(t.GetStartFrame())
            if dd is None:   # C013: the waveform-synced clip already carries its lav
                got = mp.AppendToTimeline([{"mediaPoolItem": V[n], "startFrame": sf, "endFrame": ef}]); lav = "synced"
            else:
                got = mp.AppendToTimeline([{"mediaPoolItem": V[n], "startFrame": sf, "endFrame": ef, "mediaType": 1}])
                want = int(t.GetItemListInTrack("video", 1)[0].GetDuration())
                ls = max(0, sf - dd)
                lavit = _place_exact(t, mp, {"mediaPoolItem": W[n], "startFrame": ls, "endFrame": ls + want, "recordFrame": st, "mediaType": 2, "trackIndex": 1}, want)
                lav = bool(lavit)
            tls.append((name, sf, ef, bool(got), lav))
        out[n] = tls
    pool = {c.GetName(): c for c in walk(root)}
    for n in ORDER:
        for x in out[n]: mp.MoveClips([pool[x[0]]], ang)
    pool = {c.GetName(): c for c in walk(root)}
    final = []
    for n in ORDER:
        names = [x[0] for x in out[n]]
        if len(names) > 1:
            nm = f"C{n:03d} multicam ({len(names)} takes)"
            print("multicam", n, bool(mp.CreateMulticamClip([pool[x] for x in names], {"name": nm})))
            pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool[nm]], mcb); final.append(pool[nm])
        else:
            final.append(pool[names[0]])
    tl = mp.CreateEmptyTimeline("Theodore takes - Super Smash"); pj.SetCurrentTimeline(tl)
    for c in final: print("append", c.GetName(), bool(mp.AppendToTimeline([c])))
    pool = {c.GetName(): c for c in walk(root)}
    mp.MoveClips([pool["Theodore takes - Super Smash"]], tlb)
    for k, v in out.items(): print(k, [(a, b, c, e, f) for a, b, c, e, f in v])
main()
