"""Keep a project the way an assistant editor would hand it over.

Bins
  Footage/<camera>   picture, split by camera (from metadata) when there are several
  Audio              sound-only files (lavs, recorders, music)
  Multicam           multicam clips
  Graphics           titles and lower thirds Theodore built
  Stills             images and reference frames
  Timelines          timelines; Theodore's checkpoints in Timelines/Checkpoints
Tracks
  V1 "A-Roll", picture above it "B-Roll", graphics tracks "Graphics",
  lav tracks "Lav <name>", other sound "Dialogue"/"Music", subtitles "Captions".

Only Theodore's own sorting is applied; bins the editor made are left as they
are, and nothing is ever deleted.
"""
from __future__ import annotations

import re
from typing import Optional

KNOWN_BINS = {"Footage", "Audio", "Multicam", "Graphics", "Stills", "Timelines", "Theodore References",
              "Theodore Graphics"}


def _walk(folder, path=""):
    yield path or "/", folder
    for sub in folder.GetSubFolderList() or []:
        yield from _walk(sub, f"{path}/{sub.GetName()}")


def _child(pool, parent, name):
    for sub in parent.GetSubFolderList() or []:
        if sub.GetName() == name:
            return sub
    return pool.AddSubFolder(parent, name)


def _camera_label(clip) -> str:
    maker = str(clip.GetClipProperty("Camera Manufacturer") or "").strip()
    cam = str(clip.GetClipProperty("Camera Type") or "").strip()
    label = cam or maker
    if not label:
        name = clip.GetName()
        m = re.match(r"(?i)(cam[\s_-]*[a-z0-9]+|[a-z]\d{3}c\d{3})", name)
        label = m.group(1).upper() if m else "Other"
    return re.sub(r"[/\\:]", "-", label)[:40]


def organize_media(h) -> dict:
    pool = h.project.GetMediaPool()
    root = pool.GetRootFolder()
    moves: dict[str, list] = {}
    for path, folder in _walk(root):
        top = path.strip("/").split("/")[0] if path != "/" else ""
        # Only sort what sits loose in the root or in Theodore's own bins.
        if top and top not in KNOWN_BINS:
            continue
        for clip in folder.GetClipList() or []:
            kind = str(clip.GetClipProperty("Type") or "")
            name = clip.GetName()
            if kind == "Timeline":
                dest = "Timelines/Checkpoints" if name.startswith("Theodore checkpoint") else "Timelines"
            elif kind == "Compound":
                dest = "Graphics"
            elif kind == "Multicam":
                dest = "Multicam"
            elif kind == "Audio":
                dest = "Audio"
            elif kind == "Still" or name.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
                dest = "Stills"
            elif "video" in kind.lower():
                dest = f"Footage/{_camera_label(clip)}"
            else:
                continue
            if path.strip("/") != dest:
                moves.setdefault(dest, []).append(clip)
    # One camera only: no need for a sub-bin per camera.
    cameras = [d for d in moves if d.startswith("Footage/")]
    existing_cams = [p for p, _ in _walk(root) if p.startswith("/Footage/")]
    if len(set(cameras) | {c.lstrip("/") for c in existing_cams}) <= 1:
        for d in cameras:
            moves.setdefault("Footage", []).extend(moves.pop(d))
    moved = {}
    for dest, clips in moves.items():
        folder = root
        for part in dest.split("/"):
            folder = _child(pool, folder, part)
        if folder and pool.MoveClips(clips, folder):
            moved[dest] = len(clips)
    return {"moved": moved, "untouched": "bins you made yourself are left as they are"}


def label_tracks(tl, lav_names: Optional[dict] = None) -> dict:
    """Name every track for what's on it. Returns {track: name}."""
    names = {}
    vcount = int(tl.GetTrackCount("video") or 0)
    for idx in range(1, vcount + 1):
        items = tl.GetItemListInTrack("video", idx) or []
        if not items:
            continue
        kinds = []
        for it in items:
            mpi = it.GetMediaPoolItem()
            kinds.append(str(mpi.GetClipProperty("Type")) if mpi else "Title")
        if all(k in ("Compound", "Title") for k in kinds):
            label = "Graphics"
        elif idx == 1:
            label = "A-Roll" if "Multicam" not in kinds else "Multicam"
        else:
            label = "B-Roll"
        if tl.SetTrackName("video", idx, label):
            names[f"V{idx}"] = label
    for idx in range(1, int(tl.GetTrackCount("audio") or 0) + 1):
        items = tl.GetItemListInTrack("audio", idx) or []
        if not items:
            continue
        clip_name = items[0].GetName()
        m = re.search(r"(?i)lav[\s_-]*([a-z]+)", clip_name)
        if lav_names and clip_name in lav_names:
            label = f"Lav {lav_names[clip_name]}"
        elif m:
            label = f"Lav {m.group(1).title()}"
        elif re.search(r"(?i)music|song|track|score|bed", clip_name):
            label = "Music"
        else:
            label = "Dialogue"
        if tl.SetTrackName("audio", idx, label):
            names[f"A{idx}"] = label
    for idx in range(1, int(tl.GetTrackCount("subtitle") or 0) + 1):
        if tl.SetTrackName("subtitle", idx, "Captions" if idx == 1 else f"Captions {idx}"):
            names[f"ST{idx}"] = "Captions"
    return names
