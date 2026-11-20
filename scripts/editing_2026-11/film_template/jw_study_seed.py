"""Seed the John Wick break-in STUDY project in Resolve with what has actually been seen so far.

Reference stills are private study screenshots (kept out of the student guide and shot-list sheet). Every label says
whether it was verified on screen; times that were not read off the player clock are marked UNVERIFIED.
Source: Movieclips (Fandango) "John Wick (1/10) Movie CLIP - The Break-In (2014) HD", 2:56.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from theodore.resolve import connection  # noqa: E402

SRC = "Movieclips - John Wick (1/10) The Break-In (2014) HD, https://www.youtube.com/watch?v=CdHKXmEn4l8"
REFS = os.path.expanduser("~/Documents/Theodore/Templates/John Wick Study/references")
PROJECT, LESSON, TIMELINE = "John Wick Break-In Study", "M01-L01 Break-In", "M01-L01 Break-In Study"

# name, file, shot id, scene, marker colour, timeline frame (None = bin only), description, verified, annotation
FRAMES = [
    ("REF-A 0m00s exterior wide", "REF-A_0m00s_exterior_wide.png", "M01-L01-S01", "1", "Green", 0,
     "Exterior wide establishing shot, house at dusk",
     "TIME VERIFIED 0:00 (player clock). Shot out-point UNVERIFIED.",
     "SIZE: wide / establishing (whole house in frame). ANGLE: roughly level, looking across a lawn toward the house; "
     "exact camera height UNVERIFIED. MOVEMENT: UNVERIFIED from a single still. LENS FEEL: normal-to-long, trees and "
     "house compress into layers (UNVERIFIED focal length). LIGHT: low-key dusk, cool blue-grey sky with a warm band "
     "at the horizon, house and trees in silhouette, no visible practical light. ACTION: none visible (static "
     "establishing). SOUND: UNVERIFIED (audio not heard; no caption at this frame). Mouse cursor visible near centre "
     "of this screenshot."),
    ("REF-B 0m05s overhead bed", "REF-B_0m05s_overhead_bed.png", "M01-L01-S02", "2", "Blue", 120,
     "Overhead medium shot, man asleep in bed",
     "TIME VERIFIED 0:05 (player clock). In/out UNVERIFIED.",
     "SIZE: medium (whole torso and pillow, bed fills frame). ANGLE: high, close to top-down looking straight at "
     "the bed. MOVEMENT: UNVERIFIED from a still. LENS FEEL: wide-to-normal, slight perspective stretch toward the "
     "edges (UNVERIFIED focal length). LIGHT: low-key, warm bedside lamp at frame right edge, olive/amber tones, "
     "deep shadow on the left bedding. ACTION: man lying on his back in a white t-shirt, eyes closed, mouth open, one "
     "hand resting on his stomach. SOUND: caption on screen reads <sighs> (from YouTube captions; sound itself not "
     "heard). A YouTube settings menu covers the right of the original screenshot; this still is cropped to hide it."),
    ("REF-C close-up pillow", "REF-C_unverified_closeup_pillow.png", "M01-L01-S03?", "3?", "Blue", None,
     "Close-up, man asleep on pillow (timecode UNVERIFIED)",
     "TIME UNVERIFIED: clock was not visible; roughly 10 s into the clip by frame-step count only.",
     "SIZE: close-up / medium close-up (head, shoulder, arm). ANGLE: slightly high, near eye-level with the pillow. "
     "MOVEMENT: UNVERIFIED. LENS FEEL: longer lens, shallow depth of field, foreground arm and bedside table soft "
     "(UNVERIFIED focal length). LIGHT: low-key, soft warm side light on the face, dark background. ACTION: man "
     "asleep on his side, head on pillow, arm out toward a bedside table. SOUND: caption <Dog barks> (YouTube "
     "caption; sound itself not heard)."),
]


def main() -> None:
    h = connection.connect(require_project=False, require_timeline=False)
    pm = h.project_manager
    cur = pm.GetCurrentProject()
    if cur is not None:
        pm.SaveProject()
    names = set(pm.GetProjectListInCurrentFolder() or [])
    name = PROJECT
    n = 2
    while name in names:
        name, n = f"{PROJECT} {n}", n + 1
    project = pm.CreateProject(name)
    assert project, "CreateProject failed (is a dialog open in Resolve?)"
    for k, v in (("timelineResolutionWidth", "1920"), ("timelineResolutionHeight", "1080"),
                 ("timelineFrameRate", "23.976"), ("timelinePlaybackFrameRate", "23.976")):
        project.SetSetting(k, v)
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()

    def child(parent, nm):
        return next((f for f in parent.GetSubFolderList() or [] if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)

    lesson = child(root, LESSON)
    bins = {b: child(lesson, b) for b in ("A-Cam", "B-Cam", "Audio", "Graphics", "References")}
    tl_bin = child(root, "Timelines")
    child(tl_bin, "Working")

    pool.SetCurrentFolder(bins["References"])
    got = pool.ImportMedia([os.path.join(REFS, f[1]) for f in FRAMES]) or []
    print("imported references:", len(got), "of", len(FRAMES))
    by_file = {c.GetClipProperty("File Name"): c for c in got}

    pool.SetCurrentFolder(tl_bin)
    tl = pool.CreateEmptyTimeline(TIMELINE)
    assert tl and project.SetCurrentTimeline(tl)
    tl = project.GetCurrentTimeline()
    while int(tl.GetTrackCount("video")) < 2:
        tl.AddTrack("video")
    tl.SetTrackName("video", 1, "V1 STORY A-CAM")
    tl.SetTrackName("video", 2, "V2 REFERENCE")

    for label, fname, sid, scene, color, frame, desc, status, note in FRAMES:
        c = by_file.get(fname)
        if c is None:
            print("MISSING", fname)
            continue
        c.SetName(f"{sid} {label}")
        for k, v in {"Scene": scene, "Shot": sid, "Description": desc, "Comments": f"{status} {note}",
                     "Keywords": "reference, " + ("verified" if "VERIFIED" in status and "UNVERIFIED" not in status.split(".")[0] else "unverified")}.items():
            c.SetMetadata(k, v)
        c.SetClipColor("Green" if "UNVERIFIED" not in status.split(".")[0] else "Orange")
        c.AddMarker(0, color, f"{sid} {desc}", f"{status} {note}", 1, sid)
        if frame is not None:
            got_item = pool.AppendToTimeline([{"mediaPoolItem": c, "startFrame": 0, "endFrame": 119,
                                               "recordFrame": int(tl.GetStartFrame()) + frame, "trackIndex": 2,
                                               "mediaType": 1}])
            print(sid, "placed on V2:", bool(got_item))
            tl.AddMarker(frame, color, f"{sid} {desc}", f"{status} {note} | Marker length is a placeholder (5 s) "
                         "until the full scan gives the real in/out. Source: " + SRC, 120, sid)
    print("timeline markers:", len(tl.GetMarkers()))
    project.SetCurrentTimeline(tl)
    h.resolve.OpenPage("edit")
    print("saved:", pm.SaveProject(), "| project:", project.GetName())


if __name__ == "__main__":
    main()
