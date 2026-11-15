"""Pre-production film template: a new Resolve project whose timeline is laid
out with labelled placeholder clips (one per planned shot), movie-style audio
tracks, dissolves, and colour-coded shot markers.

Steps:
  1. make_media(): a 1080p card per shot (Pillow -> ffmpeg, NOT a Resolve
     render), with 1 s of handle each side so dissolves have room, plus silent
     labelled WAVs for lav, nat sound, SFX, music cues and room tone.
  2. write_fcpxml(): V1 story spine with transitions, B-roll on V2, graphics
     on V3, audio in lanes, music cues in their own storyline with crossfades.
  3. build_project(): new project, bins, import, timeline, track names,
     markers, read-back and gap scan.

Run:  ~/theodore/.venv/bin/python build_template.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

import importlib

THEME = os.environ.get("TEMPLATE_THEME", "neon")          # neon | hollywood | fullframe
design = importlib.import_module({"neon": "design", "hollywood": "design_hollywood", "fullframe": "design_fullframe"}[THEME])

PROJECT = {"neon": "Neon Template", "hollywood": "Hollywood Template", "fullframe": "Full Frame Template"}[THEME]
MOD, LES = 1, 1
LESSON_BIN = "M01-L01 Film Template"
TIMELINE = "M01-L01 Film Template"
MEDIA = os.path.expanduser({"neon": "~/Documents/Theodore/Templates/Film Template Media",
                             "hollywood": "~/Documents/Theodore/Templates/Hollywood Template Media",
                             "fullframe": "~/Documents/Theodore/Templates/Full Frame Template Media"}[THEME])
REFS = os.path.join(MEDIA, "references")   # references/<shot ID>/*.jpg|png -> shown on that shot's slate
IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic")


def refs_for(shot_id: str) -> list[str]:
    folder = os.path.join(REFS, shot_id)
    os.makedirs(folder, exist_ok=True)
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(IMG_EXT))
W, H = 1920, 1080
FPS_NUM, FPS_DEN = 24000, 1001          # 23.976
NOMINAL = 24                             # frames per "second" in the plan
HANDLE = 24                              # frames of handle each side of every clip
FONT = "/System/Library/Fonts/Avenir Next.ttc"
HEAD = 240                               # 10 s program slate + black before 01:00:00:00
SLATE_FRAMES = 168                       # 7 s of slate, then 3 s black
START_TC = "00:59:50:00"
META = dict(production="Untitled Production", title="M01-L01 Film Template", director="TBD", dp="TBD",
            producer="TBD", editor="TBD", client="TBD", date="2026-11-14", version="v001 TEMPLATE",
            language="English", subtitles="None")

# kind -> (marker colour, card colour, label)
KINDS = {
    "interview": ("Blue", (38, 78, 140), "INTERVIEW"),
    "instruction": ("Blue", (38, 78, 140), "INSTRUCTION"),
    "broll": ("Green", (40, 112, 70), "B-ROLL"),
    "transition": ("Yellow", (150, 120, 20), "TRANSITION"),
    "title": ("Purple", (98, 54, 140), "TITLE"),
}

# The story spine on V1, in order: (key, kind, seconds, description)
SPINE = [
    ("open_est", "broll", 6, "Cold open - establishing wide"),
    ("hook", "interview", 8, "Cold open - hook soundbite"),
    ("main_title", "title", 5, "Main title"),
    ("host_intro", "instruction", 20, "Act 1 - host intro to camera"),
    ("subj_a", "interview", 20, "Act 1 - interview subject A"),
    ("bumper1", "transition", 3, "Chapter bumper - Act 2"),
    ("demo_wide", "instruction", 15, "Act 2 - demonstration wide"),
    ("demo_cu", "broll", 8, "Act 2 - demonstration close-ups"),
    ("subj_b", "interview", 18, "Act 2 - interview subject B"),
    ("mont1", "broll", 4, "Montage - detail 1"),
    ("mont2", "broll", 4, "Montage - detail 2"),
    ("mont3", "broll", 4, "Montage - detail 3"),
    ("timelapse", "transition", 3, "Time-lapse - passage of time"),
    ("recap", "instruction", 15, "Act 3 - host recap"),
    ("hero", "broll", 6, "Closing hero shot"),
    ("credits", "title", 8, "End title and credits"),
]
# Connected over a spine clip: (key, kind, seconds, description, parent key, seconds into parent, lane)
OVER = [
    ("host_cut", "broll", 5, "B-roll cutaway - location", "host_intro", 8, 1),
    ("lt_a", "title", 5, "Lower third - subject A name", "subj_a", 1, 2),
    ("subj_a_cut", "broll", 6, "B-roll cutaway - subject A at work", "subj_a", 10, 1),
    ("lt_b", "title", 5, "Lower third - subject B name", "subj_b", 1, 2),
    ("subj_b_cut", "broll", 5, "B-roll cutaway - subject B detail", "subj_b", 9, 1),
]
def load_shot_list(path: str | None = None):
    """shot_list.csv is the single source of truth: edit it, re-run, everything regenerates.
    Rows with a `parent` ride over that spine clip (B-roll / graphics); the rest form the V1 story."""
    import csv
    path = path or os.environ.get("SHOT_LIST") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_list.csv")
    spine, over, extra = [], [], {}
    for r in csv.DictReader(open(path, newline="")):
        if not (r.get("key") or "").strip():
            continue
        r = {k: (v or "").strip() for k, v in r.items()}
        extra[r["key"]] = r
        if r["parent"]:
            over.append((r["key"], r["kind"], float(r["seconds"]), r["description"], r["parent"],
                         float(r["into_seconds"] or 0), int(r["lane"] or 1)))
        else:
            spine.append((r["key"], r["kind"], float(r["seconds"]), r["description"]))
    return spine, over, extra


SPINE, OVER, SHOT_EXTRA = load_shot_list()

# Dissolves on V1: cut after spine index i -> seconds. "head"/"tail" are fades from/to black.
DISSOLVES = {1: 0.5, 2: 0.5, 4: 0.5, 5: 0.5, 8: 0.5, 9: 0.5, 10: 0.5, 11: 0.5, 12: 0.5, 14: 1.0}
HEAD_FADE, TAIL_FADE = 1.0, 2.0
# Music cues (A5): (name, first spine key, last spine key)
MUSIC = [
    ("MUSIC Cue 1 - Cold open + title", "open_est", "main_title"),
    ("MUSIC Cue 2 - Act 1-2 underscore", "host_intro", "subj_b"),
    ("MUSIC Cue 3 - Montage", "mont1", "timelapse"),
    ("MUSIC Cue 4 - Recap + credits", "recap", "credits"),
]
MUSIC_XFADE = 1.0
TRACKS_V = ["V1 STORY A-CAM", "V2 B-ROLL B-CAM", "V3 GRAPHICS"]
TRACKS_A = ["A1 DIA CAM", "A2 LAV", "A3 NAT SOUND", "A4 SFX", "A5 MUSIC", "A6 ROOM TONE"]


def fr(frames: int) -> str:
    """Frames at 23.976 -> FCPXML rational time."""
    t = Fraction(frames * FPS_DEN, FPS_NUM)
    return f"{t.numerator}/{t.denominator}s"


# --------------------------------------------------------------------------- plan

def plan() -> dict:
    spine, t = [], HEAD
    for key, kind, sec, desc in SPINE:
        n = int(round(sec * NOMINAL))
        spine.append(dict(key=key, kind=kind, frames=n, desc=desc, start=t, track="V1"))
        t += n
    total = t
    by = {s["key"]: s for s in spine}
    over = []
    for key, kind, sec, desc, parent, into, lane in OVER:
        over.append(dict(key=key, kind=kind, frames=int(round(sec * NOMINAL)), desc=desc, parent=parent,
                         start=by[parent]["start"] + int(round(into * NOMINAL)), lane=lane, track=f"V{lane + 1}"))
    shots = sorted(spine + over, key=lambda s: (s["start"], s["track"]))
    for i, s in enumerate(shots, 1):
        s["id"] = f"M{MOD:02d}-L{LES:02d}-S{i:02d}"
        s["name"] = f"{s['id']} {s['desc']}"
    for n, s in enumerate(spine, 1):
        s["scene"] = str(n)
    letters = {}
    for s in shots:
        if s["track"] != "V1":
            k = letters[s["parent"]] = letters.get(s["parent"], 0) + 1
            s["scene"] = by[s["parent"]]["scene"] + "ABCDEFGH"[k - 1]
    for s in shots:
        s["take"] = "1"
        s["roll"], s["camera"] = {"V1": ("A001", "A-CAM"), "V2": ("B001", "B-CAM"), "V3": ("GFX", "GFX")}[s["track"]]
        s["sound"] = {"interview": "SYNC", "instruction": "SYNC", "broll": "NAT"}.get(s["kind"], "GFX")
        s["where"] = {"V1": "V1 · A-CAM / STORY", "V2": "V2 · B-CAM / B-ROLL", "V3": "V3 · GRAPHICS"}[s["track"]]
    return dict(spine=spine, over=over, shots=shots, total=total, by=by)


# --------------------------------------------------------------------------- media

def _font(size, index=0):
    return ImageFont.truetype(FONT, size, index=index)


def card(shot: dict, path: str) -> None:
    _m, rgb, label = KINDS[shot["kind"]]
    lower_third = shot["kind"] == "title" and shot["desc"].startswith("Lower third")
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0) if lower_third else (18, 18, 20, 255))
    d = ImageDraw.Draw(img)
    if lower_third:  # sits on V3 over picture: a box in the lower third only
        box = (120, 760, 1100, 940)
        d.rounded_rectangle(box, 18, fill=rgb + (235,), outline=(255, 255, 255, 255), width=4)
        d.text((150, 780), shot["id"], font=_font(44, 2), fill="white")
        d.text((150, 840), shot["desc"], font=_font(40), fill=(235, 235, 235))
        img.save(path)
        return
    d.rounded_rectangle((60, 60, W - 60, H - 60), 28, fill=rgb + (255,), outline=(255, 255, 255, 200), width=6)
    # title/action-safe guides
    d.rectangle((W * .05, H * .05, W * .95, H * .95), outline=(255, 255, 255, 60), width=2)
    d.text((W / 2, 250), label, font=_font(64, 2), fill=(255, 255, 255, 190), anchor="mm")
    d.text((W / 2, 440), shot["id"], font=_font(170, 2), fill="white", anchor="mm")
    d.text((W / 2, 620), shot["desc"], font=_font(72), fill="white", anchor="mm")
    secs = shot["frames"] / NOMINAL
    where = {"V1": "A-CAM / STORY", "V2": "B-CAM / B-ROLL", "V3": "GRAPHICS"}[shot["track"]]
    d.text((W / 2, 780), f"Planned {secs:g} s   |   {where}   |   PLACEHOLDER - replace with footage",
           font=_font(40), fill=(255, 255, 255, 210), anchor="mm")
    img.save(path)


def _run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def video(png: str, mp4: str, frames: int, with_audio: bool) -> None:
    n = frames + 2 * HANDLE
    dur = n * FPS_DEN / FPS_NUM
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", f"{FPS_NUM}/{FPS_DEN}", "-i", png]
    if with_audio:  # camera scratch placeholder: silent stereo so A1 gets a clip
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-frames:v", str(n), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-r", f"{FPS_NUM}/{FPS_DEN}"]
    cmd += (["-c:a", "pcm_s16le", "-t", f"{dur:.4f}", mp4.replace(".mp4", ".mov")] if with_audio else [mp4])
    _run(cmd)


def video_alpha(png: str, mov: str, frames: int) -> None:
    n = frames + 2 * HANDLE
    _run(["ffmpeg", "-y", "-loop", "1", "-framerate", f"{FPS_NUM}/{FPS_DEN}", "-i", png, "-frames:v", str(n),
          "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-r", f"{FPS_NUM}/{FPS_DEN}", mov])


def wav(path: str, frames: int) -> None:
    secs = frames * FPS_DEN / FPS_NUM
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{secs:.4f}",
          "-c:a", "pcm_s24le", path])


def make_media(p: dict) -> dict:
    """Returns {asset key: dict(path, frames, kind of media, bin)}."""
    os.makedirs(os.path.join(MEDIA, "cards"), exist_ok=True)
    max_frames = max(s["frames"] for s in p["shots"])
    png = os.path.join(MEDIA, "cards", "00 PROGRAM SLATE.png")
    design.program_slate(META, png, p["total"] - HEAD, TRACKS_A)
    slate = os.path.join(MEDIA, "00 PROGRAM SLATE.mp4")
    _run(["ffmpeg", "-y", "-loop", "1", "-framerate", f"{FPS_NUM}/{FPS_DEN}", "-t", str(SLATE_FRAMES * FPS_DEN / FPS_NUM),
          "-i", png, "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS_NUM}/{FPS_DEN}",
          "-filter_complex", f"[0:v]fps={FPS_NUM}/{FPS_DEN},format=yuv420p,trim=end_frame={SLATE_FRAMES}[a];"
          f"[1:v]format=yuv420p,trim=end_frame={HEAD - SLATE_FRAMES + 2 * HANDLE}[b];[a][b]concat=n=2:v=1[v]",
          "-map", "[v]", "-c:v", "libx264", "-tune", "stillimage", "-r", f"{FPS_NUM}/{FPS_DEN}", slate])
    assets = {"PROGRAM SLATE": dict(path=slate, frames=HEAD + 2 * HANDLE, audio=False, video=True, bin="Graphics",
                                    name="00 PROGRAM SLATE")}
    for s in p["shots"]:
        png = os.path.join(MEDIA, "cards", s["name"] + ".png")
        if s["track"] == "V3":
            design.lower_third(s, META, png)
        else:
            design.camera_slate(s, META, png, max_frames, refs_for(s["id"]))
        talk = s["kind"] in ("interview", "instruction")
        if s["track"] == "V3":
            out = os.path.join(MEDIA, s["name"] + ".mov")
            video_alpha(png, out, s["frames"])
        else:
            out = os.path.join(MEDIA, s["name"] + (".mov" if talk else ".mp4"))
            video(png, out, s["frames"], talk)
        bin_ = {"interview": "A-Cam", "instruction": "A-Cam", "broll": "B-Cam"}.get(s["kind"], "Graphics")
        assets[s["key"]] = dict(path=out, frames=s["frames"] + 2 * HANDLE, audio=talk, video=True, bin=bin_,
                                name=s["name"])
        if talk:  # lav under every talking shot
            lav = os.path.join(MEDIA, f"{s['id']} LAV.wav")
            wav(lav, s["frames"] + 2 * HANDLE)
            assets[s["key"] + "_lav"] = dict(path=lav, frames=s["frames"] + 2 * HANDLE, audio=True, video=False,
                                             bin="Audio", name=f"{s['id']} LAV")
        if s["kind"] == "broll":
            nat = os.path.join(MEDIA, f"{s['id']} NAT SOUND.wav")
            wav(nat, s["frames"] + 2 * HANDLE)
            assets[s["key"] + "_nat"] = dict(path=nat, frames=s["frames"] + 2 * HANDLE, audio=True, video=False,
                                             bin="Audio", name=f"{s['id']} NAT SOUND")
    for kind, secs in (("SFX Whoosh", 2), ("SFX Title hit", 3)):
        path = os.path.join(MEDIA, f"{kind}.wav")
        wav(path, secs * NOMINAL)
        assets[kind] = dict(path=path, frames=secs * NOMINAL, audio=True, video=False, bin="Audio", name=kind)
    by = p["by"]
    for name, a, b in MUSIC:
        frames = by[b]["start"] + by[b]["frames"] - by[a]["start"] + 2 * HANDLE
        path = os.path.join(MEDIA, f"{name}.wav")
        wav(path, frames)
        assets[name] = dict(path=path, frames=frames, audio=True, video=False, bin="Audio", name=name)
    path = os.path.join(MEDIA, "ROOM TONE - full length.wav")
    wav(path, p["total"] - HEAD)
    assets["ROOM TONE"] = dict(path=path, frames=p["total"] - HEAD, audio=True, video=False, bin="Audio",
                               name="ROOM TONE - full length")
    return assets


# --------------------------------------------------------------------------- FCPXML

def write_fcpxml(p: dict, assets: dict, out: str) -> dict:
    """Also returns the planned audio layout for read-back."""
    root = ET.Element("fcpxml", version="1.10")
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r0", name="FFVideoFormat1080p2398", frameDuration=f"{FPS_DEN}/{FPS_NUM}s",
                  width=str(W), height=str(H))
    ids = {}
    for i, (key, a) in enumerate(assets.items(), 1):
        rid = f"a{i}"
        ids[key] = rid
        attrs = dict(id=rid, name=a["name"], start="0s", duration=fr(a["frames"]),
                     hasVideo="1" if a["video"] else "0", hasAudio="1" if a["audio"] else "0")
        if a["video"]:
            attrs["format"] = "r0"
        if a["audio"]:
            attrs.update(audioSources="1", audioChannels="2", audioRate="48000")
        el = ET.SubElement(res, "asset", attrs)
        ET.SubElement(el, "media-rep", kind="original-media", src="file://" + a["path"].replace(" ", "%20"))
    lib = ET.SubElement(root, "library")
    ev = ET.SubElement(lib, "event", name=TIMELINE)
    proj = ET.SubElement(ev, "project", name=TIMELINE)
    seq = ET.SubElement(proj, "sequence", format="r0", duration=fr(p["total"]), tcStart="0s", tcFormat="NDF",
                        audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    clips = []
    audio_plan = []  # (track, name, record start, frames)

    def connect(parent_el, parent_shot, key, rec, frames, lane, src=HANDLE, role=None):
        # child offsets are in the parent's source time: parent start (HANDLE) + time into parent
        off = HANDLE + rec - parent_shot["start"]
        attrs = dict(ref=ids[key], lane=str(lane), offset=fr(off), name=assets[key]["name"],
                     start=fr(src), duration=fr(frames))
        if role:
            attrs["audioRole"] = role
        return ET.SubElement(parent_el, "asset-clip", attrs)

    ET.SubElement(spine, "asset-clip", ref=ids["PROGRAM SLATE"], offset="0s", name="00 PROGRAM SLATE",
                  start="0s", duration=fr(HEAD), tcFormat="NDF")
    for i, s in enumerate(p["spine"]):
        el = ET.SubElement(spine, "asset-clip", ref=ids[s["key"]], offset=fr(s["start"]), name=s["name"],
                           start=fr(HANDLE), duration=fr(s["frames"]), tcFormat="NDF")
        clips.append(el)
        if assets[s["key"]]["audio"]:
            audio_plan.append(("A1", s["name"], s["start"], s["frames"]))
            connect(el, s, s["key"] + "_lav", s["start"], s["frames"], -1, role="dialogue")
            audio_plan.append(("A2", f"{s['id']} LAV", s["start"], s["frames"]))
        if s["kind"] == "broll":
            connect(el, s, s["key"] + "_nat", s["start"], s["frames"], -2, role="effects")
            audio_plan.append(("A3", f"{s['id']} NAT SOUND", s["start"], s["frames"]))
        if s["kind"] in ("transition", "title"):
            # whoosh into transitions, hit on titles: centred on the cut in
            sfx = "SFX Title hit" if s["kind"] == "title" else "SFX Whoosh"
            n = assets[sfx]["frames"]
            rec = max(0, s["start"] - n // 2)
            if i and rec < s["start"]:
                connect(clips[-2], p["spine"][i - 1], sfx, rec, n, -3, src=0, role="effects")
            else:
                connect(el, s, sfx, rec, n, -3, src=0, role="effects")
            audio_plan.append(("A4", sfx, rec, n))
        for o in p["over"]:
            if o["parent"] == s["key"]:
                connect(el, s, o["key"], o["start"], o["frames"], o["lane"])
                if o["kind"] == "broll":
                    connect(el, s, o["key"] + "_nat", o["start"], o["frames"], -2, role="effects")
                    audio_plan.append(("A3", f"{o['id']} NAT SOUND", o["start"], o["frames"]))
    # Music: one secondary storyline on lane -4 attached to the first clip, cues crossfaded.
    first = p["spine"][0]
    mus = ET.SubElement(clips[0], "spine", lane="-4", offset=fr(HANDLE))
    by = p["by"]
    x = int(MUSIC_XFADE * NOMINAL)
    t_local = 0
    for k, (name, a, b) in enumerate(MUSIC):
        rec = by[a]["start"]
        end = by[b]["start"] + by[b]["frames"]
        if k:
            mus.append(ET.Element("transition", name="Cross Dissolve", offset=fr(HANDLE + rec - first["start"] - x // 2), duration=fr(x)))
        ET.SubElement(mus, "asset-clip", ref=ids[name], offset=fr(HANDLE + rec - first["start"]), name=name, start=fr(HANDLE),
                      duration=fr(end - rec), audioRole="music")
        audio_plan.append(("A5", name, rec, end - rec))
    connect(clips[0], first, "ROOM TONE", first["start"], p["total"] - HEAD, -5, src=0, role="dialogue")
    audio_plan.append(("A6", "ROOM TONE - full length", first["start"], p["total"] - HEAD))
    # V1 dissolves, inserted backwards so indices hold.
    for i in sorted(DISSOLVES, reverse=True):
        nxt = p["spine"][i]
        d = int(DISSOLVES[i] * NOMINAL)
        spine.insert(list(spine).index(clips[i]),
                     ET.Element("transition", name="Cross Dissolve", offset=fr(nxt["start"] - d // 2), duration=fr(d)))
    hf = int(HEAD_FADE * NOMINAL)  # fade up out of the black after the slate
    spine.insert(list(spine).index(clips[0]),
                 ET.Element("transition", name="Cross Dissolve", offset=fr(HEAD - hf // 2), duration=fr(hf)))
    spine.append(ET.Element("transition", name="Cross Dissolve", offset=fr(p["total"] - int(TAIL_FADE * NOMINAL)),
                            duration=fr(int(TAIL_FADE * NOMINAL))))
    with open(out, "wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        ET.ElementTree(root).write(fh, encoding="utf-8", xml_declaration=False)
    return dict(audio=audio_plan)


if __name__ == "__main__":
    p = plan()
    if "--plan" in sys.argv:
        for s in p["shots"]:
            print(s["id"], s["track"], s["start"], s["frames"], s["kind"], s["desc"])
        sys.exit()
    assets = make_media(p)
    info = write_fcpxml(p, assets, os.path.join(MEDIA, "template.fcpxml"))
    json.dump(dict(plan={k: v for k, v in p.items() if k != "by"}, assets=assets, audio=info["audio"]),
              open(os.path.join(MEDIA, "plan.json"), "w"), indent=1)
    print("media + fcpxml written to", MEDIA)
