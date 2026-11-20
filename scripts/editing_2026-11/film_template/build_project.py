"""Build the Resolve project from the media + FCPXML that build_template.py wrote.
Never deletes or renders. Run: ~/theodore/.venv/bin/python build_project.py"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import build_template as T  # noqa: E402
from theodore.resolve import connection  # noqa: E402

media = T.MEDIA
data = json.load(open(os.path.join(media, "plan.json")))
shots, assets = data["plan"]["shots"], data["assets"]

h = connection.connect(require_project=False, require_timeline=False)
pm, r = h.project_manager, h.resolve
cur = pm.GetCurrentProject()
if cur is not None:
    print("saving current project:", cur.GetName(), pm.SaveProject())

existing = set(pm.GetProjectListInCurrentFolder() or [])
name = T.PROJECT
n = 2
while name in existing:
    name = f"{T.PROJECT} {n}"
    n += 1
project = pm.CreateProject(name)
if not project:
    sys.exit(f"CreateProject({name!r}) returned None - a dialog may be open in Resolve.")
print("created project:", project.GetName())
for k, v in (("timelineResolutionWidth", str(T.W)), ("timelineResolutionHeight", str(T.H)),
             ("timelineFrameRate", "23.976"), ("timelinePlaybackFrameRate", "23.976")):
    print(" ", k, v, project.SetSetting(k, v), project.GetSetting(k))

pool = project.GetMediaPool()
root = pool.GetRootFolder()


def child(parent, nm):
    for f in parent.GetSubFolderList() or []:
        if f.GetName() == nm:
            return f
    return pool.AddSubFolder(parent, nm)


lesson = child(root, T.LESSON_BIN)
bins = {b: child(lesson, b) for b in ("A-Cam", "B-Cam", "Audio", "Graphics")}
tl_bin = child(root, "Timelines")
child(tl_bin, "Working")

for b, folder in bins.items():
    paths = [a["path"] for a in assets.values() if a["bin"] == b]
    pool.SetCurrentFolder(folder)
    got = pool.ImportMedia(paths) or []
    print(f"  {T.LESSON_BIN}/{b}: imported {len(got)}/{len(paths)}")

pool.SetCurrentFolder(tl_bin)
tl = pool.ImportTimelineFromFile(os.path.join(media, "template.fcpxml"),
                                 {"timelineName": T.TIMELINE, "importSourceClips": False,
                                  "sourceClipsFolders": list(bins.values())})
if not tl:
    sys.exit("ImportTimelineFromFile returned None.")
print("timeline:", tl.GetName(), project.SetCurrentTimeline(tl), project.GetCurrentTimeline().GetName())

# Make sure the timeline sits in Timelines, not the Master root.
for clip in root.GetClipList() or []:
    if clip.GetClipProperty("Type") == "Timeline":
        print("  moving timeline to Timelines bin:", pool.MoveClips([clip], tl_bin))

for kind, names in (("video", T.TRACKS_V), ("audio", T.TRACKS_A)):
    while int(tl.GetTrackCount(kind)) < len(names):
        tl.AddTrack(kind, "stereo") if kind == "audio" else tl.AddTrack(kind)
    for i, nm in enumerate(names, 1):
        tl.SetTrackName(kind, i, nm)

start = int(tl.GetStartFrame())
for s in shots:
    color = T.KINDS[s["kind"]][0]
    ok = tl.AddMarker(s["start"], color, s["name"], f"{T.KINDS[s['kind']][2]} | {s['track']} | planned "
                      f"{s['frames'] / T.NOMINAL:g} s", s["frames"], s["id"])
    if not ok:
        print("  marker FAILED", s["id"])
project.SetCurrentTimeline(tl)
print("saved:", pm.SaveProject())
