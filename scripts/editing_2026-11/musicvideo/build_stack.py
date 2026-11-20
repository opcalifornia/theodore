"""Plain stacked timeline (no multicam): the song on A1, every take cut into its detected RUNS, each run on its own named video track at its song offset.
Unplaced clips and B-roll go on one track after the song. Usage: build_stack.py runs.json "timeline name" """
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from theodore.resolve.connection import connect
FPS = 23.976
runs = json.load(open(sys.argv[1])); NAME = sys.argv[2]
runs.setdefault("c019", [dict(t0=0.0, t1=9.4, offset=33.875, score=0.2)])      # shorter than one window: confirmed earlier by lyrics + xc
def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)
h = connect(require_timeline=False); r = getattr(h, "resolve", h); pj = r.GetProjectManager().GetCurrentProject(); mp = pj.GetMediaPool(); root = mp.GetRootFolder()
P = {c.GetName(): c for c in walk(root) if c.GetClipProperty("Type") != "Timeline"}
song = [c for n, c in P.items() if n.lower().endswith(".mp3")][0]; cams = {n[5:9].lower(): c for n, c in P.items() if n.startswith("A009_C")}
tb = [s for s in root.GetSubFolderList() if s.GetName() == "Timelines"][0]
tl = mp.CreateEmptyTimeline(NAME); pj.SetCurrentTimeline(tl); st = int(tl.GetStartFrame())
mp.AppendToTimeline([{"mediaPoolItem": song, "recordFrame": st, "mediaType": 2, "trackIndex": 1}])
pieces = [(k, i + 1, x) for k in sorted(runs) for i, x in enumerate(runs[k])]
for _ in range(len(pieces) + 1): tl.AddTrack("video")
bad = []; markers = []
for tr, (k, ri, x) in enumerate(pieces, 1):
    c = cams[k]; n = int(c.GetClipProperty("Frames")); sf = int(round(x["t0"] * FPS)); ef = min(n - 1, int(round(x["t1"] * FPS)) - 1); rec = int(round((x["offset"] + x["t0"]) * FPS))
    if rec < 0: sf += -rec; rec = 0
    got = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": sf, "endFrame": ef, "recordFrame": st + rec, "mediaType": 1, "trackIndex": tr}])
    tl.SetTrackName("video", tr, f"{k.upper()} run{ri}")
    tl.AddMarker(rec, "Green" if x["score"] >= 0.2 else "Yellow", f"{k.upper()} run{ri}", f"offset {x['offset']} s, clip {x['t0']}-{x['t1']} s, match {x['score']}", 1)
    if not got: bad.append((k, ri))
tt = len(pieces) + 1; cur = int(round(202.83 * FPS)) + 48
for k in sorted(cams):
    if k in runs and runs[k]: continue
    c = cams[k]; n = int(c.GetClipProperty("Frames")); b = int(k[1:]) >= 29
    got = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": 0, "endFrame": n - 1, "recordFrame": st + cur, "mediaType": 1, "trackIndex": tt}])
    tl.AddMarker(cur, "Blue" if b else "Orange", k.upper() + (" B-ROLL" if b else " UNPLACED"), "B-roll" if b else "no usable playback audio in this clip", 1); cur += n + 24
    if not got: bad.append((k, 0))
tl.SetTrackName("video", tt, "UNPLACED + B-ROLL")
pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool[NAME]], tb)
print(len(pieces), "runs placed;", "failed:", bad)
