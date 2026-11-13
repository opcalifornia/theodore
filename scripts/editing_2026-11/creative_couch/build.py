from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import _placed_item
h = connect(require_timeline=False); proj = h.project; pool = proj.GetMediaPool()
clips = {}
def walk(f):
    for c in f.GetClipList() or []: clips.setdefault(c.GetName(), c)
    for s in f.GetSubFolderList() or []: walk(s)
walk(pool.GetRootFolder())
NAME = "Synced - RED + Lavs"
tl = pool.CreateEmptyTimeline(NAME); assert tl, "timeline"
proj.SetCurrentTimeline(tl)
T0 = tl.GetStartFrame(); LEAD = 240; R0 = T0 + LEAD
tl.AddTrack("video"); 
while tl.GetTrackCount("audio") < 3: tl.AddTrack("audio", "mono")
for k, i, n in (("video",1,"RED"),("video",2,"Alexa"),("audio",1,"Lav 1 DJI_07"),("audio",2,"Lav 2 DJI_06"),("audio",3,"RED scratch")):
    tl.SetTrackName(k, i, n)
def place(mpi, s, e, rec, track, mt):
    it = _placed_item(pool.AppendToTimeline([{"mediaPoolItem": mpi, "startFrame": s, "endFrame": e, "recordFrame": rec, "trackIndex": track, "mediaType": mt}]))
    print(mpi.GetName(), "track", track, "mt", mt, "->", it and (it.GetStart(), it.GetEnd(), it.GetSourceStartTime()))
    return it
red = clips["R057_C042_0101CF_001.R3D"]
place(red, 0, 46860, R0, 1, 1)
place(red, 0, 46860, R0, 3, 2)
FILE1 = 88604661/48000
for track, off, f1, f2 in ((1, 418.526, "DJI_07_20261112_111627.WAV", "DJI_07_20261112_114713.WAV"),
                           (2, 418.049, "DJI_06_20261112_111627.WAV", "DJI_06_20261112_114715.WAV")):
    src_in = round((off - LEAD/24) * 24)
    rec2 = T0 + round((FILE1 - off) * 24 + LEAD)
    a = place(clips[f1], src_in, src_in + (rec2 - T0), T0, track, 2)
    if a and a.GetEnd() != rec2:
        a.GetTimeline if False else None
        print("file1 end", a.GetEnd(), "want", rec2)
    b = place(clips[f2], 0, int(clips[f2].GetClipProperty("Duration") and 25917339/48000*24), rec2, track, 2)
