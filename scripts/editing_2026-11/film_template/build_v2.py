"""Pass 2: Resolve ignores FCPXML audio lanes (it packs connected audio onto the
first free track and drops secondary-storyline music), so import the video
spine + dissolves only, then lay every audio piece on its exact track/frame by
API, link it to its video, and fade/crossfade it. Nothing is deleted except a
trial placement that landed on the wrong frame (_place_exact)."""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
import build_template as T  # noqa: E402
from theodore.resolve import connection  # noqa: E402
from theodore.resolve.timeline_ops import _place_exact  # noqa: E402

media = T.MEDIA
data = json.load(open(os.path.join(media, "plan.json")))
shots, assets, audio = data["plan"]["shots"], data["assets"], data["audio"]

# --- video-only FCPXML -------------------------------------------------------
tree = ET.parse(os.path.join(media, "template.fcpxml"))
root = tree.getroot()
res = root.find("resources")
audio_only = {a.get("id") for a in res.findall("asset") if a.get("hasVideo") == "0"}
for a in res.findall("asset"):
    a.set("hasAudio", "0")
    for k in ("audioSources", "audioChannels", "audioRate"):
        a.attrib.pop(k, None)
spine = root.find(".//sequence/spine")
for clip in spine.findall("asset-clip"):
    clip.set("srcEnable", "video")
    for ch in list(clip):
        if ch.tag == "spine" or (ch.tag == "asset-clip" and ch.get("ref") in audio_only):
            clip.remove(ch)
        elif ch.tag == "asset-clip":
            ch.set("srcEnable", "video")
vo = os.path.join(media, "template_video_only.fcpxml")
with open(vo, "wb") as fh:
    fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
    tree.write(fh, encoding="utf-8", xml_declaration=False)

# --- Resolve -----------------------------------------------------------------
h = connection.connect(require_timeline=False)
project, pm = h.project, h.project_manager
assert project.GetName().startswith(T.PROJECT), project.GetName()
pool = project.GetMediaPool()
rootf = pool.GetRootFolder()


def sub(parent, nm):
    return next(f for f in parent.GetSubFolderList() if f.GetName() == nm)


tl_bin = sub(rootf, "Timelines")
working = sub(tl_bin, "Working")
lesson = sub(rootf, T.LESSON_BIN)
bins = {b: sub(lesson, b) for b in ("A-Cam", "B-Cam", "Audio", "Graphics")}


def ensure(parent, nm):
    return next((f for f in parent.GetSubFolderList() if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)


ref_bin = ensure(lesson, "References")
slate_bin = ensure(bins["Graphics"], "Slates")


def pool_paths(folder):
    return {c.GetClipProperty("File Path") for c in folder.GetClipList() or []}


# Import anything new: program slate, slate stills, reference frames.
for b, folder in bins.items():
    new = [a["path"] for a in assets.values() if a["bin"] == b and a["path"] not in pool_paths(folder)]
    if new:
        pool.SetCurrentFolder(folder)
        print(f"  import into {b}:", len(pool.ImportMedia(new) or []), "/", len(new))
cards = os.path.join(media, "cards")
new = [os.path.join(cards, f) for f in sorted(os.listdir(cards)) if f.endswith(".png")]
new = [p for p in new if p not in pool_paths(slate_bin)]
if new:
    pool.SetCurrentFolder(slate_bin)
    print("  slate stills:", len(pool.ImportMedia(new) or []), "/", len(new))
refs = []
for root_, _d, files in os.walk(T.REFS):
    refs += [os.path.join(root_, f) for f in files if f.lower().endswith(T.IMG_EXT)]
new = [p for p in sorted(refs) if p not in pool_paths(ref_bin)]
if new:
    pool.SetCurrentFolder(ref_bin)
    print("  references:", len(pool.ImportMedia(new) or []), "/", len(new))
if "--refresh" in sys.argv:
    # Slates were rewritten in place: make Resolve re-read them (it shows Media Offline otherwise).
    for f in bins.values():
        for c in f.GetClipList() or []:
            c.ReplaceClip(c.GetClipProperty("File Path"))
    print("refresh only: slates regenerated in place, new media imported; timeline untouched.")
    pm.SaveProject()
    sys.exit()

# Retire the current build as a checkpoint (renamed + moved, never deleted).
label = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--checkpoint=")), "previous build")
for clip in tl_bin.GetClipList() or []:
    if clip.GetClipProperty("Type") == "Timeline" and clip.GetName() == T.TIMELINE:
        for i in range(1, int(project.GetTimelineCount()) + 1):
            t = project.GetTimelineByIndex(i)
            if t.GetName() == T.TIMELINE:
                print("checkpoint:", t.SetName(f"Theodore checkpoint - {label}"))
        print("move checkpoint to Working:", pool.MoveClips([clip], working))

pool.SetCurrentFolder(tl_bin)
tl = pool.ImportTimelineFromFile(vo, {"timelineName": T.TIMELINE, "importSourceClips": False,
                                      "sourceClipsFolders": list(bins.values())})
assert tl, "import failed"
assert project.SetCurrentTimeline(tl) and project.GetCurrentTimeline().GetName() == T.TIMELINE
tl = project.GetCurrentTimeline()
print("timeline:", tl.GetName(), "start TC:", tl.SetStartTimecode(T.START_TC), tl.GetStartTimecode())

for kind, names in (("video", T.TRACKS_V), ("audio", T.TRACKS_A)):
    while int(tl.GetTrackCount(kind)) < len(names):
        tl.AddTrack(kind, "stereo") if kind == "audio" else tl.AddTrack(kind)
    for i, nm in enumerate(names, 1):
        tl.SetTrackName(kind, i, nm)

stray = [(i, it.GetName()) for i in range(1, int(tl.GetTrackCount("audio")) + 1)
         for it in tl.GetItemListInTrack("audio", i) or []]
print("audio items after video-only import (want none):", stray)

# media pool items by name (file name without extension)
mpi = {}
for f in bins.values():
    for c in f.GetClipList() or []:
        mpi[os.path.splitext(c.GetName())[0]] = c

s0 = int(tl.GetStartFrame())
placed, failed = [], []
for track, name, rec, n in audio:
    item = mpi.get(name)
    src = 0 if name.startswith(("SFX", "ROOM TONE")) else T.HANDLE
    info = {"mediaPoolItem": item, "startFrame": src, "endFrame": src + n - 1, "recordFrame": s0 + rec,
            "trackIndex": int(track[1:]), "mediaType": 2}
    got = _place_exact(tl, pool, info, n) if item else None
    (placed if got else failed).append((track, name, rec, n, got))
print(f"audio placed {len(placed)}, failed {len(failed)}:", [f[:4] for f in failed])

# Link: video + camera audio + lav (talking shots), video + nat sound (B-roll).
vid = {}
for i in range(1, int(tl.GetTrackCount("video")) + 1):
    for it in tl.GetItemListInTrack("video", i) or []:
        if it.GetMediaPoolItem():
            vid[it.GetName()[:11]] = it
links = 0
for s in shots:
    group = [vid.get(s["id"])] + [g for _t, nm, _r, _n, g in placed if nm.startswith(s["id"])]
    group = [g for g in group if g]
    if len(group) > 1 and tl.SetClipsLinked(group, True):
        links += 1
print("linked groups:", links)

# Fades: music in/out and crossfades between cues, room tone in/out, nat sound short fades.
fps_note = []
music = [g for t, _nm, _r, _n, g in placed if t == "A5"]
for k, g in enumerate(music):
    fin = 1.0 if k == 0 else T.MUSIC_XFADE / 2
    fout = T.TAIL_FADE if k == len(music) - 1 else T.MUSIC_XFADE / 2
    fps_note.append(g.SetFades({"FadeIn": fin * 24, "FadeOut": fout * 24}))
for t, _nm, _r, _n, g in placed:
    if t == "A6":
        g.SetFades({"FadeIn": 24.0, "FadeOut": T.TAIL_FADE * 24})
    elif t == "A3":
        g.SetFades({"FadeIn": 6.0, "FadeOut": 6.0})
print("music SetFades:", fps_note, "readback:", [m.GetFades() for m in music])

for s in shots:
    color = T.KINDS[s["kind"]][0]
    tl.AddMarker(s["start"], color, s["name"],
                 f"{T.KINDS[s['kind']][2]} | {s['track']} | planned {s['frames'] / T.NOMINAL:g} s",
                 s["frames"], s["id"])
print("markers:", len(tl.GetMarkers()))
print("saved:", pm.SaveProject())
