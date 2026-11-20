"""FULL-LENGTH stacked music-video timeline (editor, 2026-11-18: never cut clips short). Song on A1 starts at SONG_AT so no clip loses its head.
Every clip goes on WHOLE. A take that restarts gets one whole copy per run, each aligned to that run's song offset; a ranged marker shows the in-sync span.
Song on A1 at 0. Every performance RUN (mv_runs.json + C019) on its OWN video track, named after it, at its song offset.
Clips with no playback in their audio (portraits, inserts, false starts) go on one track after the song; B-roll on the next.
Each piece is checked after placing: right track, right record frame, right source in-point. Nothing that fails stays.
Usage: build_v4.py runs.json "timeline name" """
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from theodore.resolve.connection import connect
FPS = 23.976
SONG_AT = 480   # 20 s of room before the song (earliest take starts 18.6 s before it)
runs = {k: v for k, v in json.load(open(sys.argv[1])).items() if v}
runs["c019"] = [dict(t0=0.0, t1=9.4, offset=33.875, score=0.233)]          # lyrics + xc agree
NAME = sys.argv[2]
NOSYNC = {"c001": "false start, group by the lake", "c003": "group, camera upside down", "c004": "group, camera upside down",
          "c015": "portrait at tree", "c016": "portrait at tree", "c017": "portrait at tree", "c018": "portrait at tree",
          "c026": "hand on wood insert", "c027": "group by the lake, chatter", "c028": "walk, called action"}

def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)

h = connect(require_timeline=False); r = getattr(h, "resolve", h); pj = r.GetProjectManager().GetCurrentProject(); mp = pj.GetMediaPool(); root = mp.GetRootFolder()
P = {c.GetName(): c for c in walk(root) if c.GetClipProperty("Type") != "Timeline"}
song = [c for n, c in P.items() if n.lower().endswith(".mp3")][0]
cams = {n[5:9].lower(): c for n, c in P.items() if n.startswith("A009_C") and n.endswith(".R3D")}
tb = [s for s in root.GetSubFolderList() if s.GetName() == "Timelines"][0]
tl = mp.CreateEmptyTimeline(NAME)
if not tl or not pj.SetCurrentTimeline(tl) or pj.GetCurrentTimeline().GetName() != NAME: sys.exit("could not open the new timeline")
st = int(tl.GetStartFrame())
mp.AppendToTimeline([{"mediaPoolItem": song, "recordFrame": st + SONG_AT, "mediaType": 2, "trackIndex": 1}])
songitem = tl.GetItemListInTrack("audio", 1)[0]; assert int(songitem.GetStart()) - st == SONG_AT
songlen = SONG_AT + int(songitem.GetDuration())

pieces = [(k, i + 1, x) for k in sorted(runs) for i, x in enumerate(runs[k])]
need = len(pieces) + 2
while tl.GetTrackCount("video") < need: tl.AddTrack("video")

def where(item):
    u = item.GetUniqueId()
    for t in range(1, tl.GetTrackCount("video") + 1):
        if any(i.GetUniqueId() == u for i in tl.GetItemListInTrack("video", t) or []): return t
    return None

def place(c, sf, ef, rec, tr):
    got = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": sf, "endFrame": ef, "recordFrame": st + rec, "mediaType": 1, "trackIndex": tr}])
    it = next((i for i in (got or []) if i and i.GetDuration()), None)
    if it is None: return "not placed"
    t = where(it); s = int(it.GetStart()) - st; lo = int(it.GetLeftOffset())
    if t != tr or s != rec or lo != sf:
        tl.DeleteClips([it], False); return f"landed V{t} at {s} in {lo} (wanted V{tr} at {rec} in {sf})"
    return None

report = []
for tr, (k, ri, x) in enumerate(pieces, 1):
    c = cams[k]; n = int(c.GetClipProperty("Frames")); rec = SONG_AT + int(round(x["offset"] * FPS))
    assert rec >= 0, k
    label = f"{k.upper()} sync{ri}" if len(runs[k]) > 1 else k.upper()
    tl.SetTrackName("video", tr, label)
    err = place(c, 0, n - 1, rec, tr)
    strong = x["score"] >= 0.2; a = int(round(x["t0"] * FPS)); b = min(n, int(round(x["t1"] * FPS)))
    tl.AddMarker(rec + a, "Green" if strong else "Yellow", label + " IN SYNC", f"whole clip; in sync for clip {x['t0']}-{x['t1']} s (song offset {x['offset']} s, audio match {x['score']})" + ("" if strong else " weaker: check lips"), max(1, b - a))
    report.append((label, rec, n, x["score"], err))

def lay(keys, tr, title, color, note):
    cur = songlen + 48; tl.SetTrackName("video", tr, title); out = []
    for k in keys:
        c = cams[k]; n = int(c.GetClipProperty("Frames"))
        err = place(c, 0, n - 1, cur, tr); tl.AddMarker(cur, color, k.upper() + " " + title.split()[0], note(k), 1); out.append((k.upper(), cur, n, err)); cur += n + 24
    return out
ns = lay(sorted(NOSYNC), len(pieces) + 1, "NO PLAYBACK (not synced)", "Orange", lambda k: NOSYNC[k] + ": no song in its audio, place by eye")
br = lay([k for k in sorted(cams) if int(k[1:]) >= 29], len(pieces) + 2, "B-ROLL", "Blue", lambda k: "B-roll")

# read back: every placed item, gaps under the song
pool = {c.GetName(): c for c in walk(root)}; mp.MoveClips([pool[NAME]], tb)
cover = [0] * songlen
for f in range(SONG_AT): cover[f] = 99
for (k, ri, x) in pieces:
    a = SONG_AT + int(round((x["offset"] + x["t0"]) * FPS)); b = SONG_AT + int(round((x["offset"] + x["t1"]) * FPS))
    for f in range(max(SONG_AT, a), min(songlen, b)): cover[f] += 1
holes, f = [], 0
while f < songlen:
    if cover[f] == 0:
        g = f
        while g < songlen and cover[g] == 0: g += 1
        holes.append((round((f - SONG_AT) / FPS, 2), round((g - SONG_AT) / FPS, 2))); f = g
    else: f += 1
json.dump(dict(runs=report, nosync=ns, broll=br, holes=holes, min_angles=min(cover[SONG_AT:]), songlen=songlen), sys.stdout, indent=1)
