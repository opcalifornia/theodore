"""Build the shot-breakdown overlay cut in Resolve: the screen recording of the (already produced) scene, cut at every
shot, with the Full Frame info overlay on V2 that changes at each cut. Private study material.
Run: ~/theodore/.venv/bin/python jw_overlay_build.py"""
from __future__ import annotations

import json, os, shutil, sys
import xml.etree.ElementTree as ET
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_overlay as O  # noqa: E402
from theodore.resolve import connection  # noqa: E402

JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
OUT = os.path.expanduser("~/Documents/Theodore/Templates/John Wick Home Invasion Template")
PROJECT, TL = "John Wick Home Invasion Template", "M01-L02 Home Invasion - Overlay Cut"
NUM, DEN = 24000, 1001
META = dict(production="John Wick - Home Invasion (study)")


def fr(n):
    t = Fraction(int(n) * DEN, NUM)
    return f"{t.numerator}/{t.denominator}s"


def main():
    shots = json.load(open(f"{JW}/shots_v2.json")); labels = json.load(open(f"{JW}/labels_v2.json"))
    for sub in ("overlays", "recording"):
        os.makedirs(f"{OUT}/{sub}", exist_ok=True)
    video = f"{OUT}/recording/scene_conformed_f00000.mp4"
    if not os.path.exists(video):
        shutil.copy(f"{JW}/scene_conformed2.mp4", video)
    mx = max(s["frames"] for s in shots)
    pngs = []
    for s in shots:
        s["kind"] = "dialogue" if s["f_in"] >= 4254 else "action"
        p = f"{OUT}/overlays/{s['id']}_overlay_f{s['f_in']:05d}.png"
        if not os.path.exists(p):
            O.overlay(s, labels.get(s["id"], {}), META, p, len(shots), mx)
        pngs.append(p); print(s["n"], end=" ", flush=True)
    print()
    root = ET.Element("fcpxml", version="1.10"); res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r0", name="FFVideoFormat1080p2398", frameDuration=f"{DEN}/{NUM}s", width="1920", height="1080")
    ET.SubElement(res, "asset", id="v", name=os.path.basename(video), start="0s", duration=fr(4846), hasVideo="1", format="r0").append(
        ET.Element("media-rep", kind="original-media", src="file://" + video.replace(" ", "%20")))
    for i, p in enumerate(pngs, 1):
        a = ET.SubElement(res, "asset", id=f"o{i}", name=os.path.basename(p), start="0s", duration=fr(24 * 3600), hasVideo="1", format="r0")
        ET.SubElement(a, "media-rep", kind="original-media", src="file://" + p.replace(" ", "%20"))
    total = shots[-1]["f_out"] + 1
    ev = ET.SubElement(ET.SubElement(root, "library"), "event", name=TL)
    seq = ET.SubElement(ET.SubElement(ev, "project", name=TL), "sequence", format="r0", duration=fr(total), tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(seq, "spine")
    for i, s in enumerate(shots, 1):
        c = ET.SubElement(spine, "asset-clip", ref="v", offset=fr(s["f_in"]), name=f"{s['id']} video", start=fr(s["f_in"]), duration=fr(s["frames"]))
        ET.SubElement(c, "asset-clip", ref=f"o{i}", lane="1", offset=fr(s["f_in"]), name=os.path.basename(pngs[i - 1]), start="0s", duration=fr(s["frames"]))
    xml = f"{OUT}/overlay_cut.fcpxml"
    with open(xml, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'); ET.ElementTree(root).write(fh, encoding="utf-8", xml_declaration=False)

    h = connection.connect(require_project=False, require_timeline=False); pm = h.project_manager
    if pm.GetCurrentProject() is None or pm.GetCurrentProject().GetName() != PROJECT:
        if pm.GetCurrentProject() is not None: pm.SaveProject()
        assert pm.LoadProject(PROJECT), "could not open " + PROJECT
    project = pm.GetCurrentProject(); pool = project.GetMediaPool(); rootf = pool.GetRootFolder()
    ch = lambda parent, nm: next((f for f in parent.GetSubFolderList() or [] if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)
    lesson = ch(rootf, "M01-L02 Home Invasion"); acam = ch(lesson, "A-Cam"); gfx = ch(lesson, "Graphics"); ovl = ch(gfx, "Overlays"); tl_bin = ch(rootf, "Timelines")
    work = ch(tl_bin, "Working")
    have = {c.GetName() for c in acam.GetClipList() or []}
    pool.SetCurrentFolder(acam)
    if os.path.basename(video) not in have: print("video imported:", len(pool.ImportMedia([video]) or []))
    have = {c.GetName() for c in ovl.GetClipList() or []}
    new = [p for p in pngs if os.path.basename(p) not in have]
    pool.SetCurrentFolder(ovl)
    if new: print("overlays imported:", len(pool.ImportMedia(new) or []), "of", len(new))
    for i in range(1, int(project.GetTimelineCount()) + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == TL:
            taken = {project.GetTimelineByIndex(j).GetName() for j in range(1, int(project.GetTimelineCount()) + 1)}
            k = 1
            while f"Theodore checkpoint - {TL} v{k}" in taken: k += 1
            cp = f"Theodore checkpoint - {TL} v{k}"; t.SetName(cp)
            for c in tl_bin.GetClipList() or []:
                if c.GetName() == cp: pool.MoveClips([c], work)
            break
    pool.SetCurrentFolder(tl_bin)
    tl = pool.ImportTimelineFromFile(xml, {"timelineName": TL, "importSourceClips": False, "sourceClipsFolders": [acam, ovl]})
    assert tl and project.SetCurrentTimeline(tl); tl = project.GetCurrentTimeline()
    tl.SetTrackName("video", 1, "V1 SCREEN RECORDING (cut per shot)")
    while int(tl.GetTrackCount("video")) < 2: tl.AddTrack("video")
    tl.SetTrackName("video", 2, "V2 SHOT INFO OVERLAY")
    s0 = int(tl.GetStartFrame())
    for s in shots:
        lab = labels.get(s["id"], {})
        tl.AddMarker(s["f_in"], "Blue" if s["f_in"] >= 4254 else "Sky", f"{s['id']} {lab.get('size', '')} · {lab.get('angle', '')}"[:120],
                     f"SIZE {lab.get('size')} | ANGLE {lab.get('angle')} | MOVEMENT {lab.get('movement')} | LENS {lab.get('lens')} | LIGHT {lab.get('light')} | "
                     f"ACTION {lab.get('action')} | {s['frames']} frames. Read from start+end frames; sound not verified.", s["frames"], s["id"])
    rep = {}
    for tr in (1, 2):
        items = sorted((it.GetStart() - s0, it.GetDuration()) for it in tl.GetItemListInTrack("video", tr) if it.GetType() != "transition")
        t, gaps = 0, []
        for st_, du in items:
            if st_ != t: gaps.append((t, st_))
            t = st_ + du
        rep[tr] = (len(items), t, len(gaps))
    print("V1 (clips, ends, gaps):", rep[1], "| V2:", rep[2], "| expected", len(shots), "clips each, end", total, "| markers", len(tl.GetMarkers()))
    project.SetCurrentTimeline(tl); h.resolve.OpenPage("edit"); print("saved:", pm.SaveProject())


if __name__ == "__main__":
    main()
