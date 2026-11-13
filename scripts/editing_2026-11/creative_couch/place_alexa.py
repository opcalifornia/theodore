from theodore.resolve.connection import connect
h = connect(require_timeline=False); proj = h.project; pool = proj.GetMediaPool()
tl = proj.GetCurrentTimeline(); print(tl.GetName())
assert tl.GetName() == "Synced - RED + Lavs"
clips = {}
def walk(f):
    for c in f.GetClipList() or []: clips.setdefault(c.GetName(), c)
    for s in f.GetSubFolderList() or []: walk(s)
walk(pool.GetRootFolder())
for n, rec in (("A036C001_261112_R6L9.mxf", 86640), ("A037C001_261112_R6L9.mxf", 133546 + 120 - 2412)):
    L = int(clips[n].GetClipProperty("Frames"))
    res = pool.AppendToTimeline([{"mediaPoolItem": clips[n], "startFrame": 0, "endFrame": L, "recordFrame": rec, "trackIndex": 2, "mediaType": 1}])
    print(n, [(x.GetStart(), x.GetEnd()) for x in (res or []) if x])
for k in ("video", "audio"):
    for i in range(1, tl.GetTrackCount(k)+1):
        print(k, i, tl.GetTrackName(k, i), [(x.GetName(), x.GetStart(), x.GetEnd()) for x in tl.GetItemListInTrack(k, i) or []])
h.resolve.GetProjectManager().SaveProject()
