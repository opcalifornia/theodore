"""Super Smash proof: one multicam per shot, angles = takes, lav synced INTO each take so the audio follows the angle.
Fresh imports (originals and the editor's timelines are never touched). Everything is filed in bins; nothing deleted."""
import os, statistics, sys, time
from theodore.resolve.connection import connect

FPS = 23.976
BASE = os.path.expanduser("~/Desktop/R054_0101EJ.RDM/")
# lav-time cue (s) of each take's "action"; None = single take. 08 and 13 are rougher (flagged in the report).
CUES = {4: [10.5, 19.3], 5: [15.5, 31.0, 51.7], 6: [5.2, 20.7], 7: [7.7, 31.2, 58.3], 8: "manual:0,66",
        9: [21.2, 45.8], 10: [10.6, 29.7], 11: [39.3, 52.1, 65.1, 74.9, 95.0, 110.7], 12: [30.1, 44.2, 60.2],
        13: [10.8, 40.0, 47.2, 69.2, 79.0], 14: None}
ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
REEL = {n: None for n in range(1, 20)}

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
    assert pj.GetName() == "Super smash commercial 2027", pj.GetName()
    # lav offset per shot from the editor's own sync in "Lined up" (video frame = lav frame + d)
    lined = [pj.GetTimelineByIndex(i) for i in range(1, pj.GetTimelineCount() + 1) if pj.GetTimelineByIndex(i).GetName() == "Lined up"][0]
    vit = {it.GetName()[:9]: it for it in lined.GetItemListInTrack("video", 1)}
    d = {}
    for it in lined.GetItemListInTrack("audio", 5):
        n = int(it.GetName()[4:6]); v = vit.get("R054_C%03d" % n)
        if v is not None: d[n] = (it.GetStart() - v.GetStart()) - it.GetSourceStartFrame() + v.GetSourceStartFrame()
    dm = int(statistics.median(d.values())); print("lav offsets (frames):", d, "median", dm)
    tk = sub(mp, root, "Theodore Takes"); media = sub(mp, tk, "Media (fresh, synced)"); ang = sub(mp, tk, "Take angles"); mc = sub(mp, tk, "Multicams")
    tlb = sub(mp, root, "Timelines")
    paths = []
    for n in ORDER:
        paths += [BASE + f"R054_C{n:03d}_" ]
    # find files
    vids, lavs = {}, {}
    for n in ORDER + [3]:
        for dp, dn, fn in os.walk(BASE):
            for f in fn:
                if f.startswith(f"R054_C{n:03d}_") and f.endswith("_001.R3D"): vids[n] = os.path.join(dp, f)
    for f in os.listdir(BASE):
        if f.startswith("DJI_") and f.endswith(".WAV"): lavs[int(f[4:6])] = BASE + f
    mp.SetCurrentFolder(media)
    items = mp.ImportMedia([vids[n] for n in ORDER + [3]] + [lavs[n] for n in ORDER if n in lavs]) or []
    print("imported", len(items))
    byname = {c.GetName(): c for c in media.GetClipList() or []}
    V = {n: [c for k, c in byname.items() if k.startswith(f"R054_C{n:03d}_")][0] for n in ORDER + [3]}
    W = {n: [c for k, c in byname.items() if k.startswith(f"DJI_{n:02d}_")][0] for n in ORDER if n in lavs}
    for n in ORDER:
        ok = mp.AutoSyncAudio([V[n], W[n]], {}); print("sync", n, ok)
    for _ in range(40):
        pend = [n for n in ORDER if not V[n].GetClipProperty("Synced Audio")]
        if not pend: break
        time.sleep(3)
    print("not synced:", [n for n in ORDER if not V[n].GetClipProperty("Synced Audio")])
    # one-clip timeline per take, then a multicam per shot
    out = {}
    for n in ORDER:
        s = starts(n); L = int(V[n].GetClipProperty("Frames")); tls = []
        for k, a in enumerate(s):
            b = s[k + 1] if k + 1 < len(s) else None
            sf = max(0, int(a * FPS) + d.get(n, dm)); ef = (int(b * FPS) + d.get(n, dm) - 1) if b is not None else L - 1
            name = f"C{n:03d} T{k+1}"
            t = mp.CreateEmptyTimeline(name); pj.SetCurrentTimeline(t)
            got = mp.AppendToTimeline([{"mediaPoolItem": V[n], "startFrame": sf, "endFrame": ef}])
            tls.append((name, sf, ef, bool(got)))
        out[n] = tls
    pool = {c.GetName(): c for c in walk(root)}
    for n in ORDER:
        for name, *_ in out[n]:
            mp.MoveClips([pool[name]], ang)
    pool = {c.GetName(): c for c in walk(root)}
    final = []
    for n in ORDER:
        names = [x[0] for x in out[n]]
        if len(names) > 1:
            res = mp.CreateMulticamClip([pool[x] for x in names], {"name": f"C{n:03d} multicam ({len(names)} takes)"})
            print("multicam", n, bool(res))
            pool = {c.GetName(): c for c in walk(root)}
            nm = f"C{n:03d} multicam ({len(names)} takes)"; mp.MoveClips([pool[nm]], mc); final.append(pool[nm])
        else:
            final.append(V[n])
    final.append(V[3])
    tl = mp.CreateEmptyTimeline("Theodore takes - Super Smash"); pj.SetCurrentTimeline(tl)
    for c in final: print("append", c.GetName(), bool(mp.AppendToTimeline([c])))
    pool = {c.GetName(): c for c in walk(root)}
    mp.MoveClips([pool["Theodore takes - Super Smash"]], tlb)
    print("done")
    for k, v in out.items(): print(k, [(a, b, c) for a, b, c, e in v])

main()
