import json, sys
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
MODE = sys.argv[1]  # plan | test | all
FPS = 24000/1001
def off_frames(clip, f):
    if clip == "C005": return -0.9053*FPS
    return (0.0588 + (f-1700)*(0.0064/22700))*FPS
DJI = {"C005": "DJI_16_20261103_222255.WAV", "C006": "DJI_17_20261103_224128.WAV"}
h = connect(); tl = h.timeline; pool = h.project.GetMediaPool()
assert tl.GetName() == "THEODORE", tl.GetName()
def snap():
    s = {}
    for k in ("video","audio"):
        for i in range(1, tl.GetTrackCount(k)+1):
            s[f"{k}{i}"] = [(it.GetName(), it.GetStart(), it.GetEnd(), it.GetLeftOffset()) for it in tl.GetItemListInTrack(k, i) or []]
    return s
before = snap()
print("locks", {i: tl.GetIsTrackLocked("audio", i) for i in range(1,7)}, "A5 items", len(before["audio5"]))
# per-frame choice: first track among 1..4 whose item there is lav-covered
items = {i: [(it.GetStart(), it.GetEnd(), it.GetName()[5:9], it.GetLeftOffset(), n) for n, it in enumerate(tl.GetItemListInTrack("audio", i) or [])] for i in (1,2,3,4)}
lo_f = min(a for v in items.values() for a,*_ in v); hi_f = max(b for v in items.values() for _,b,*_ in v)
segs = []
for f in range(lo_f, hi_f):
    pick = None
    for t in (1,2,3,4):
        for a,b,clip,lo,n in items[t]:
            if a <= f < b and clip in DJI:
                pick = (t, n, clip, lo + (f - a)); break
        if pick: break
    if not pick: continue
    t,n,clip,src = pick
    if segs and segs[-1]["end"] == f and segs[-1]["clip"] == clip and segs[-1]["src_end"] == src:
        segs[-1]["end"] = f+1; segs[-1]["src_end"] = src+1
    else:
        segs.append(dict(start=f, end=f+1, clip=clip, src=src, src_end=src+1, track=t, n=n))
for s in segs:
    s["dji_in"] = round(s["src"] - 0.5 + off_frames(s["clip"], s["src"]))
print(len(segs), "segments")
for s in segs: print(s)
if MODE == "plan": sys.exit()
mpis = {}
def walk(fo):
    for c in fo.GetClipList() or []:
        if c.GetName() in DJI.values(): mpis[c.GetName()] = c
    for sf in fo.GetSubFolderList() or []: walk(sf)
walk(pool.GetRootFolder())
todo = segs[:1] if MODE == "test" else (segs[1:] if MODE == "rest" else segs)
report = []
for s in todo:
    want = s["end"] - s["start"]
    ok = None
    for shift in (0, 1, -1):
        for extra in (0, -1, 1):
            info = {"mediaPoolItem": mpis[DJI[s["clip"]]], "startFrame": s["dji_in"] + shift,
                    "endFrame": s["dji_in"] + shift + want - 1 + extra, "recordFrame": s["start"],
                    "trackIndex": 5, "mediaType": 2}
            got = _placed_item(pool.AppendToTimeline([info]))
            if got is None: continue
            if int(got.GetStart()) == s["start"] and int(got.GetEnd()) == s["end"]:
                ok = (got, shift); break
            print("  retry", s["start"], got.GetStart(), got.GetEnd(), got.GetLeftOffset())
            tl.DeleteClips([got], False)
        if ok: break
    if ok:
        got, shift = ok
        report.append((s["start"], s["end"], s["clip"], s["dji_in"], got.GetLeftOffset(), shift))
        print("placed", report[-1])
    else:
        print("FAILED", s)
after = snap()
changed = [k for k in before if k != "audio5" and before[k] != after[k]]
print("other tracks changed:", changed, "| A5 now", len(after["audio5"]))
