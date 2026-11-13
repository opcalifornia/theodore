import json, sys
from theodore.resolve.connection import connect
h = connect()
tl = h.timeline
print("TL", tl.GetName(), tl.GetStartFrame(), tl.GetStartTimecode(), "audio tracks", tl.GetTrackCount("audio"))
for i in range(1, tl.GetTrackCount("audio")+1):
    print("track", i, tl.GetTrackName("audio", i), tl.GetTrackSubType("audio", i))
out = []
for i in (1,2,3,4):
    for n, it in enumerate(tl.GetItemListInTrack("audio", i) or []):
        mpi = it.GetMediaPoolItem()
        out.append(dict(track=i, n=n, start=it.GetStart(), end=it.GetEnd(), name=it.GetName(),
            lo=it.GetLeftOffset(), ss=it.GetSourceStartTime(), se=it.GetSourceEndTime(),
            ssf=it.GetSourceStartFrame() if hasattr(it,'GetSourceStartFrame') else None,
            path=mpi.GetClipProperty("File Path") if mpi else None))
json.dump(out, open(sys.argv[1], "w"), indent=1)
pool = h.project.GetMediaPool()
def walk(f):
    for c in f.GetClipList() or []:
        p = c.GetClipProperty()
        if any(x in (p.get("File Name") or "") for x in ("DJI", "R3D")):
            print({k: p.get(k) for k in ("File Name","File Path","Start TC","End TC","FPS","Duration","Frames","Sample Rate","Audio Ch","Audio Codec","Bit Depth")})
    for s in f.GetSubFolderList() or []: walk(s)
walk(pool.GetRootFolder())
