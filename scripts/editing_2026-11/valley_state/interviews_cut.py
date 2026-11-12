"""Valley State Prison Mini Doc - Interviews Cut: a copy of the full day timeline
where every interview is cut down to its answers (no questions, crew talk, ums or
stalls). Everything else is copied frame for frame.
  - Eli's two interviews reuse the approved pieces from "Eli Full v7".
  - the other interviews come from interview_pieces.json (interviews_cut_pieces.py).
  - the second angle sits on V2/A2 under every piece (A2 off); the visible angle
    flips at every cut. Unused angles are never deleted, only disabled.
usage: interviews_cut.py <dir with interview_pieces.json>"""
import json, os, sys
from theodore.resolve.connection import connect
from theodore.resolve.timeline_ops import duplicate_for_edit, _placed_item
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from interviews_cut_blocks import DROP, REUSE_ELI

V = sys.argv[1]
NAME = "Valley State Prison Mini Doc - Interviews Cut"
WHO = {"C0523 ii.MP4": "Joe Wilson (Shriners)", "C0525 II.MP4": "Jeffrey Burke", "A7Siii0491.MP4": "Norman Williams",
       "C0519 ii.MP4": "Daniel Henson", "C0520 ii.MP4": "Noah Duenas", "C0521 ii.MP4": "Lester Polk",
       "C0522 ii.MP4": "On death row since 1981", "A7Siii0506.MP4": "Sean Diaz Garcia Jr.",
       "A7Siii0470.MP4": "Elijah Johnson (first)", "A7Siii0508.MP4": "Elijah Johnson (long)"}
PIECES = json.load(open(f"{V}/interview_pieces.json"))

h = connect(); p = h.project
tls = {p.GetTimelineByIndex(i).GetName(): p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)}
if NAME in tls: p.GetMediaPool().DeleteTimelines([tls[NAME]])
src, eli = tls["Valley State Prison Mini Doc"], tls[REUSE_ELI]
F = float(src.GetSetting("timelineFrameRate"))

def items(tl, k, t): return tl.GetItemListInTrack(k, t) or []
def fps_of(mpi):
    try: return float(mpi.GetClipProperty("FPS"))
    except Exception: return F

# --- read the source timeline (plain data, so nothing goes stale after reconnecting)
b0 = int(src.GetStartFrame())
V1 = [dict(name=it.GetName(), id=it.GetMediaPoolItem().GetUniqueId(), start=int(it.GetStart()) - b0,
           dur=int(it.GetDuration()), left=int(it.GetLeftOffset()), sf=int(it.GetSourceStartFrame()))
      for it in items(src, "video", 1)]
V2 = [dict(name=it.GetName(), id=it.GetMediaPoolItem().GetUniqueId(), start=int(it.GetStart()) - b0,
           end=int(it.GetEnd()) - b0, left=int(it.GetLeftOffset())) for it in items(src, "video", 2)]
e0 = int(eli.GetStartFrame())
ELI = {}
v2e = {int(it.GetStart()): it for it in items(eli, "video", 2)}
for it in items(eli, "video", 1):
    st = int(it.GetStart()); s2 = v2e.get(st)
    ELI.setdefault(it.GetName(), []).append(dict(left=int(it.GetLeftOffset()), dur=int(it.GetDuration()),
        id2=s2.GetMediaPoolItem().GetUniqueId() if s2 else None, left2=int(s2.GetLeftOffset()) if s2 else None,
        on2=bool(s2.GetClipEnabled()) if s2 else False))

p.SetCurrentTimeline(src); h = connect()
tl = duplicate_for_edit(h, NAME, "interviews cut")
tl.DeleteClips([it for k in ("video", "audio") for i in range(1, tl.GetTrackCount(k) + 1) for it in items(tl, k, i)], False)
for m in list((tl.GetMarkers() or {}).keys()): tl.DeleteMarkerAtFrame(m)
mp = h.project.GetMediaPool()
def walk(f, acc):
    for c in f.GetClipList() or []: acc[c.GetUniqueId()] = c
    for s in f.GetSubFolderList() or []: walk(s, acc)
    return acc
POOL = walk(mp.GetRootFolder(), {})
base = int(tl.GetStartFrame())

def place(mpi, sf, L, rec, tr, mt):
    """Place L timeline frames of mpi starting at source frame sf, exactly, or nothing."""
    r = fps_of(mpi) / F; span = int(round(L * r))
    for shift in (0, 1, -1):
        for extra in (1, 0, 2, -1, int(r), -int(r)):
            got = _placed_item(mp.AppendToTimeline([{"mediaPoolItem": mpi, "startFrame": sf + shift,
                  "endFrame": sf + shift + span - 1 + extra, "recordFrame": rec, "trackIndex": tr, "mediaType": mt}]))
            if got is None: continue
            if int(got.GetStart()) == rec and int(got.GetEnd()) == rec + L: return got
            tl.DeleteClips([got], False)
    return None

rec, failed, log, flip = base, [], [], 0
def put_piece(id1, s1, id2, s2, L, show2):
    """s1, s2: source frames of main and second angle (in each clip's own frames)."""
    global rec
    for mt in (1, 2):
        if place(POOL[id1], s1, L, rec, 1, mt) is None: failed.append((POOL[id1].GetName(), rec - base, mt, 1))
    if id2 is not None:
        for mt in (1, 2):
            got = place(POOL[id2], s2, L, rec, 2, mt)
            if got is None: failed.append((POOL[id2].GetName(), rec - base, mt, 2))
            elif mt == 1 and not show2: got.SetClipEnabled(False)
    rec += L

for n, c in enumerate(V1):
    name = c["name"]
    if name in DROP:
        log.append(f"{(rec-base)/F/60:7.2f} min  DROPPED {name} ({c['dur']/F:.1f} s, no interview)"); continue
    if name in ("A7Siii0470.MP4", "A7Siii0508.MP4"):
        tl.AddMarker(rec - base, "Blue", WHO[name], f"{name}: pieces from {REUSE_ELI}", 1)
        t0 = rec
        for pc in ELI.get(name, []):
            put_piece(c["id"], pc["left"], pc["id2"], pc["left2"], pc["dur"], pc["on2"])
        log.append(f"{(t0-base)/F/60:7.2f} min  {WHO[name]}: {c['dur']/F/60:.1f} -> {(rec-t0)/F/60:.1f} min ({len(ELI.get(name, []))} pieces, from Eli Full v7)")
        continue
    if name in PIECES:
        cover = [x for x in V2 if x["start"] < c["start"] + c["dur"] and x["end"] > c["start"]]
        t0, last_blk = rec, None
        for s, e, blk in PIECES[name]:
            L = int(round((e - s) * F)); a = int(round(s * F))
            if blk != last_blk:
                tl.AddMarker(rec - base, "Blue", WHO[name], f"{name} answer at {blk[0]:.1f}-{blk[1]:.1f} s", 1); last_blk = blk
            tpos = c["start"] + (a - c["left"])                      # where this audio sat in the full timeline
            two = next((x for x in cover if x["start"] <= tpos and tpos + L <= x["end"]), None)
            if two is not None:
                m2 = POOL[two["id"]]; s2 = int(round((two["left"] + tpos - two["start"]) / F * fps_of(m2)))
                put_piece(c["id"], a, two["id"], s2, L, flip % 2 == 1)
            else:
                put_piece(c["id"], a, None, None, L, False)
            flip += 1
        log.append(f"{(t0-base)/F/60:7.2f} min  {WHO[name]}: {c['dur']/F/60:.1f} -> {(rec-t0)/F/60:.1f} min ({len(PIECES[name])} pieces)")
        continue
    # anything else: copy it frame for frame, with any second angle under it
    m1 = POOL[c["id"]]; under = [x for x in V2 if x["start"] < c["start"] + c["dur"] and x["end"] > c["start"]]
    for mt in (1, 2):
        if place(m1, c["sf"], c["dur"], rec, 1, mt) is None: failed.append((name, rec - base, mt, 1))
    for x in under:
        off = max(x["start"], c["start"]) - c["start"]; L2 = min(x["end"], c["start"] + c["dur"]) - max(x["start"], c["start"])
        m2 = POOL[x["id"]]; s2 = int(round((x["left"] + max(x["start"], c["start"]) - x["start"]) / F * fps_of(m2)))
        for mt in (1, 2):
            if place(m2, s2, L2, rec + off, 2, mt) is None: failed.append((x["name"], rec - base + off, mt, 2))
    rec += c["dur"]
    if n % 20 == 0: print(f"copied {n+1}/{len(V1)}", flush=True)

tl.SetTrackName("video", 1, "Main angle"); tl.SetTrackName("video", 2, "Second angle")
tl.SetTrackName("audio", 1, "Dialogue / sound"); tl.SetTrackName("audio", 2, "Second angle audio")
tl.SetTrackEnable("audio", 2, False)
gaps = []
for k in ("video", "audio"):
    its = items(tl, k, 1)
    gaps += [(k, int(a.GetEnd()) - base) for a, c in zip(its, its[1:]) if int(c.GetStart()) != int(a.GetEnd())]
    if its and int(its[0].GetStart()) != base: gaps.append((k, 0))
h.project_manager.SaveProject()
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "interviews_cut_log.txt"), "w").write("\n".join(log) + "\n")
print("\n".join(log))
print(f"DONE {NAME}: {(rec-base)/F/60:.1f} min (was {sum(c['dur'] for c in V1)/F/60:.1f}), "
      f"V1 {len(items(tl,'video',1))} items, gaps on V1/A1: {len(gaps)} {gaps[:5]}, failed placements: {len(failed)} {failed[:8]}")
