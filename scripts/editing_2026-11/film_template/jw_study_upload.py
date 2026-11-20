"""Upload the scanned home-invasion study into Resolve: bins, 2 reference stills per shot (START = where the camera
begins, END = where it lands before the cut), an animatic of those stills on V1, and a marker per shot with its real
in/out/duration. Labels come from labels.json (shot id -> dict) and are marked UNVERIFIED unless the entry says
verified. Never deletes anything; re-runnable (creates a new timeline name if the old one exists).

Source (private study copy, not for the student guide): Clip Empire, "John Wick's Peace Shattered Overnight (Home
Invasion Scene)", https://www.youtube.com/watch?v=y5ZG2-KEZOY. Frame times come from the player's own frame
callbacks (23.976 fps), cuts were detected from frame differences and confirmed by eye.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from theodore.resolve import connection  # noqa: E402
from theodore.resolve.timeline_ops import _place_exact  # noqa: E402

JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
STILLS = os.path.expanduser("~/Documents/Theodore/Templates/John Wick Study/home invasion stills")
PROJECT, LESSON, TL_NAME = "John Wick Break-In Study", "M01-L02 Home Invasion", "M01-L02 Home Invasion Study"
FPS = 24000 / 1001
SRC = "Clip Empire - Home Invasion Scene, https://www.youtube.com/watch?v=y5ZG2-KEZOY"


def tc(f: int) -> str:
    s, ff = divmod(int(f), 24)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def still_name(s, k) -> str:
    """Two different numbers in the name (shot and frame) so Resolve does not fuse the files into an image sequence."""
    return f"{s['id']}_{k}_f{(s['f_in'] if k == 'START' else s['f_out']):05d}.jpg"


def animatic_fcpxml(shots, path) -> None:
    """Gapless V1 spine: for each shot its START still for the first half, END still for the second half."""
    import xml.etree.ElementTree as ET
    from fractions import Fraction

    def fr(n):
        t = Fraction(int(n) * 1001, 24000)
        return f"{t.numerator}/{t.denominator}s"
    root = ET.Element("fcpxml", version="1.10")
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r0", name="FFVideoFormat1080p2398", frameDuration="1001/24000s", width="1920", height="1080")
    ids = {}
    for s in shots:
        for k in ("START", "END"):
            nm = still_name(s, k)
            rid = f"a{len(ids) + 1}"
            ids[(s["id"], k)] = (rid, nm)
            a = ET.SubElement(res, "asset", id=rid, name=nm, start="0s", duration=fr(24 * 3600), hasVideo="1", format="r0")
            ET.SubElement(a, "media-rep", kind="original-media", src="file://" + os.path.join(STILLS, nm).replace(" ", "%20"))
    total = sum(s["frames"] for s in shots)
    ev = ET.SubElement(ET.SubElement(root, "library"), "event", name=TL_NAME)
    seq = ET.SubElement(ET.SubElement(ev, "project", name=TL_NAME), "sequence", format="r0", duration=fr(total),
                        tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(seq, "spine")
    for s in shots:
        half = max(1, s["frames"] // 2) if s["frames"] > 1 else 1
        for k, off, ln in (("START", s["f_in"], half), ("END", s["f_in"] + half, s["frames"] - half)):
            if ln <= 0:
                continue
            rid, nm = ids[(s["id"], k)]
            ET.SubElement(spine, "asset-clip", ref=rid, offset=fr(off), name=nm, start="0s", duration=fr(ln))
    with open(path, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        ET.ElementTree(root).write(fh, encoding="utf-8", xml_declaration=False)


def main() -> None:
    shots = json.load(open(os.path.join(JW, "shots.json")))
    labels = json.load(open(os.path.join(JW, "labels.json"))) if os.path.exists(os.path.join(JW, "labels.json")) else {}
    os.makedirs(STILLS, exist_ok=True)
    for s in shots:
        for k in ("START", "END"):
            src = os.path.join(JW, "stills", f"{s['id']}_{k}.jpg")
            dst = os.path.join(STILLS, still_name(s, k))
            if not os.path.exists(dst):
                open(dst, "wb").write(open(src, "rb").read())
    h = connection.connect(require_project=False, require_timeline=False)
    pm = h.project_manager
    if pm.GetCurrentProject() is None or pm.GetCurrentProject().GetName() != PROJECT:
        if pm.GetCurrentProject() is not None:
            pm.SaveProject()
        assert pm.LoadProject(PROJECT), f"could not open {PROJECT}"
    project = pm.GetCurrentProject()
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()

    def child(parent, nm):
        return next((f for f in parent.GetSubFolderList() or [] if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)

    lesson = child(root, LESSON)
    bins = {b: child(lesson, b) for b in ("A-Cam", "B-Cam", "Audio", "Graphics", "References")}
    tl_bin = child(root, "Timelines")
    child(tl_bin, "Working")
    ref = bins["References"]
    # an earlier import fused the files into two image sequences: move those out of the way (never delete)
    old = [c for c in ref.GetClipList() or [] if "[" in c.GetName()]
    if old:
        sup = child(lesson, "_superseded")
        print("moved fused sequence clips aside:", pool.MoveClips(old, sup))
    have = {c.GetName() for c in ref.GetClipList() or []}
    pool.SetCurrentFolder(ref)
    new = [os.path.join(STILLS, still_name(s, k)) for s in shots for k in ("START", "END")
           if still_name(s, k) not in have]
    if new:
        print("imported stills:", len(pool.ImportMedia(new) or []), "of", len(new))
    clips = {}
    for c in ref.GetClipList() or []:
        base = os.path.splitext(c.GetClipProperty("File Name"))[0]
        clips[base] = c

    working = child(tl_bin, "Working")
    for i in range(1, int(project.GetTimelineCount()) + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == TL_NAME:           # retire the previous build as a checkpoint (renamed + moved, not deleted)
            taken = {project.GetTimelineByIndex(j).GetName() for j in range(1, int(project.GetTimelineCount()) + 1)}
            k = 1
            while f"Theodore checkpoint - {TL_NAME} v{k}" in taken:
                k += 1
            cp = f"Theodore checkpoint - {TL_NAME} v{k}"
            print("checkpoint:", t.SetName(cp))
            for c in tl_bin.GetClipList() or []:
                if c.GetName() == cp:
                    pool.MoveClips([c], working)
            break
    pool.SetCurrentFolder(tl_bin)
    xml = os.path.join(JW, "animatic.fcpxml")
    animatic_fcpxml(shots, xml)
    tl = pool.ImportTimelineFromFile(xml, {"timelineName": TL_NAME, "importSourceClips": False,
                                           "sourceClipsFolders": [ref]})
    assert tl and project.SetCurrentTimeline(tl)
    tl = project.GetCurrentTimeline()
    tl.SetTrackName("video", 1, "V1 REFERENCE ANIMATIC (start > end)")
    s0 = int(tl.GetStartFrame())
    placed = 0
    failed = []
    for s in shots:
        lab = labels.get(s["id"], {})
        verified = lab.get("verified", False)
        status = "LABELS VERIFIED BY EYE" if verified else "LABELS PENDING / UNVERIFIED"
        info = (f"IN {tc(s['f_in'])} (frame {s['f_in']})  OUT {tc(s['f_out'])} (frame {s['f_out']})  "
                f"DURATION {s['frames']} frames = {s['frames'] / FPS:.2f} s. ")
        text = " ".join(f"{k.upper()}: {lab[k]}." for k in ("size", "angle", "movement", "lens", "light", "action", "sound") if k in lab)
        for k in ("START", "END"):
            c = clips.get(still_name(s, k)[:-4])
            if c is None:
                continue
            for key, v in {"Scene": "L02", "Shot": s["id"], "Description": lab.get("desc", f"{s['id']} {k} frame"),
                           "Comments": info + status + " " + text, "Keywords": f"reference, {k.lower()}"}.items():
                c.SetMetadata(key, v)
            c.SetClipColor("Green" if verified else "Orange")
        color = lab.get("marker", "Sky")
        tl.AddMarker(s["f_in"], color, f"{s['id']} {lab.get('desc', 'labels pending')}"[:120],
                     info + status + " " + text + " Source: " + SRC, s["frames"], s["id"])
    print("exact placements failed:", failed[:10], len(failed))
    print("clips placed on timeline:", placed, "| markers:", len(tl.GetMarkers()), "| timeline:", tl.GetName())
    project.SetCurrentTimeline(tl)
    h.resolve.OpenPage("edit")
    print("saved:", pm.SaveProject())


if __name__ == "__main__":
    main()
