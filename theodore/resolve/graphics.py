"""Titles and lower thirds, built as Fusion node trees and placed safely.

Resolve's InsertTitle/InsertFusionTitle calls are *insert edits* on V1 at the
playhead: they split whatever is there and push the rest of the timeline
along (measured on Resolve 21). So graphics are never inserted into the
editor's timeline. Instead:

1. On a throwaway "factory" timeline at the target's resolution, a Text+
   title is inserted and its Fusion comp is built out into a designed
   graphic -- plate, accent bar, two text layers, eased fade/slide in & out.
2. The title is turned into a compound clip, which lives in the media pool.
3. That clip is placed like any other clip: exact frame, on the first video
   track above the picture that's free for its duration -- nothing below is
   touched. The factory timeline is then removed.
"""
from __future__ import annotations

import time
from typing import Optional

from theodore.resolve import timecode

GRAPHICS_BIN = "Theodore Graphics"
ANIM = 8           # frames for the in/out animation
TITLE_FRAMES = 120  # Resolve's standard title length (5 s at 24 fps)

STYLES = ("boxed", "clean", "title_card", "title_card_solid")


class GraphicsError(RuntimeError):
    pass


def _registry_path(project_name: str):
    import re
    from pathlib import Path
    from theodore import config
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project_name).strip("-").lower() or "project"
    return Path(config.DATA_ROOT) / "graphics" / f"{slug}.json"


def remember(project_name: str, clip_name: str, spec: dict) -> None:
    """What each graphic was built from, so another shape can rebuild it."""
    import json
    path = _registry_path(project_name)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[clip_name] = spec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def recall(project_name: str, clip_name: str) -> Optional[dict]:
    import json
    path = _registry_path(project_name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get(clip_name)


def is_compound(clip) -> bool:
    try:
        return "compound" in str(clip.GetClipProperty("Type") or "").lower()
    except Exception:  # noqa: BLE001
        return False


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = (hex_color or "#E8B04A").lstrip("#")
    if len(h) != 6:
        h = "E8B04A"
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


CHAR_W = 0.32  # average character width / Text+ size (Open Sans, measured)


def _text_width(text: str, size: float) -> float:
    return len(text or "") * size * CHAR_W


def _set(tool, **inputs):
    for k, v in inputs.items():
        tool.SetInput(k, v)


def _text(comp, tool, text, size, x, y, rgb=(1, 1, 1), style="Bold", font="Open Sans",
          align="left", tracking=1.0):
    tool = tool or comp.AddTool("TextPlus", -1, -1)
    just = {"left": (-1, 0), "center": (0, 1), "right": (1, 2)}[align]
    _set(tool, StyledText=text, Font=font, Style=style, Size=size,
         Center={1: x, 2: y, 3: 0.0}, Red1=rgb[0], Green1=rgb[1], Blue1=rgb[2],
         HorizontalLeftCenterRight=just[0], HorizontalJustificationNew=just[1],
         CharacterSpacing=tracking)
    return tool


def _box(comp, x, y, w, h, rgba, radius=0.0):
    bg = comp.AddTool("Background", -1, -1)
    _set(bg, TopLeftRed=rgba[0], TopLeftGreen=rgba[1], TopLeftBlue=rgba[2], TopLeftAlpha=rgba[3])
    mask = comp.AddTool("RectangleMask", -1, -1)
    _set(mask, Center={1: x, 2: y}, Width=w, Height=h, CornerRadius=radius)
    bg.ConnectInput("EffectMask", mask)
    return bg


def _merge(comp, back, fore):
    m = comp.AddTool("Merge", -1, -1)
    m.ConnectInput("Background", back)
    m.ConnectInput("Foreground", fore)
    return m


def _animate(comp, final, frames: int, slide: float):
    """Eased fade + slide in over ANIM frames, out over the last ANIM."""
    clear = comp.AddTool("Background", -1, -1)
    _set(clear, TopLeftAlpha=0.0)
    m = _merge(comp, clear, final)
    m.AddModifier("Blend", "BezierSpline")
    for t, v in ((0, 0.0), (ANIM, 1.0), (frames - ANIM, 1.0), (frames - 1, 0.0)):
        m.SetInput("Blend", v, t)
    if slide:
        m.AddModifier("Center", "XYPath")
        for t, x in ((0, 0.5 - slide), (ANIM, 0.5), (frames - ANIM, 0.5), (frames - 1, 0.5 - slide / 2)):
            m.SetInput("Center", {1: x, 2: 0.5}, t)
    return m


def build(comp, kind: str, text: str, subtext: str, accent: str, aspect: float, frames: int):
    """Build one graphic in ``comp`` (which already holds a Text+ called Template)."""
    comp.Lock()
    try:
        main = comp.FindTool("Template")
        out = comp.FindTool("MediaOut1")
        acc = _rgb(accent)
        vertical = aspect < 1.0
        if kind in ("boxed", "clean"):
            # Safe-area aware: 7% in from the left, lower fifth of the frame.
            left, base = (0.08, 0.22) if vertical else (0.07, 0.16)
            name_size, title_size = (0.08, 0.048) if vertical else (0.062, 0.036)
            # Text size follows frame width, plate height follows frame height:
            # on a tall frame the same type needs proportionally less plate.
            k = 0.56 if vertical else 1.0
            # Text+ size is relative to frame width; measured on Open Sans,
            # a character averages ~0.32 x size wide. Size the plate to fit.
            width = max(_text_width(text, name_size), _text_width(subtext, title_size)) + 0.045
            if width > 0.86:  # long names: shrink the type rather than run off frame
                k = 0.86 / width
                name_size, title_size, width = name_size * k, title_size * k, 0.86
            plate_h = ((name_size + (title_size if subtext else 0)) * 1.45 + 0.03) * k
            name = _text(comp, main, text, name_size, left + 0.02, base + (title_size * 1.1 * k if subtext else 0.0))
            layers = [name]
            if subtext:
                layers.append(_text(comp, None, subtext, title_size, left + 0.02, base - 0.012 * k,
                                    rgb=(0.86, 0.86, 0.86), style="Regular"))
            cy = base + (title_size * 0.45 if subtext else name_size * 0.25) * k
            if kind == "boxed":
                plate = _box(comp, left + width / 2, cy, width, plate_h, (0.04, 0.04, 0.05, 0.72), 0.02)
                bar = _box(comp, left - 0.004, cy, 0.006, plate_h, (*acc, 1.0))
                img = _merge(comp, plate, bar)
            else:
                img = _box(comp, left + 0.02 + width * 0.3, base - 0.035 - (0.0 if subtext else -0.02),
                           width * 0.6, 0.006, (*acc, 1.0))
            for layer in layers:
                img = _merge(comp, img, layer)
            final = _animate(comp, img, frames, slide=0.04)
        else:
            # Biggest size that keeps the line inside a 9% safe margin each side.
            size = min(0.11 if not vertical else 0.085, 0.82 / max(len(text) * CHAR_W * 1.08, 1))
            title = _text(comp, main, text, size, 0.5, 0.53 if subtext else 0.5, align="center", tracking=1.05)
            img = title
            if subtext:
                sub_size = min(size * 0.38, 0.82 / max(len(subtext) * CHAR_W * 1.25, 1))
                sub = _text(comp, None, subtext, sub_size, 0.5, 0.42, rgb=(*acc,), style="Regular", align="center",
                            tracking=1.2)
                img = _merge(comp, title, sub)
            if kind == "title_card_solid":
                solid = comp.AddTool("Background", -1, -1)
                _set(solid, TopLeftRed=0.03, TopLeftGreen=0.03, TopLeftBlue=0.035, TopLeftAlpha=1.0)
                img = _merge(comp, solid, img)
            final = _animate(comp, img, frames, slide=0.0)
        out.ConnectInput("Input", final)
    finally:
        comp.Unlock()


def _graphics_bin(pool):
    root = pool.GetRootFolder()
    for sub in root.GetSubFolderList() or []:
        if sub.GetName() == GRAPHICS_BIN:
            return sub
    return pool.AddSubFolder(root, GRAPHICS_BIN) or root


def make_graphic(h, kind: str, text: str, subtext: str = "", accent: str = "#E8B04A",
                 name: Optional[str] = None, frames: Optional[int] = None):
    """Build the graphic and return its media pool clip (a compound clip)."""
    if kind not in STYLES:
        raise GraphicsError(f"style must be one of {STYLES}")
    project, pool = h.project, h.project.GetMediaPool()
    target = project.GetCurrentTimeline()
    width = int(target.GetSetting("timelineResolutionWidth") or 1920) if target else 1920
    height = int(target.GetSetting("timelineResolutionHeight") or 1080) if target else 1080
    fps = (target.GetSetting("timelineFrameRate") if target else None) or project.GetSetting("timelineFrameRate")
    previous_folder = pool.GetCurrentFolder()
    pool.SetCurrentFolder(_graphics_bin(pool))
    factory = pool.CreateEmptyTimeline(f"Theodore graphics factory {int(time.time() * 1000)}")
    if not factory:
        raise GraphicsError("Could not create the graphics factory timeline.")
    try:
        project.SetCurrentTimeline(factory)
        for k, v in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(width)),
                     ("timelineResolutionHeight", str(height))):
            factory.SetSetting(k, v)
        factory.SetCurrentTimecode(timecode.frames_to_timecode(int(factory.GetStartFrame()), str(fps)))
        item = factory.InsertFusionTitleIntoTimeline("Text+")
        if not item:
            raise GraphicsError("Resolve would not create a Text+ title.")
        available = int(item.GetDuration() or TITLE_FRAMES)
        frames = max(ANIM * 2 + 2, min(int(frames or available), available))
        build(item.GetFusionCompByIndex(1), kind, text, subtext, accent, width / height, frames)
        label = name or f"{'LT' if kind in ('boxed', 'clean') else 'Title'} - {text[:40]}"
        if width < height:
            label += f" ({width}x{height})"
        compound = factory.CreateCompoundClip([item], {"name": label})
        clip = compound.GetMediaPoolItem() if compound else None
        if clip is None:
            raise GraphicsError("Could not turn the graphic into a clip.")
        remember(project.GetName(), clip.GetName(),
                 {"kind": kind, "text": text, "subtext": subtext, "accent": accent, "frames": frames})
        return clip, frames
    finally:
        if target:
            project.SetCurrentTimeline(target)
        if previous_folder:
            pool.SetCurrentFolder(previous_folder)
        try:
            pool.DeleteTimelines([factory])
        except Exception:  # noqa: BLE001
            pass


def free_video_track(tl, start: int, end: int, lowest: int = 2) -> int:
    count = int(tl.GetTrackCount("video") or 0)
    for idx in range(lowest, count + 1):
        if all(not (it.GetStart() < end and start < it.GetEnd()) for it in (tl.GetItemListInTrack("video", idx) or [])):
            return idx
    tl.AddTrack("video")
    return int(tl.GetTrackCount("video"))


def place(h, tl, clip, record_frame: int, frames: int, max_frames: int) -> dict:
    from theodore.resolve.timeline_ops import _placed_item
    frames = max(ANIM * 2 + 2, min(int(frames), int(max_frames)))
    track = free_video_track(tl, record_frame, record_frame + frames)
    got = _placed_item(h.project.GetMediaPool().AppendToTimeline([{
        "mediaPoolItem": clip, "startFrame": 0, "endFrame": frames, "recordFrame": int(record_frame),
        "trackIndex": track, "mediaType": 1}]))
    if got is None:
        raise GraphicsError(f"Resolve refused to place the graphic at frame {record_frame} on V{track}.")
    return {"track": f"V{track}", "frames": frames}
