"""Timeline operations Resolve's scripting API has no single call for.

Resolve can't *move* a clip from a script: there is no SetStart, no "nudge",
no ripple-insert of a gap. What it can do is place a media pool clip at an
exact record frame on an exact track, copy a grade from one clip to another,
copy clip properties, and delete. So a move here is: re-place each clip at its
new frame with the same source range, give it the original's grade and
properties, then remove the original -- on a duplicate timeline by default, so
the editor's timeline is never at risk.

Ordering matters: moving right, clips are re-placed right-to-left, so a new
position never lands on a clip that hasn't moved yet (and moving left, the
reverse).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("theodore.resolve.timeline_ops")

VIDEO, AUDIO, SUBTITLE = "video", "audio", "subtitle"


class TimelineOpError(RuntimeError):
    pass


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value[k] for k in sorted(value)]
    return list(value)


def _items(tl, kind: str, idx: int) -> list:
    return _as_list(tl.GetItemListInTrack(kind, idx))


def _tracks(tl, kinds=(VIDEO, AUDIO, SUBTITLE)) -> list[tuple[str, int]]:
    out = []
    for kind in kinds:
        try:
            count = int(tl.GetTrackCount(kind) or 0)
        except Exception:  # noqa: BLE001 -- older builds lack subtitle tracks
            count = 0
        out += [(kind, i) for i in range(1, count + 1)]
    return out


def parse_tracks(spec: Optional[list]) -> Optional[set]:
    """["video1", "audio2", "video"] -> {("video",1), ("audio",2), ("video",None)}."""
    if not spec:
        return None
    out = set()
    for s in spec:
        s = str(s).strip().lower().replace(" ", "")
        for kind in (VIDEO, AUDIO, SUBTITLE):
            if s.startswith(kind):
                rest = s[len(kind):]
                out.add((kind, int(rest) if rest.isdigit() else None))
                break
        else:
            if s[:1] in "va" and s[1:].isdigit():
                out.add((VIDEO if s[0] == "v" else AUDIO, int(s[1:])))
            else:
                raise TimelineOpError(f"Unknown track {s!r}; use e.g. video1, audio2, V1, A2.")
    return out


def _wanted(track: tuple[str, int], spec: Optional[set]) -> bool:
    return spec is None or track in spec or (track[0], None) in spec


def duplicate_for_edit(h, name: Optional[str], suffix: str):
    """Duplicate the current timeline, make it current, return it."""
    original = h.timeline
    base = name or f"{original.GetName()} - {suffix}"
    candidate, n = base, 2
    while True:
        dup = original.DuplicateTimeline(candidate)
        if dup:
            break
        n += 1
        if n > 50:
            raise TimelineOpError(f"Could not duplicate '{original.GetName()}' (name {base!r} taken?).")
        candidate = f"{base} {n}"
    if h.project.SetCurrentTimeline(dup) is False:
        raise TimelineOpError(f"Made '{candidate}' but Resolve would not switch to it.")
    return dup


@dataclass
class _Snap:
    item: object
    kind: str
    index: int
    start: int
    end: int
    duration: int
    src_in: int
    src_out: int
    mpi: object
    props: dict = field(default_factory=dict)
    enabled: Optional[bool] = None
    color: Optional[str] = None


def _placed_item(appended):
    """The clip AppendToTimeline placed, or None. A refused placement can come
    back as a list holding a dead item rather than an empty list."""
    for item in _as_list(appended):
        try:
            if item is not None and item.GetDuration() is not None:
                return item
        except Exception:  # noqa: BLE001
            continue
    return None


def _snapshot(item, kind, index) -> _Snap:
    def safe(fn, *a):
        f = getattr(item, fn, None)
        try:
            return f(*a) if f else None
        except Exception:  # noqa: BLE001 -- best effort per attribute
            return None
    props = safe("GetProperty")
    return _Snap(
        item=item, kind=kind, index=index, start=int(item.GetStart()), end=int(item.GetEnd()),
        duration=int(item.GetDuration()), src_in=int(safe("GetSourceStartFrame") or 0),
        src_out=int(safe("GetSourceEndFrame") or 0), mpi=safe("GetMediaPoolItem"),
        props=props if isinstance(props, dict) else {}, enabled=safe("GetClipEnabled"),
        color=safe("GetClipColor"),
    )


#: Properties that describe the clip's place rather than its look -- never copied.
_SKIP_PROPS = {"Start", "End", "Duration"}


def _restore(new, snap: _Snap) -> list[str]:
    misses = []
    for key, value in snap.props.items():
        if key in _SKIP_PROPS or value is None:
            continue
        try:
            current = new.GetProperty(key)
            if current != value and new.SetProperty(key, value) is False:
                misses.append(key)
        except Exception:  # noqa: BLE001
            misses.append(key)
    if snap.enabled is False:
        try:
            new.SetClipEnabled(False)
        except Exception:  # noqa: BLE001
            misses.append("enabled")
    if snap.color:
        try:
            new.SetClipColor(snap.color)
        except Exception:  # noqa: BLE001
            misses.append("clip color")
    return misses


@dataclass
class MoveResult:
    timeline: str
    original: str
    offset_frames: int
    moved: int = 0
    skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    markers_moved: int = 0
    end_inclusive: Optional[bool] = None

    def as_dict(self) -> dict:
        return {"timeline": self.timeline, "from": self.original, "offset_frames": self.offset_frames,
                "clips_moved": self.moved, "not_moved": self.skipped, "warnings": self.warnings,
                "markers_moved": self.markers_moved}


def move_clips(h, offset_frames: int, *, tracks: Optional[list] = None,
               from_frame: Optional[int] = None, to_frame: Optional[int] = None,
               on_duplicate: bool = True, new_name: Optional[str] = None,
               move_markers: Optional[bool] = None) -> MoveResult:
    """Move every clip (optionally: on these tracks, starting in [from, to))
    by ``offset_frames``. Positive = later/right."""
    offset = int(offset_frames)
    if offset == 0:
        raise TimelineOpError("Offset is 0 -- nothing to move.")
    spec = parse_tracks(tracks)
    original_name = h.timeline.GetName()
    tl = duplicate_for_edit(h, new_name, f"moved {'+' if offset > 0 else ''}{offset}f") if on_duplicate else h.timeline
    result = MoveResult(timeline=tl.GetName(), original=original_name, offset_frames=offset)
    tl_start = int(tl.GetStartFrame())

    def selected(s: _Snap) -> bool:
        return ((from_frame is None or s.start >= int(from_frame))
                and (to_frame is None or s.start < int(to_frame)))

    plan: dict[tuple[str, int], list[_Snap]] = {}
    for kind, idx in _tracks(tl):
        if not _wanted((kind, idx), spec):
            continue
        snaps = [_snapshot(it, kind, idx) for it in _items(tl, kind, idx)]
        moving = [s for s in snaps if selected(s)]
        staying = [s for s in snaps if not selected(s)]
        movable = []
        for s in moving:
            label = f"{kind}{idx} '{s.item.GetName()}' at {s.start}"
            if s.mpi is None:
                result.skipped.append(f"{label}: a title/generator/Fusion/subtitle item -- "
                                      "Resolve can't re-place it from a script")
                staying.append(s)
            elif s.src_out - s.src_in not in (s.duration, s.duration - 1, s.duration + 1):
                result.skipped.append(f"{label}: speed-changed clip -- moving it would lose the retime")
                staying.append(s)
            else:
                movable.append(s)
        if offset < 0:
            for s in movable:
                if s.start + offset < tl_start:
                    raise TimelineOpError(f"{kind}{idx} '{s.item.GetName()}' would move before the "
                                          "timeline start. Use a smaller offset.")
        for s in movable:
            ns, ne = s.start + offset, s.end + offset
            for o in staying:
                if ns < o.end and o.start < ne:
                    raise TimelineOpError(
                        f"{kind}{idx}: '{s.item.GetName()}' would land on '{o.item.GetName()}', which "
                        "isn't moving. Move everything after it too, or choose another offset.")
        if movable:
            plan[(kind, idx)] = sorted(movable, key=lambda s: s.start, reverse=offset > 0)

    if not plan:
        raise TimelineOpError("No clips matched -- nothing moved." +
                              (f" Not movable: {result.skipped}" if result.skipped else ""))

    pool = h.project.GetMediaPool()
    state = {"end_adjust": None, "temp_track": None}
    placed: list[tuple[_Snap, object]] = []

    def append(s: _Snap, track: int, record: int):
        info = {"mediaPoolItem": s.mpi, "startFrame": s.src_in,
                "endFrame": s.src_out + (state["end_adjust"] or 0),
                "recordFrame": record, "trackIndex": track,
                "mediaType": 1 if s.kind == VIDEO else 2}
        new = _placed_item(pool.AppendToTimeline([info]))
        if new is None:
            raise TimelineOpError(
                f"Resolve refused to place '{s.item.GetName()}' at frame {record} on {s.kind}{track}. "
                f"{result.moved} clip(s) were already moved on '{tl.GetName()}'"
                + ("; your original timeline is untouched." if on_duplicate else "."))
        if state["end_adjust"] is None:
            # Resolve has counted AppendToTimeline's endFrame (and reported
            # GetSourceEndFrame) both ways across versions: measure it on the
            # first clip, re-place that clip if it's off, and remember.
            delta = int(new.GetDuration()) - s.duration
            state["end_adjust"] = -delta if abs(delta) == 1 else 0
            result.end_inclusive = delta == 1
            if state["end_adjust"]:
                tl.DeleteClips([new], False)
                return append(s, track, record)
        return new

    def carry_look(source, s: _Snap, new):
        if s.kind == VIDEO:
            try:
                source.CopyGrades([new])
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"grade not copied for '{s.item.GetName()}': {exc}")
        misses = _restore(new, s)
        if misses:
            result.warnings.append(f"'{s.item.GetName()}': could not restore {', '.join(misses)}")

    for (kind, idx), snaps in plan.items():
        for s in snaps:
            record = s.start + offset
            overlaps_itself = abs(offset) < s.duration
            if not overlaps_itself:
                new = append(s, idx, record)
                carry_look(s.item, s, new)
                if not tl.DeleteClips([s.item], False):
                    result.warnings.append(f"original '{s.item.GetName()}' at {s.start} could not be removed")
            elif kind == AUDIO:
                # Moving by less than its own length: the new spot overlaps the
                # clip itself. Audio carries no grade, so remove, then place.
                if not tl.DeleteClips([s.item], False):
                    raise TimelineOpError(f"Could not lift '{s.item.GetName()}' to move it.")
                new = append(s, idx, record)
                _restore(new, s)
            else:
                # Video overlapping itself: park a copy on a spare track to hold
                # the grade, lift the original, place the clip, take the grade
                # from the parked copy, then clear it away.
                if state["temp_track"] is None:
                    tl.AddTrack("video")
                    state["temp_track"] = int(tl.GetTrackCount("video"))
                parked = append(s, state["temp_track"], record)
                carry_look(s.item, s, parked)
                if not tl.DeleteClips([s.item], False):
                    tl.DeleteClips([parked], False)
                    raise TimelineOpError(f"Could not lift '{s.item.GetName()}' to move it.")
                new = append(s, idx, record)
                carry_look(parked, s, new)
                tl.DeleteClips([parked], False)
            placed.append((s, new))
            result.moved += 1

    if state["temp_track"] is not None:
        try:
            tl.DeleteTrack("video", state["temp_track"])
        except Exception:  # noqa: BLE001
            result.warnings.append("an empty helper video track was left at the top")

    # Video and audio of the same take moved separately; link them back.
    by_key: dict = {}
    for s, new in placed:
        by_key.setdefault((id(s.mpi), s.start, s.src_in), []).append((s.kind, new))
    for group in by_key.values():
        kinds = {k for k, _ in group}
        if VIDEO in kinds and AUDIO in kinds:
            try:
                tl.SetClipsLinked([n for _, n in group], True)
            except Exception:  # noqa: BLE001
                pass

    whole_tracks = spec is None and from_frame is None and to_frame is None
    if move_markers if move_markers is not None else whole_tracks:
        result.markers_moved = _move_markers(tl, offset, from_frame, to_frame, tl_start)

    _verify(tl, placed, offset, result)
    return result


def _move_markers(tl, offset, from_frame, to_frame, tl_start) -> int:
    markers = tl.GetMarkers() or {}
    moved = 0
    order = sorted(markers.items(), reverse=offset > 0)
    for frame, m in order:
        absolute = int(frame) + tl_start
        if from_frame is not None and absolute < int(from_frame):
            continue
        if to_frame is not None and absolute >= int(to_frame):
            continue
        if tl.DeleteMarkerAtFrame(frame):
            if tl.AddMarker(int(frame) + offset, m.get("color", "Blue"), m.get("name", ""),
                            m.get("note", ""), int(m.get("duration", 1) or 1), m.get("customData", "")):
                moved += 1
    return moved


def _verify(tl, placed, offset, result: MoveResult) -> None:
    present = {}
    for kind, idx in _tracks(tl, (VIDEO, AUDIO)):
        present[(kind, idx)] = [(int(i.GetStart()), int(i.GetDuration())) for i in _items(tl, kind, idx)]
    for s, _new in placed:
        want = (s.start + offset, s.duration)
        if want not in present.get((s.kind, s.index), []):
            near = [p for p in present.get((s.kind, s.index), []) if abs(p[0] - want[0]) <= 1]
            result.warnings.append(
                f"{s.kind}{s.index} '{s.item.GetName()}': expected at {want[0]} for {want[1]}f, "
                f"found {near or 'nothing there'}")


# ---------------------------------------------------------------------------
# Social versions: same edit, new shape
# ---------------------------------------------------------------------------

ASPECTS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def make_aspect_version(h, aspect: str = "9:16", *, name: Optional[str] = None,
                        width: Optional[int] = None, height: Optional[int] = None,
                        smart_reframe: bool = True) -> dict:
    """Duplicate the open timeline at a new frame size and let Resolve's
    Smart Reframe (Studio) keep the subject in frame on every video clip.

    "Scale full frame with crop" is set so horizontal footage fills a vertical
    frame instead of shrinking into a letterbox; Smart Reframe then moves the
    crop window onto the action, clip by clip.
    """
    if width is None or height is None:
        if aspect not in ASPECTS:
            raise TimelineOpError(f"aspect must be one of {sorted(ASPECTS)} or give width and height.")
        width, height = ASPECTS[aspect]
    label = aspect if aspect in ASPECTS else f"{width}x{height}"
    original_name = h.timeline.GetName()
    tl = duplicate_for_edit(h, name, label.replace(":", "x"))
    settings = [("useCustomSettings", "1"),
                ("timelineResolutionWidth", str(width)), ("timelineResolutionHeight", str(height)),
                ("timelineOutputResolutionWidth", str(width)), ("timelineOutputResolutionHeight", str(height)),
                ("timelineInputResMismatchBehavior", "scaleToCrop")]
    failed = [k for k, v in settings if not tl.SetSetting(k, v)]
    if any(k.startswith("timelineResolution") for k in failed):
        raise TimelineOpError(f"Resolve would not set '{tl.GetName()}' to {width}x{height} "
                              f"({', '.join(failed)}).")
    reframed, not_reframed, overlays = 0, [], []
    for kind, idx in _tracks(tl, (VIDEO,)):
        for it in _items(tl, kind, idx):
            if it.GetMediaPoolItem() is None:
                overlays.append(f"V{idx} '{it.GetName()}' at {it.GetStart()}")
                continue
            if not smart_reframe:
                continue
            try:
                ok = it.SmartReframe()
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                reframed += 1
            else:
                not_reframed.append(f"V{idx} '{it.GetName()}' at {it.GetStart()}")
    out = {"timeline": tl.GetName(), "from": original_name, "resolution": f"{width}x{height}",
           "clips_smart_reframed": reframed, "not_reframed": not_reframed,
           "settings_not_applied": failed}
    if overlays:
        out["check_these"] = overlays
        out["note"] = ("Titles/graphics/generators keep their 16:9 layout -- reposition or resize "
                       "them for the new frame.")
    return out
