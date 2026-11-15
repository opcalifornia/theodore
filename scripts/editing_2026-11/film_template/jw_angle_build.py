"""Angle-change overlay cut: V1 = the screen recording cut at every shot, V2 = one overlay per ANGLE block that changes only
when the angle changes. Also writes angle_log.csv (angle ID, type, times used, shots, in/out). Private study material.
Run: ~/theodore/.venv/bin/python jw_angle_build.py"""
from __future__ import annotations
import csv, json, os, sys
import xml.etree.ElementTree as ET
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_angle as A  # noqa: E402
from theodore.resolve import connection  # noqa: E402

JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
OUT = os.path.expanduser("~/Documents/Theodore/Templates/John Wick Home Invasion Template")
PROJECT, TL = "John Wick Home Invasion Template", "M01-L02 Home Invasion - Angle Overlay Cut v2"
NUM, DEN = 24000, 1001
META = dict(production="John Wick - Home Invasion (study)")


def fr(n):
    t = Fraction(int(n) * DEN, NUM); return f"{t.numerator}/{t.denominator}s"


def main():
    shots = json.load(open(f"{JW}/shots_v4.json")); blocks = json.load(open(f"{JW}/angle_blocks_v4.json")); labels = json.load(open(f"{JW}/labels_v4.json"))
    os.makedirs(f"{OUT}/angle_overlays_v2", exist_ok=True)
    video = f"{OUT}/recording/scene_conformed_f00000.mp4"
    mx = max(b["frames"] for b in blocks)
    pngs = []
    for b in blocks:
        p = f"{OUT}/angle_overlays_v2/{b['id']}_{b['key']}_f{b['f_in']:05d}.png"
        A.render(b, blocks, META, p, mx); pngs.append(p); print(b["id"], end=" ", flush=True)
    print()
    with open(f"{OUT}/angle_log.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["angle_id", "angle_type", "times_used_before", "shots", "in_tc", "out_tc", "frames", "seconds", "lens", "lighting", "movement_first_shot", "action_first_shot"])
        for b in blocks:
            l = b["label"]
            w.writerow([b["id"], b["type_name"], b["before"], f"{b['shots'][0]}..{b['shots'][-1]}", A.tc(b["f_in"]), A.tc(b["f_out"]), b["frames"], round(b["frames"] * DEN / NUM, 2),
                        l.get("lens", ""), l.get("light", ""), l.get("movement", ""), l.get("action", "")])
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
    parent = {}
    for s in shots:
        c = ET.SubElement(spine, "asset-clip", ref="v", offset=fr(s["f_in"]), name=f"{s['id']} video", start=fr(s["f_in"]), duration=fr(s["frames"]))
        parent[s["id"]] = c
    for i, b in enumerate(blocks, 1):
        ET.SubElement(parent[b["shots"][0]], "asset-clip", ref=f"o{i}", lane="1", offset=fr(b["f_in"]), name=os.path.basename(pngs[i - 1]), start="0s", duration=fr(b["frames"]))
    xml = f"{OUT}/angle_overlay_cut_v2.fcpxml"
    with open(xml, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'); ET.ElementTree(root).write(fh, encoding="utf-8", xml_declaration=False)

    h = connection.connect(require_project=False, require_timeline=False); pm = h.project_manager
    if pm.GetCurrentProject() is None or pm.GetCurrentProject().GetName() != PROJECT:
        if pm.GetCurrentProject() is not None: pm.SaveProject()
        assert pm.LoadProject(PROJECT)
    project = pm.GetCurrentProject(); pool = project.GetMediaPool(); rootf = pool.GetRootFolder()
    ch = lambda parent, nm: next((f for f in parent.GetSubFolderList() or [] if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)
    lesson = ch(rootf, "M01-L02 Home Invasion"); acam = ch(lesson, "A-Cam"); gfx = ch(lesson, "Graphics"); ang = ch(gfx, "Angle Overlays v2"); tl_bin = ch(rootf, "Timelines")
    pool.SetCurrentFolder(ang); have = {c.GetName() for c in ang.GetClipList() or []}
    new = [p for p in pngs if os.path.basename(p) not in have]
    if new: print("angle overlays imported:", len(pool.ImportMedia(new) or []), "of", len(new))
    pool.SetCurrentFolder(tl_bin)
    tl = pool.ImportTimelineFromFile(xml, {"timelineName": TL, "importSourceClips": False, "sourceClipsFolders": [acam, ang]})
    assert tl and project.SetCurrentTimeline(tl); tl = project.GetCurrentTimeline()
    tl.SetTrackName("video", 1, "V1 SCREEN RECORDING (cut per shot)")
    while int(tl.GetTrackCount("video")) < 2: tl.AddTrack("video")
    tl.SetTrackName("video", 2, "V2 ANGLE LOG (changes on new angle)")
    fades = 0
    for it in tl.GetItemListInTrack("video", 2):       # soft entrance on every new angle
        if it.GetType() != "transition" and it.SetFades({"FadeIn": 6.0, "FadeOut": 0.0}): fades += 1
    s0 = int(tl.GetStartFrame())
    for b in blocks:
        tl.AddMarker(b["f_in"], "Green" if b["before"] == 0 else "Yellow", f"{b['id']} {'NEW' if b['before'] == 0 else 'RETURN x' + str(b['before'] + 1)} {b['type_name']}"[:120],
                     f"ANGLE {b['id']} {b['type_name']} | shots {b['shots'][0]}..{b['shots'][-1]} | {b['frames']} frames | replicate: {b['tip']}", b["frames"], b["id"])
    rep = {}
    for tr in (1, 2):
        items = sorted((it.GetStart() - s0, it.GetDuration()) for it in tl.GetItemListInTrack("video", tr) if it.GetType() != "transition")
        t, gaps = 0, []
        for st_, du in items:
            if st_ != t: gaps.append((t, st_))
            t = st_ + du
        rep[tr] = (len(items), t, len(gaps))
    print("V1 (clips, ends, gaps):", rep[1], "| V2 (clips, ends, gaps):", rep[2], "| expected V1", len(shots), "V2", len(blocks), "end", total, "| fades", fades, "| markers", len(tl.GetMarkers()))
    project.SetCurrentTimeline(tl); h.resolve.OpenPage("edit"); print("saved:", pm.SaveProject())


if __name__ == "__main__":
    main()
