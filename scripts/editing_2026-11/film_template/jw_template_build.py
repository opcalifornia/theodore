"""John Wick home-invasion pre-production template in the Full Frame skin: every shot's slate has its START still as the
full-frame background and its END still as a thumbnail; placeholder clips sit in order with 1 s handles (pockets for
dissolves). Reads shots.json (+ labels.json) written by the scan. Private study material: the stills are film frames.
Run: ~/theodore/.venv/bin/python jw_template_build.py"""
from __future__ import annotations

import json, os, subprocess, sys
import xml.etree.ElementTree as ET
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import design_fullframe as D  # noqa: E402
from theodore.resolve import connection  # noqa: E402

JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
OUT = os.path.expanduser("~/Documents/Theodore/Templates/John Wick Home Invasion Template")
PROJECT, LESSON, TL = "John Wick Home Invasion Template", "M01-L02 Home Invasion", "M01-L02 Home Invasion"
NUM, DEN, HANDLE = 24000, 1001, 24
META = dict(production="John Wick - Home Invasion (study)", title="M01-L02 Home Invasion", director="-", dp="-",
            producer="-", editor="-", client="-", date="2026-11-14", version="v001 study", language="English", subtitles="None")


def fr(n):
    t = Fraction(int(n) * DEN, NUM)
    return f"{t.numerator}/{t.denominator}s"


def tc(f):
    s, ff = divmod(int(f), 24)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main():
    shots = json.load(open(f"{JW}/shots.json"))
    labels = json.load(open(f"{JW}/labels.json")) if os.path.exists(f"{JW}/labels.json") else {}
    for sub in ("slates", "clips", "refs"):
        os.makedirs(f"{OUT}/{sub}", exist_ok=True)
    max_frames = max(s["frames"] for s in shots)
    assets = []
    for s in shots:
        lab = labels.get(s["id"], {})
        st, en = (f"{JW}/stills/{s['id']}_START.jpg", f"{JW}/stills/{s['id']}_END.jpg")
        for k, p in (("START", st), ("END", en)):
            dst = f"{OUT}/refs/{s['id']}_{k}_f{(s['f_in'] if k == 'START' else s['f_out']):05d}.jpg"
            if not os.path.exists(dst):
                open(dst, "wb").write(open(p, "rb").read())
        n = s["n"]
        shot = dict(id=s["id"], kind="dialogue" if n >= 49 else "action", desc=lab.get("desc", f"Shot {n} - labels pending"),
                    frames=s["frames"], scene=str(n), take="1", roll="A001", camera="A-CAM",
                    sound="SYNC" if n >= 49 else "NAT", where=f"IN {tc(s['f_in'])}  ·  OUT {tc(s['f_out'])}",
                    ref_labels=["START FRAME", "END FRAME"])
        png = f"{OUT}/slates/{s['id']}.png"
        D.camera_slate(shot, META, png, max_frames, [st, en])
        mp4 = f"{OUT}/clips/{s['id']}_f{s['f_in']:05d}.mp4"
        frames = s["frames"] + 2 * HANDLE
        run(["ffmpeg", "-y", "-loop", "1", "-framerate", f"{NUM}/{DEN}", "-i", png, "-frames:v", str(frames), "-c:v", "libx264",
             "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", f"{NUM}/{DEN}", mp4])
        assets.append((s, mp4, frames))
        print(s["id"], end=" ", flush=True)
    print()
    # FCPXML: gapless V1 spine, each clip trimmed to its shot, handles left on both sides
    root = ET.Element("fcpxml", version="1.10"); res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r0", name="FFVideoFormat1080p2398", frameDuration=f"{DEN}/{NUM}s", width="1920", height="1080")
    for i, (s, mp4, frames) in enumerate(assets, 1):
        a = ET.SubElement(res, "asset", id=f"a{i}", name=os.path.basename(mp4), start="0s", duration=fr(frames), hasVideo="1", format="r0")
        ET.SubElement(a, "media-rep", kind="original-media", src="file://" + mp4.replace(" ", "%20"))
    total = shots[-1]["f_out"] + 1
    ev = ET.SubElement(ET.SubElement(root, "library"), "event", name=TL)
    seq = ET.SubElement(ET.SubElement(ev, "project", name=TL), "sequence", format="r0", duration=fr(total), tcStart="0s", tcFormat="NDF")
    spine = ET.SubElement(seq, "spine")
    for i, (s, mp4, frames) in enumerate(assets, 1):
        ET.SubElement(spine, "asset-clip", ref=f"a{i}", offset=fr(s["f_in"]), name=os.path.basename(mp4), start=fr(HANDLE), duration=fr(s["frames"]))
    xml = f"{OUT}/template.fcpxml"
    with open(xml, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'); ET.ElementTree(root).write(fh, encoding="utf-8", xml_declaration=False)

    h = connection.connect(require_project=False, require_timeline=False)
    pm = h.project_manager
    if pm.GetCurrentProject() is not None:
        pm.SaveProject()
    name, k = PROJECT, 2
    while name in (pm.GetProjectListInCurrentFolder() or []):
        name, k = f"{PROJECT} {k}", k + 1
    project = pm.CreateProject(name)
    assert project, "CreateProject failed (dialog open in Resolve?)"
    for key, v in (("timelineResolutionWidth", "1920"), ("timelineResolutionHeight", "1080"), ("timelineFrameRate", "23.976"), ("timelinePlaybackFrameRate", "23.976")):
        project.SetSetting(key, v)
    pool = project.GetMediaPool(); rootf = pool.GetRootFolder()
    ch = lambda parent, nm: next((f for f in parent.GetSubFolderList() or [] if f.GetName() == nm), None) or pool.AddSubFolder(parent, nm)
    lesson = ch(rootf, LESSON)
    bins = {b: ch(lesson, b) for b in ("A-Cam", "B-Cam", "Audio", "Graphics", "References")}
    slates = ch(bins["Graphics"], "Slates"); tl_bin = ch(rootf, "Timelines"); ch(tl_bin, "Working")
    pool.SetCurrentFolder(bins["A-Cam"]); a = pool.ImportMedia([m for _, m, _ in assets]) or []
    pool.SetCurrentFolder(slates); b = pool.ImportMedia([f"{OUT}/slates/{s['id']}.png" for s, _, _ in assets]) or []
    pool.SetCurrentFolder(bins["References"]); c = pool.ImportMedia(sorted(os.path.join(f"{OUT}/refs", f) for f in os.listdir(f"{OUT}/refs"))) or []
    print("imported: placeholders", len(a), "slates", len(b), "references", len(c))
    pool.SetCurrentFolder(tl_bin)
    tl = pool.ImportTimelineFromFile(xml, {"timelineName": TL, "importSourceClips": False, "sourceClipsFolders": [bins["A-Cam"]]})
    assert tl and project.SetCurrentTimeline(tl); tl = project.GetCurrentTimeline()
    tl.SetTrackName("video", 1, "V1 STORY A-CAM")
    s0 = int(tl.GetStartFrame()); tl.SetStartTimecode("01:00:00:00") if False else None
    for s, _, _ in assets:
        lab = labels.get(s["id"], {})
        tl.AddMarker(s["f_in"], lab.get("marker", "Blue" if s["n"] >= 49 else "Sky"), f"{s['id']} {lab.get('desc', 'shot - labels pending')}"[:120],
                     f"IN {tc(s['f_in'])} OUT {tc(s['f_out'])} DURATION {s['frames']} frames = {s['frames'] * DEN / NUM:.2f} s. Slate background = START frame, "
                     f"thumbnail = END frame. Labels pending/UNVERIFIED unless marked verified.", s["frames"], s["id"])
    items = sorted((it.GetStart() - s0, it.GetDuration()) for it in tl.GetItemListInTrack("video", 1) if it.GetType() != "transition")
    t, gaps = 0, []
    for st_, du in items:
        if st_ != t: gaps.append((t, st_))
        t = st_ + du
    print("timeline", tl.GetName(), "| clips", len(items), "| ends", t, "of", total, "| gaps", len(gaps), "| markers", len(tl.GetMarkers()))
    project.SetCurrentTimeline(tl); h.resolve.OpenPage("edit"); print("saved:", pm.SaveProject(), "|", project.GetName())


if __name__ == "__main__":
    main()
