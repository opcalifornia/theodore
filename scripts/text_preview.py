"""Render contact-sheet previews of Theodore title templates from live Resolve.

usage: python3 scripts/text_preview.py "<Template Name>" [frames comma list] [text]
Works only on the 'Theodore Text Lab' timeline, never on the editor's timelines.
"""
import subprocess, sys, time
from pathlib import Path
from theodore.resolve.connection import connect
from theodore.resolve import timecode

OUT = Path.home() / "Movies" / "Theodore Text"
OUT.mkdir(parents=True, exist_ok=True)
LAB = "Theodore Text Lab"


def lab(h):
    p = h.project
    for i in range(1, p.GetTimelineCount() + 1):
        t = p.GetTimelineByIndex(i)
        if t.GetName() == LAB:
            p.SetCurrentTimeline(t)
            return t
    t = p.GetMediaPool().CreateEmptyTimeline(LAB)
    p.SetCurrentTimeline(t)
    return t


def preview(h, name, frames, text=None):
    tl = lab(h)
    for tr in range(1, tl.GetTrackCount("video") + 1):
        items = tl.GetItemListInTrack("video", tr)
        if items:
            tl.DeleteClips(items, False)
    tl.SetCurrentTimecode(tl.GetStartTimecode())
    item = tl.InsertFusionTitleIntoTimeline(name)
    if not item:
        raise SystemExit(f"Resolve would not insert '{name}' (is it installed? restart needed for brand-new files?)")
    if text:
        comp = item.GetFusionCompByIndex(1)
        for tool in comp.GetToolList().values():
            if tool.ID in ("TextPlus", "StyledTextFollower"):
                pass
    h.resolve.OpenPage("edit")
    start = item.GetStart()
    shots = []
    for f in frames:
        tl.SetCurrentTimecode(timecode.frames_to_timecode(start + f, "24"))
        time.sleep(0.15)
        p = OUT / f"_{name.replace(' ', '_')}_{f:03d}.png"
        if p.exists():
            p.unlink()
        h.project.ExportCurrentFrameAsStill(str(p))
        shots.append(p)
    sheet = OUT / f"{name}.png"
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s in shots:
        cmd += ["-i", str(s)]
    n = len(shots)
    cols = min(4, n)
    rows = (n + cols - 1) // cols
    filt = "".join(f"[{i}:v]scale=480:-1[v{i}];" for i in range(n))
    filt += "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout=" + "|".join(f"{(i % cols) * 480}_{(i // cols) * 270}" for i in range(n))
    cmd += ["-filter_complex", filt, str(sheet)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    for s in shots:
        s.unlink(missing_ok=True)
    print(sheet if sheet.exists() else r.stderr)


if __name__ == "__main__":
    h = connect(require_timeline=False)
    name = sys.argv[1]
    frames = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [0, 4, 8, 12, 16, 22, 30, 45]
    preview(h, name, frames, sys.argv[3] if len(sys.argv) > 3 else None)
