"""Music video multicam: every performance take is an angle on the SONG's time axis (song at 0 in each angle, take at its measured offset),
the song itself is each angle's audio, so choosing an angle can never put the audio out of sync."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from theodore.resolve.connection import connect
FPS = 23.976
OFFS = {"C002": -3.081, "C005": 3.173, "C006": -1.342, "C007": -1.059, "C008": -14.391, "C009": 53.679, "C010": 60.987,
        "C011": 21.613, "C013": 115.197, "C014": -18.622, "C019": 33.875, "C021": 36.107, "C025": 140.996}
def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)
def sub(mp, parent, name):
    for s in parent.GetSubFolderList() or []:
        if s.GetName() == name: return s
    return mp.AddSubFolder(parent, name)
h = connect(require_timeline=False); r = getattr(h, "resolve", h); pj = r.GetProjectManager().GetCurrentProject(); mp = pj.GetMediaPool(); root = mp.GetRootFolder()
P = {c.GetName(): c for c in walk(root) if c.GetClipProperty("Type") != "Timeline"}
song = [c for n, c in P.items() if n.lower().endswith(".mp3")][0]
cams = {n[5:9]: c for n, c in P.items() if n.startswith("A009_C")}
tb = sub(mp, root, "Timelines"); wk = sub(mp, tb, "Working"); ang = sub(mp, sub(mp, root, "Theodore MV"), "Take angles"); mcb = sub(mp, sub(mp, root, "Theodore MV"), "Multicam")
names = []
for k in sorted(OFFS):
    c = cams[k]; off = OFFS[k]; n = int(c.GetClipProperty("Frames")); o = int(round(off * FPS))
    t = mp.CreateEmptyTimeline(f"MV angle A009_{k}"); pj.SetCurrentTimeline(t); st = int(t.GetStartFrame())
    mp.AppendToTimeline([{"mediaPoolItem": song, "recordFrame": st, "mediaType": 2, "trackIndex": 1}])
    got = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": max(0, -o), "endFrame": n - 1, "recordFrame": st + max(0, o), "mediaType": 1, "trackIndex": 1}])
    v = t.GetItemListInTrack("video", 1)[0]; a = t.GetItemListInTrack("audio", 1)[0]
    print(k, "offset", off, "video at", int(v.GetStart() - st), "src", int(v.GetSourceStartFrame()), "dur", int(v.GetDuration()), "| song audio at", int(a.GetStart() - st), "dur", int(a.GetDuration()), bool(got))
    names.append(f"MV angle A009_{k}")
pool = {c.GetName(): c for c in walk(root)}
for nm in names: mp.MoveClips([pool[nm]], ang)
pool = {c.GetName(): c for c in walk(root)}
mc = mp.CreateMulticamClip([pool[n] for n in names], {"name": "Music video multicam (song-synced)"}); print("multicam", bool(mc))
pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool["Music video multicam (song-synced)"]], mcb)
tl = mp.CreateEmptyTimeline("Music video MULTICAM"); pj.SetCurrentTimeline(tl)
print("append", bool(mp.AppendToTimeline([pool["Music video multicam (song-synced)"]])))
pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool["Music video MULTICAM"]], tb)
it = tl.GetItemListInTrack("video", 1)[0]; print("timeline item", it.GetName(), int(it.GetDuration()), "frames; audio items", len(tl.GetItemListInTrack("audio", 1) or []))
