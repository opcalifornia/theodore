"""The tools Claude operates Resolve with.

Every tool is a typed, named wrapper over a handful of Resolve API calls,
grouped the way Resolve's own pages are. The agent prefers these over the
generic escape hatch (`call_resolve_api`), because a tool can validate its
input, return something readable, and be tested against the fake Resolve.

How safety works here, in one place
-----------------------------------
Each tool declares a ``kind``:

  read          looks, never changes anything
  navigate      moves the playhead / current timeline (reversible, no data)
  safe_write    adds something new beside the edit (import, bin, render job)
  write         changes the timeline. Auto-checkpointed once per instruction,
                and the result carries a state read-back
  destructive   deletes something on the timeline. Checkpoint + CONFIRMATION
  render        starts rendering. CONFIRMATION (time, disk, can overwrite)

Those two confirmations -- deleting, and starting a render -- are the only
things that ever ask. Source media and projects can't be deleted by any tool
at all, and the escape hatch refuses the methods that would.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from theodore.agent import memory, safety
from theodore.assembly import builder as assembly_builder
from theodore.resolve import connection, timecode

VIDEO, AUDIO = "video", "audio"

READ, NAVIGATE, SAFE_WRITE, WRITE, DESTRUCTIVE, RENDER = (
    "read", "navigate", "safe_write", "write", "destructive", "render",
)
MUTATING_KINDS = (WRITE, DESTRUCTIVE)
CONFIRMED_KINDS = (DESTRUCTIVE, RENDER)

#: Methods the escape hatch will never call, with why.
FORBIDDEN_API = {
    "DeleteProject": "projects are never deleted",
    "CloseProject": "closing a project would drop the connection",
    "DeleteClips": "source media is never deleted (use delete_timeline_items for timeline clips)",
    "DeleteFolders": "bins hold source media; never deleted",
    "Quit": "quitting Resolve would kill the bridge",
    "DeleteRenderJob": "use the render tools",
}


class ToolError(RuntimeError):
    """A tool refused or failed in a way the model should read and adapt to."""


@dataclass
class ToolOutput:
    data: dict
    images: list = field(default_factory=list)  # PNG bytes, shown to the model


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable
    kind: str = READ
    needs_resolve: bool = True  # False: works on files only, runs even with Resolve closed
    needs_timeline: bool = True  # False: works with no project/timeline open (opens or makes one)

    def api_spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.schema}


@dataclass
class AgentContext:
    """Everything a tool call needs, injected so tests can swap Resolve out."""
    connect: Callable = connection.connect
    confirm: Callable = lambda description: False
    project_name: str = ""
    theodore_project: str = ""   # the Theodore registry project, for the editorial engine
    instruction: str = ""
    safety: safety.SafetyState = field(default_factory=safety.SafetyState)
    memory_root: Optional[Path] = None
    scratch_dir: Optional[Path] = None
    mutated: bool = False
    verified_since_mutation: bool = True
    run_cli: Optional[Callable] = None  # injectable for tests
    checkpoint_note: Optional[str] = None

    def handles(self):
        # Fresh every call: after a timeline switch a cached handle would
        # quietly point at the old one. Cheap over the bridge.
        return self.connect()

    def scratch(self) -> Path:
        if self.scratch_dir is None:
            self.scratch_dir = Path(tempfile.mkdtemp(prefix="theodore-agent-"))
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        return self.scratch_dir


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value[k] for k in sorted(value)]
    return list(value)


def _fps(h) -> str:
    return str(h.project.GetSetting("timelineFrameRate") or h.timeline.GetSetting("timelineFrameRate"))


def _track_items(timeline, track_type: str, index: int) -> list:
    fn = getattr(timeline, "GetItemListInTrack", None)
    return _as_list(fn(track_type, index)) if fn else []


def _item_row(item, i: int) -> dict:
    return {"index": i, "name": item.GetName(), "start": item.GetStart(),
            "end": item.GetEnd(), "duration": item.GetDuration()}


def timeline_snapshot(h) -> dict:
    """The read-back attached to every edit, computed here rather than
    trusted to the model: what is actually on the timeline right now."""
    tl = h.timeline
    snap = {"timeline": tl.GetName(), "fps": _fps(h), "start_frame": tl.GetStartFrame()}
    tracks, last_end = {}, tl.GetStartFrame()
    for kind in (VIDEO, AUDIO):
        count = tl.GetTrackCount(kind) or 0
        for idx in range(1, int(count) + 1):
            items = _track_items(tl, kind, idx)
            tracks[f"{kind}{idx}"] = len(items)
            for it in items:
                last_end = max(last_end, it.GetEnd())
    snap["track_item_counts"] = tracks
    snap["duration_frames"] = last_end - tl.GetStartFrame()
    markers = getattr(tl, "GetMarkers", None)
    snap["marker_count"] = len(markers() or {}) if markers else None
    return snap


def _walk_folders(folder, path=""):
    yield path or "/", folder
    for sub in _as_list(folder.GetSubFolderList()):
        yield from _walk_folders(sub, f"{path}/{sub.GetName()}")


def _find_clip(h, name: str):
    pool = h.project.GetMediaPool()
    matches = []
    for _p, folder in _walk_folders(pool.GetRootFolder()):
        for clip in _as_list(folder.GetClipList()):
            if clip.GetName() == name:
                matches.append(clip)
    if not matches:
        raise ToolError(f"No media pool clip named {name!r}. Use list_media_pool to see names.")
    return matches[0]


def _find_timeline(h, name: str):
    count = int(h.project.GetTimelineCount() or 0)
    for i in range(1, count + 1):
        tl = h.project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    raise ToolError(f"No timeline named {name!r}. Use list_timelines.")


# ---------------------------------------------------------------------------
# handlers: project / navigation
# ---------------------------------------------------------------------------

def _project_info(ctx, h):
    if h.project is None:
        return {"project": None, "resolve": h.product, "version": h.version, "studio": h.is_studio,
                "note": "No project open. Use list_projects / open_project."}
    names = []
    count = int(getattr(h.project, "GetTimelineCount", lambda: 0)() or 0)
    for i in range(1, count + 1):
        tl = h.project.GetTimelineByIndex(i)
        if tl:
            names.append(tl.GetName())
    return {"project": h.project.GetName(), "resolve": h.product, "version": h.version,
            "studio": h.is_studio, "current_timeline": h.timeline.GetName() if h.timeline else None,
            "timelines": names, "connected_via": h.via}


def _list_timelines(ctx, h):
    return {"current": h.timeline.GetName() if h.timeline else None,
            "timelines": _project_info(ctx, h).get("timelines", [])}


def _set_current_timeline(ctx, h, name):
    ok = h.project.SetCurrentTimeline(_find_timeline(h, name))
    if ok is False:
        raise ToolError(f"Resolve refused to switch to {name!r}.")
    return {"current_timeline": name}


def _timeline_info(ctx, h):
    return timeline_snapshot(h)


def _list_track_items(ctx, h, track_type, track_index=1):
    rows = [_item_row(it, i) for i, it in enumerate(_track_items(h.timeline, track_type, track_index))]
    return {"track": f"{track_type}{track_index}", "count": len(rows), "items": rows}


def _get_markers(ctx, h):
    markers = h.timeline.GetMarkers() or {}
    return {"markers": [{"frame": f, **{k: v for k, v in m.items() if k != "customData"}}
                        for f, m in sorted(markers.items())]}


def _set_playhead(ctx, h, frame):
    tc = timecode.frames_to_timecode(int(frame), _fps(h))
    if h.timeline.SetCurrentTimecode(tc) is False:
        raise ToolError(f"Resolve would not move the playhead to {tc}.")
    return {"frame": int(frame), "timecode": tc}


def _undo(ctx, h):
    cp = safety.restore_last_checkpoint(h, ctx.safety)
    ctx.mutated, ctx.verified_since_mutation = False, True
    return {"restored": cp.name, "note": "Switched to the checkpoint. The edited timeline "
            f"'{cp.original_name}' still exists, untouched."}


def _remember(ctx, h, key, value, scope="project"):
    if scope == "studio" or h is None or h.project is None:
        memory.set_preference(memory.STUDIO, key, value, ctx.memory_root)
        return {"remembered": {key: value}, "scope": "studio (every project)"}
    memory.set_preference(ctx.project_name or h.project.GetName(), key, value, ctx.memory_root)
    return {"remembered": {key: value}, "scope": "this project"}


# ---------------------------------------------------------------------------
# handlers: media pool
# ---------------------------------------------------------------------------

def _list_media_pool(ctx, h):
    out = []
    for path, folder in _walk_folders(h.project.GetMediaPool().GetRootFolder()):
        out.append({"bin": path, "clips": [c.GetName() for c in _as_list(folder.GetClipList())]})
    return {"bins": out}


def _import_media(ctx, h, paths):
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise ToolError(f"These files don't exist: {missing}")
    items = _as_list(h.project.GetMediaPool().ImportMedia(list(paths)))
    if not items:
        raise ToolError("Resolve imported nothing (unsupported format, or already imported?).")
    return {"imported": [i.GetName() for i in items]}


def _create_bin(ctx, h, name):
    pool = h.project.GetMediaPool()
    folder = pool.AddSubFolder(pool.GetRootFolder(), name)
    if not folder:
        raise ToolError(f"Could not create bin {name!r} (it may already exist).")
    return {"bin": name}


# ---------------------------------------------------------------------------
# handlers: timeline editing
# ---------------------------------------------------------------------------

def _create_timeline(ctx, h, name):
    tl = h.project.GetMediaPool().CreateEmptyTimeline(name)
    if not tl:
        raise ToolError(f"Could not create timeline {name!r} (name taken?).")
    return {"timeline": name}


def _duplicate_timeline(ctx, h, name):
    dup = h.timeline.DuplicateTimeline(name)
    if not dup:
        raise ToolError(f"Could not duplicate as {name!r} (name taken?).")
    return {"duplicate": name, "note": "The original is still the current timeline."}


def _append_clips(ctx, h, clips):
    infos = []
    for c in clips:
        info = {"mediaPoolItem": _find_clip(h, c["clip_name"])}
        for src, dst in (("start_frame", "startFrame"), ("end_frame", "endFrame"),
                         ("track_index", "trackIndex")):
            if c.get(src) is not None:
                info[dst] = int(c[src])
        infos.append(info)
    placed = _as_list(h.project.GetMediaPool().AppendToTimeline(infos))
    if not placed:
        raise ToolError("Resolve appended nothing. Check frame ranges against the clip length.")
    return {"appended": len(placed)}


def _cut_ranges(ctx, h, ranges, new_name=None):
    pairs = [(int(r["start_frame"]), int(r["end_frame"])) for r in ranges]
    if any(e <= s for s, e in pairs):
        raise ToolError("Every range needs end_frame > start_frame.")
    result = assembly_builder.trim_timeline_ranges(h, pairs, new_name=new_name)
    return {"new_timeline": result.timeline_name, "from": result.original_timeline_name,
            "frames_removed": result.frames_removed,
            "clips_before": result.original_clip_count, "clips_after": result.new_clip_count,
            "note": "Built on a DUPLICATE; the original timeline is untouched."}


def _delete_timeline_items(ctx, h, track_type, track_index, item_indices, ripple=False):
    items = _track_items(h.timeline, track_type, track_index)
    try:
        chosen = [items[i] for i in item_indices]
    except IndexError:
        raise ToolError(f"Item index out of range; the track has {len(items)} items.")
    if not h.timeline.DeleteClips(chosen, bool(ripple)):
        raise ToolError("Resolve refused to delete those clips.")
    return {"deleted": [i for i in item_indices], "ripple": bool(ripple)}


def _delete_timeline(ctx, h, name):
    if name.startswith(safety.CHECKPOINT_PREFIX):
        raise ToolError("Checkpoints are what makes undo possible; they are not deleted.")
    if name == h.timeline.GetName():
        raise ToolError("Switch to another timeline first; the open one isn't deleted.")
    tl = _find_timeline(h, name)
    if not h.project.GetMediaPool().DeleteTimelines([tl]):
        raise ToolError(f"Resolve refused to delete {name!r}.")
    return {"deleted_timeline": name}


def _add_marker(ctx, h, frame, color="Blue", name="", note="", duration=1):
    start = h.timeline.GetStartFrame()
    rel = int(frame) - start if int(frame) >= start else int(frame)
    if not h.timeline.AddMarker(rel, color, name, note, int(duration), ""):
        raise ToolError(f"Resolve would not add a marker at frame {frame} "
                        "(one may already be there, or the color is invalid).")
    return {"marker_at": int(frame), "color": color}


def _delete_marker(ctx, h, frame):
    start = h.timeline.GetStartFrame()
    rel = int(frame) - start if int(frame) >= start else int(frame)
    if not h.timeline.DeleteMarkerAtFrame(rel):
        raise ToolError(f"No marker to delete at frame {frame}.")
    return {"deleted_marker": int(frame)}


# ---------------------------------------------------------------------------
# handlers: vision, deliver, escape hatch, theodore CLI
# ---------------------------------------------------------------------------

def _export_frame(ctx, h, frame=None):
    info = {}
    if frame is not None:
        info = _set_playhead(ctx, h, frame)
    path = ctx.scratch() / f"frame-{len(list(ctx.scratch().glob('frame-*.png'))):03d}.png"
    if not h.project.ExportCurrentFrameAsStill(str(path)):
        raise ToolError("Resolve could not export the current frame (is the Edit page showing?).")
    if not path.exists():
        raise ToolError("Resolve reported success but wrote no image file.")
    ctx.verified_since_mutation = True
    return ToolOutput({"frame_exported": info.get("frame"), "timecode": info.get("timecode"),
                       "note": "The image follows. Judge it: is anything black, misframed, or wrong?"},
                      images=[path.read_bytes()])


def _list_render_presets(ctx, h):
    return {"presets": _as_list(h.project.GetRenderPresetList())}


def _queue_render(ctx, h, preset, target_dir, custom_name=None):
    if not h.project.LoadRenderPreset(preset):
        raise ToolError(f"No render preset {preset!r}. Use list_render_presets.")
    settings = {"TargetDir": target_dir}
    if custom_name:
        settings["CustomName"] = custom_name
    if not h.project.SetRenderSettings(settings):
        raise ToolError("Resolve rejected the render settings (does the target folder exist?).")
    job = h.project.AddRenderJob()
    if not job:
        raise ToolError("Resolve would not queue the render job.")
    return {"job_id": job, "preset": preset, "target_dir": target_dir}


def _start_render(ctx, h, job_ids=None):
    ok = h.project.StartRendering(*(job_ids or []))
    if not ok:
        raise ToolError("Resolve would not start rendering.")
    return {"started": True, "jobs": job_ids or "all queued"}


def _render_status(ctx, h, job_id=None):
    out = {"rendering": bool(h.project.IsRenderingInProgress())}
    if job_id:
        out["job"] = h.project.GetRenderJobStatus(job_id)
    return out


_API_TARGETS = {
    "resolve": lambda h: h.resolve,
    "project_manager": lambda h: h.project_manager,
    "project": lambda h: h.project,
    "media_pool": lambda h: h.project.GetMediaPool(),
    "timeline": lambda h: h.timeline,
}


def _call_resolve_api(ctx, h, target, method, args=None, item=None):
    if target == "timeline_item":
        # Per-clip methods: Stabilize, SmartReframe, CreateMagicMask, SetProperty,
        # SetLUT via GetNodeGraph, SetCDL, AddVersion, SetClipColor ...
        if not item or h.timeline is None:
            raise ToolError("timeline_item needs item={track_type, track_index, item_index} "
                            "(indices from list_track_items) and an open timeline.")
        items = _track_items(h.timeline, item.get("track_type", VIDEO), int(item.get("track_index", 1)))
        try:
            clip_obj = items[int(item.get("item_index", 0))]
        except (IndexError, ValueError):
            raise ToolError(f"No item {item} -- the track has {len(items)} items.")
    elif target not in _API_TARGETS:
        raise ToolError(f"target must be one of {sorted(_API_TARGETS) + ['timeline_item']}")
    if method.startswith("_"):
        raise ToolError("Private attributes aren't callable.")
    if method in FORBIDDEN_API:
        raise ToolError(f"{method} is blocked: {FORBIDDEN_API[method]}.")
    if method.startswith(("Delete", "Remove")) and not ctx.confirm(
            f"Call {target}.{method}({json.dumps(args or [])[:200]})"):
        raise ToolError("The editor declined.")
    read_only = method.startswith(("Get", "Is", "Has", "List"))
    if not read_only:
        # Not provably a read: protect it like any other edit.
        safety.ensure_checkpoint(h, ctx.safety, ctx.instruction or method)
        h = ctx.handles()
        ctx.mutated, ctx.verified_since_mutation = True, False
    obj = clip_obj if target == "timeline_item" else _API_TARGETS[target](h)
    if target == "timeline_item" and not read_only:
        obj = _track_items(ctx.handles().timeline, item.get("track_type", VIDEO),
                           int(item.get("track_index", 1)))[int(item.get("item_index", 0))]
    fn = getattr(obj, method, None)
    if fn is None:
        raise ToolError(f"{target} has no method {method!r} in this Resolve build.")
    result = fn(*(args or []))
    if hasattr(result, "GetName"):
        result = {"object": result.GetName()}
    elif isinstance(result, (list, tuple)):
        result = [r.GetName() if hasattr(r, "GetName") else r for r in result]
    return {"result": result}


_THEODORE_READ = {"timeline-status", "dupes", "gaps", "rejects", "find", "peaks", "segments", "ids"}
_THEODORE_EDIT = {"remove-silence", "cut-timeline", "caption-timeline", "trim-timeline"}


def _run_theodore(ctx, h, command, args=None):
    args = [str(a) for a in (args or [])]
    if command not in _THEODORE_READ | _THEODORE_EDIT:
        raise ToolError(f"command must be one of {sorted(_THEODORE_READ | _THEODORE_EDIT)}")
    project = ctx.theodore_project or ctx.project_name or h.project.GetName()
    cmd = [sys.executable, "-m", "theodore.cli", command, "--project", project, *args]
    runner = ctx.run_cli or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=900))
    done = runner(cmd)
    out = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode != 0:
        raise ToolError(out[-1500:] or f"theodore {command} failed ({done.returncode})")
    return {"output": out[-4000:]}


def _theodore_read(ctx, h, command, args=None):
    if command not in _THEODORE_READ:
        raise ToolError(f"read-only commands: {sorted(_THEODORE_READ)}")
    return _run_theodore(ctx, h, command, args)


def _theodore_edit(ctx, h, command, args=None):
    if command not in _THEODORE_EDIT:
        raise ToolError(f"editing commands: {sorted(_THEODORE_EDIT)}")
    return _run_theodore(ctx, h, command, args)


# ---------------------------------------------------------------------------
# handlers: projects
# ---------------------------------------------------------------------------

def _list_projects(ctx, h):
    pm = h.project_manager
    return {"current": h.project.GetName() if h.project else None,
            "folder": pm.GetCurrentFolder() if hasattr(pm, "GetCurrentFolder") else None,
            "projects": _as_list(pm.GetProjectListInCurrentFolder()),
            "folders": _as_list(pm.GetFolderListInCurrentFolder())}


def _open_project(ctx, h, name):
    pm = h.project_manager
    if h.project is not None and h.project.GetName() == name:
        return {"opened": name, "note": "Already open."}
    if h.project is not None:
        pm.SaveProject()  # never lose the editor's work when switching
    project = pm.LoadProject(name)
    if not project:
        raise ToolError(f"Could not open {name!r}. Use list_projects for exact names.")
    ctx.project_name = name
    tl = project.GetCurrentTimeline()
    return {"opened": name, "current_timeline": tl.GetName() if tl else None,
            "note": "The previous project was saved first."}


def _create_project(ctx, h, name):
    pm = h.project_manager
    if h.project is not None:
        pm.SaveProject()
    project = pm.CreateProject(name)
    if not project:
        raise ToolError(f"Could not create project {name!r} (name taken?).")
    ctx.project_name = name
    return {"created": name}


# ---------------------------------------------------------------------------
# handlers: moving clips
# ---------------------------------------------------------------------------

def _move_clips(ctx, h, offset_seconds=None, offset_frames=None, tracks=None, from_seconds=None,
                to_seconds=None, in_place=False, new_name=None):
    from theodore.resolve import timeline_ops
    fps = float(_fps(h) or 24)
    if offset_frames is None:
        if offset_seconds is None:
            raise ToolError("Give offset_seconds (e.g. 60) or offset_frames.")
        offset_frames = round(float(offset_seconds) * fps)
    start = h.timeline.GetStartFrame()
    frm = start + round(float(from_seconds) * fps) if from_seconds is not None else None
    to = start + round(float(to_seconds) * fps) if to_seconds is not None else None
    try:
        res = timeline_ops.move_clips(h, int(offset_frames), tracks=tracks, from_frame=frm, to_frame=to,
                                      on_duplicate=not in_place, new_name=new_name)
    except timeline_ops.TimelineOpError as exc:
        raise ToolError(str(exc))
    out = res.as_dict()
    out["offset_seconds"] = round(res.offset_frames / fps, 3)
    out["note"] = ("Edited the open timeline directly (a checkpoint copy was made first)." if in_place else
                   f"Built on a DUPLICATE '{res.timeline}', now open; '{res.original}' is untouched.")
    return out


# ---------------------------------------------------------------------------
# handlers: Resolve's own tools (transcription, reframing, docs)
# ---------------------------------------------------------------------------

def _transcribe_timeline(ctx, h, detail="sentences"):
    from theodore.resolve import transcribe
    try:
        result = transcribe.transcribe_timeline(h, word_level=True)
    except transcribe.TranscribeError as exc:
        raise ToolError(str(exc))
    out = ctx.scratch() / f"transcript-{len(list(ctx.scratch().glob('transcript-*.json'))):03d}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    start = h.timeline.GetStartFrame()
    sents = [{**x, "timeline_start_frame": start + x["start_frame"], "timeline_end_frame": start + x["end_frame"]}
             for x in result["sentences"]]
    data = {"timeline": result["timeline"], "fps": result["fps"], "words": len(result["words"]),
            "sentences": sents, "saved_to": str(out), "engine": result["engine"],
            "note": "Times are seconds from the timeline start; timeline_*_frame are absolute frames."}
    if detail == "words":
        data["word_list"] = [{**w, "timeline_frame": start + w["start_frame"]} for w in result["words"]]
    return data


def _make_social_version(ctx, h, aspect="9:16", width=None, height=None, name=None, smart_reframe=True):
    from theodore.resolve import timeline_ops
    try:
        return timeline_ops.make_aspect_version(h, aspect, name=name, width=width, height=height,
                                                smart_reframe=smart_reframe)
    except timeline_ops.TimelineOpError as exc:
        raise ToolError(str(exc))


def _resolve_api_docs(ctx, h, query=None, section=None):
    from theodore.resolve import docs
    if section:
        text = docs.section(section)
        if not text:
            raise ToolError(f"No section {section!r}. Try a query instead.")
        return {"section": section, "text": text[:12000]}
    if not query:
        raise ToolError("Give a query (e.g. 'subtitle', 'SetProperty', 'render settings') or a section name.")
    return docs.search(query)


# ---------------------------------------------------------------------------
# handlers: the editorial engine
# ---------------------------------------------------------------------------

def _build_story_cut(ctx, h, target_seconds=60, brief="", clips=None, bin=None, use_timeline=False,
                     name=None):
    from theodore.edit import interview
    try:
        sources = None if use_timeline else interview.find_clips(h, clips, bin)
        return interview.build_story(h, clips=sources, use_timeline=bool(use_timeline), brief=brief,
                                     target_seconds=float(target_seconds), name=name)
    except interview.InterviewError as exc:
        raise ToolError(str(exc))


def _clean_up_speech(ctx, h, max_pause_seconds=0.6, keep_pause_seconds=0.25, fillers=True, repeats=True,
                     preview=False, name=None):
    from theodore.edit import cleanup
    return cleanup.clean_up(h, fillers=fillers, repeats=repeats, max_pause=float(max_pause_seconds),
                            keep_pause=float(keep_pause_seconds), new_name=name, dry_run=bool(preview))


def _place_broll(ctx, h, bin=None, clips=None, coverage=0.4, brief="", in_place=False, name=None):
    from theodore.edit import broll, interview
    try:
        library = interview.find_clips(h, clips, bin)
    except interview.InterviewError as exc:
        raise ToolError(str(exc))
    return broll.place_broll(h, library, coverage=float(coverage), brief=brief,
                             on_duplicate=not in_place, name=name)


def _add_graphic(ctx, h, text, at_seconds, subtext="", style="boxed", seconds=4.0, accent="#E8B04A"):
    from theodore.resolve import graphics
    tl = h.timeline
    fps = float(_fps(h) or 24)
    try:
        clip, frames = graphics.make_graphic(h, style, text, subtext, accent, frames=int(float(seconds) * fps))
        placed = graphics.place(h, ctx.handles().timeline, clip, tl.GetStartFrame() + int(float(at_seconds) * fps),
                                frames, frames)
    except graphics.GraphicsError as exc:
        raise ToolError(str(exc))
    return {"graphic": clip.GetName(), "style": style, "at_seconds": at_seconds,
            "seconds": round(placed["frames"] / fps, 2), "track": placed["track"],
            "note": "Built as a Fusion title, kept in the 'Theodore Graphics' bin; nothing under it moved."}


def _add_transitions(ctx, h, kind="dissolve", seconds=0.5, at_cuts=None, fade_in=False, fade_out=False,
                     name=None):
    from theodore.resolve import transitions
    try:
        return transitions.add_transitions(h, at_cuts=at_cuts, kind=kind, seconds=float(seconds),
                                           head=bool(fade_in), tail=bool(fade_out), name=name)
    except transitions.TransitionError as exc:
        raise ToolError(str(exc))


def _match_look(ctx, h, reference, strength=0.85, per_clip=True, in_place=False, name=None, look_name=None):
    from theodore.color import resolve_apply
    try:
        out = resolve_apply.match_look(h, reference, strength=float(strength), per_clip=bool(per_clip),
                                       on_duplicate=not in_place, name=name, look_name=look_name)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ToolError(str(exc))
    images = []
    if out.get("preview_image"):
        try:
            images = [Path(out["preview_image"]).read_bytes()]
        except OSError:
            pass
    out["preview_note"] = "Image shows reference | before | after for the first clip."
    return ToolOutput(out, images=images)


def _build_multicam(ctx, h, camera_bin=None, cameras=None, lav_bin=None, lavs=None, people=None,
                    camera_for=None, wide=None, name="Panel Multicam"):
    from theodore.edit import interview, panel
    try:
        cams = interview.find_clips(h, cameras, camera_bin)
        lav_clips = interview.find_clips(h, lavs, lav_bin) if (lavs or lav_bin) else []
    except interview.InterviewError as exc:
        raise ToolError(str(exc))
    paths = lambda clips: [c.GetClipProperty("File Path") for c in clips if c.GetClipProperty("File Path")]
    try:
        plan = panel.build_plan(paths(cams), paths(lav_clips), people=people, mapping=camera_for, wide=wide)
        made = panel.import_to_resolve(h, plan, name)
    except panel.PanelError as exc:
        raise ToolError(str(exc))
    return {**made, **panel.summary(plan),
            "note": "A real multicam clip: switch any cut's angle in Resolve's multicam viewer. Lavs are on "
                    "their own audio tracks; camera scratch audio is off."}


def _add_captions(ctx, h, style="social"):
    from theodore.resolve import transcribe
    try:
        return transcribe.add_captions(h, style)
    except transcribe.TranscribeError as exc:
        raise ToolError(str(exc))


# ---------------------------------------------------------------------------
# handlers: studying reference videos (files only, no Resolve)
# ---------------------------------------------------------------------------

def _study_reference(ctx, h, path, name=None, look_at_shots=True):
    from theodore.study import reference
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ToolError(f"No video file at {path!r}.")
    result = reference.study(Path(path), name, use_vision=bool(look_at_shots))
    bp = result["blueprint"]
    return {"name": result["name"], "saved_to": result["dir"], "blueprint": bp,
            "note": "Full shot-by-shot log, transitions, beats and transcript: get_reference."}


def _list_references(ctx, h):
    from theodore.study import reference
    return {"references": reference.list_references()}


_REFERENCE_PARTS = ("blueprint", "shots", "transitions", "music", "speech", "transcript", "pacing")


def _get_reference(ctx, h, name, part="blueprint"):
    from theodore.study import reference
    try:
        ref = reference.load_reference(name)
    except FileNotFoundError as exc:
        raise ToolError(str(exc))
    an = ref["analysis"]
    if part == "blueprint":
        return {"blueprint": ref["blueprint"]}
    if part == "shots":
        log = an.get("shot_log", {})
        return {"fps": an["media"]["fps"], "shots": [
            {**s, "look": log.get(str(s["index"]), {})} for s in an["shots"]]}
    if part == "transitions":
        return {"fps": an["media"]["fps"], "summary": an["transition_summary"],
                "transitions": an["transitions"]}
    if part == "music":
        return {"music": an.get("music"), "energy": an.get("energy"), "loudness": an.get("loudness")}
    if part == "speech":
        return {"speech": an.get("speech")}
    if part == "transcript":
        return {"transcript": an.get("transcript", "")}
    if part == "pacing":
        return {"pacing": an.get("pacing")}
    raise ToolError(f"part must be one of {_REFERENCE_PARTS}")


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


_S, _I = {"type": "string"}, {"type": "integer"}
_TRACK = {"type": "string", "enum": [VIDEO, AUDIO]}
_RANGES = {"type": "array", "items": _obj(
    {"start_frame": _I, "end_frame": _I}, ["start_frame", "end_frame"])}

TOOLS = [
    # project
    Tool("get_project_info", "Project name, Resolve version/edition, current timeline, all timelines.",
         _obj({}), _project_info, READ, needs_timeline=False),
    Tool("list_timelines", "Names of every timeline in the project and which is open.",
         _obj({}), _list_timelines, READ, needs_timeline=False),
    Tool("set_current_timeline", "Open a timeline by name.", _obj({"name": _S}, ["name"]),
         _set_current_timeline, NAVIGATE, needs_timeline=False),
    Tool("undo_last_checkpoint", "Undo: switch back to the checkpoint taken before this session's "
         "first edit. Non-destructive -- the edited timeline stays.", _obj({}), _undo, NAVIGATE),
    Tool("remember_preference", "Store how the editor likes things so it's followed from now on. "
         "scope='studio' = their house style for EVERY project (caption look, lower-third colour, pacing, "
         "naming, delivery habits); scope='project' = just this project (client name, brand colours).",
         _obj({"key": _S, "value": _S, "scope": {"type": "string", "enum": ["project", "studio"]}},
              ["key", "value"]), _remember, READ, needs_timeline=False),
    # media pool
    Tool("list_media_pool", "Every bin and the clip names in it.", _obj({}), _list_media_pool, READ, needs_timeline=False),
    Tool("import_media", "Import files from disk into the media pool.",
         _obj({"paths": {"type": "array", "items": _S}}, ["paths"]), _import_media, SAFE_WRITE, needs_timeline=False),
    Tool("create_bin", "Create a bin in the media pool root.", _obj({"name": _S}, ["name"]),
         _create_bin, SAFE_WRITE, needs_timeline=False),
    # timeline
    Tool("get_timeline_info", "Name, fps, start frame, items per track, duration, marker count. "
         "Use this to check state.", _obj({}), _timeline_info, READ),
    Tool("list_track_items", "Clips on one track: name, start, end, duration (timeline frames).",
         _obj({"track_type": _TRACK, "track_index": _I}, ["track_type"]), _list_track_items, READ),
    Tool("create_timeline", "Create an empty timeline.", _obj({"name": _S}, ["name"]),
         _create_timeline, SAFE_WRITE, needs_timeline=False),
    Tool("duplicate_timeline", "Duplicate the open timeline under a new name.",
         _obj({"name": _S}, ["name"]), _duplicate_timeline, SAFE_WRITE),
    Tool("append_clips", "Append media pool clips to the open timeline. Frames are source frames.",
         _obj({"clips": {"type": "array", "items": _obj(
             {"clip_name": _S, "start_frame": _I, "end_frame": _I, "track_index": _I},
             ["clip_name"])}}, ["clips"]), _append_clips, WRITE),
    Tool("cut_ranges", "Remove timeline-frame ranges from every track and ripple the rest together. "
         "Builds a NEW duplicate timeline; the original is untouched.",
         _obj({"ranges": _RANGES, "new_name": _S}, ["ranges"]), _cut_ranges, WRITE),
    Tool("delete_timeline_items", "Delete clips on a track by index (from list_track_items). "
         "Asks the editor to confirm.",
         _obj({"track_type": _TRACK, "track_index": _I,
               "item_indices": {"type": "array", "items": _I}, "ripple": {"type": "boolean"}},
              ["track_type", "track_index", "item_indices"]), _delete_timeline_items, DESTRUCTIVE),
    Tool("delete_timeline", "Delete a whole timeline (never the open one, never a checkpoint). "
         "Asks the editor to confirm.", _obj({"name": _S}, ["name"]), _delete_timeline, DESTRUCTIVE),
    Tool("set_playhead", "Move the playhead to a timeline frame.", _obj({"frame": _I}, ["frame"]),
         _set_playhead, NAVIGATE),
    # markers
    Tool("get_markers", "Markers on the open timeline.", _obj({}), _get_markers, READ),
    Tool("add_marker", "Add a marker at an absolute timeline frame.",
         _obj({"frame": _I, "color": _S, "name": _S, "note": _S, "duration": _I}, ["frame"]),
         _add_marker, WRITE),
    Tool("delete_marker", "Delete the marker at a timeline frame. Asks the editor to confirm.",
         _obj({"frame": _I}, ["frame"]), _delete_marker, DESTRUCTIVE),
    # vision
    Tool("export_frame", "Look at the viewer: exports the current frame (optionally after moving "
         "the playhead to `frame`) and shows it to you. REQUIRED after every edit.",
         _obj({"frame": _I}), _export_frame, READ),
    # deliver
    Tool("list_render_presets", "Render presets available.", _obj({}), _list_render_presets, READ, needs_timeline=False),
    Tool("queue_render", "Load a preset and queue a render job for the open timeline.",
         _obj({"preset": _S, "target_dir": _S, "custom_name": _S}, ["preset", "target_dir"]),
         _queue_render, SAFE_WRITE),
    Tool("start_render", "Start rendering queued jobs (all, or the given ids). Asks the editor "
         "to confirm.", _obj({"job_ids": {"type": "array", "items": _S}}), _start_render, RENDER),
    Tool("get_render_status", "Whether a render is running, and one job's status.",
         _obj({"job_id": _S}), _render_status, READ, needs_timeline=False),
    # theodore's editorial engine
    Tool("theodore_analyze", "Run a read-only Theodore command: timeline-status, dupes, gaps, "
         "rejects, find, peaks, segments, ids. Extra flags go in `args`.",
         _obj({"command": _S, "args": {"type": "array", "items": _S}}, ["command"]),
         _theodore_read, READ),
    Tool("theodore_edit", "Run a Theodore editing command: remove-silence, cut-timeline, "
         "caption-timeline, trim-timeline. These build a duplicate. cut-timeline only previews "
         "unless `--apply` is in args -- preview first.",
         _obj({"command": _S, "args": {"type": "array", "items": _S}}, ["command"]),
         _theodore_edit, WRITE),
    # projects
    Tool("list_projects", "Projects in the current Project Manager folder, and which is open.",
         _obj({}), _list_projects, READ, needs_timeline=False),
    Tool("open_project", "Open a project by name (saves the current one first).",
         _obj({"name": _S}, ["name"]), _open_project, NAVIGATE, needs_timeline=False),
    Tool("create_project", "Create a new empty project and open it (saves the current one first).",
         _obj({"name": _S}, ["name"]), _create_project, SAFE_WRITE, needs_timeline=False),
    # moving
    Tool("move_clips", "Move clips later (positive) or earlier (negative) on the timeline: all of "
         "them, or only some tracks (tracks: ['V1','A1','video2']), or only clips starting between "
         "from_seconds and to_seconds (timeline seconds from its start). Keeps each clip's source "
         "range, grade, zoom/position/crop/opacity, enabled state, colour label; moves markers too "
         "when everything moves. Default builds a DUPLICATE timeline; in_place=true edits the open "
         "one (after an automatic checkpoint). Titles/generators/Fusion and speed-changed clips "
         "can't be moved from a script -- they are listed in not_moved.",
         _obj({"offset_seconds": {"type": "number"}, "offset_frames": _I,
               "tracks": {"type": "array", "items": _S}, "from_seconds": {"type": "number"},
               "to_seconds": {"type": "number"}, "in_place": {"type": "boolean"}, "new_name": _S}),
         _move_clips, WRITE),
    # the editorial engine
    Tool("build_story_cut", "Cut interview footage into a finished story: Resolve transcribes every "
         "source, Claude (as story editor) picks hook -> arc -> button (-> CTA) to target_seconds, and a "
         "NEW timeline is assembled in story order with section markers. Sources: a bin, clip names, or "
         "use_timeline=true for the open timeline (multi-track, kept in sync). Give a brief: purpose, "
         "audience, tone, must-say lines. Run it again with other targets for :15/:30/:60 versions.",
         _obj({"target_seconds": {"type": "number"}, "brief": _S, "bin": _S,
               "clips": {"type": "array", "items": _S}, "use_timeline": {"type": "boolean"}, "name": _S}),
         _build_story_cut, SAFE_WRITE, needs_timeline=False),
    Tool("clean_up_speech", "Take ums/uhs, stumbles ('I, I') and dead air out of the open timeline "
         "(long pauses shortened to keep_pause_seconds, not removed) on a NEW duplicate, every track "
         "cut together. preview=true lists the cuts without changing anything.",
         _obj({"max_pause_seconds": {"type": "number"}, "keep_pause_seconds": {"type": "number"},
               "fillers": {"type": "boolean"}, "repeats": {"type": "boolean"},
               "preview": {"type": "boolean"}, "name": _S}), _clean_up_speech, SAFE_WRITE),
    Tool("place_broll", "Lay B-roll over the open timeline: Claude looks at every clip in the bin "
         "(cached), reads the cut's transcript, and places cutaways that match the words on the first "
         "free track above (video only -- the interview sound stays). coverage = share of runtime "
         "(0.3-0.6). On a duplicate unless in_place.",
         _obj({"bin": _S, "clips": {"type": "array", "items": _S}, "coverage": {"type": "number"},
               "brief": _S, "in_place": {"type": "boolean"}, "name": _S}), _place_broll, SAFE_WRITE),
    Tool("add_graphic", "Add a designed title or lower third at at_seconds (timeline seconds) on a "
         "free track above the picture -- never inserted into the edit. style: boxed (plate + accent "
         "bar lower third), clean (text + accent line), title_card (big centred over picture), "
         "title_card_solid (on a dark card). Eased fade/slide in and out. Max ~5 s each. accent: hex colour.",
         _obj({"text": _S, "subtext": _S, "at_seconds": {"type": "number"}, "seconds": {"type": "number"},
               "style": {"type": "string", "enum": ["boxed", "clean", "title_card", "title_card_solid"]},
               "accent": _S}, ["text", "at_seconds"]), _add_graphic, WRITE),
    Tool("add_transitions", "Add transitions -- dissolve, dip_to_black, dip_to_white -- at cuts of "
         "the main track (at_cuts: [1,3] = 1st and 3rd cut; omit for every cut), optionally fade_in / "
         "fade_out at the ends. Builds a NEW timeline via FCPXML (Resolve has no script call for "
         "transitions). Clips need handles. Do this BEFORE graphics/colour: those don't survive the trip.",
         _obj({"kind": {"type": "string", "enum": ["dissolve", "dip_to_black", "dip_to_white"]},
               "seconds": {"type": "number"}, "at_cuts": {"type": "array", "items": _I},
               "fade_in": {"type": "boolean"}, "fade_out": {"type": "boolean"}, "name": _S}),
         _add_transitions, SAFE_WRITE),
    Tool("match_look", "Grade the open timeline to match the look of a reference image (a still from any "
         "film, a photo, a grade the editor likes): each clip gets its own LUT computed from its own pixels "
         "-- tone curve (black lift, contrast, roll-off), split-toned colour by shadows/mids/highlights, "
         "saturation -- so every shot lands on the same look. On a DUPLICATE unless in_place. strength "
         "0.5 subtle .. 1 full. per_clip=false uses one shared LUT. Returns a reference|before|after image.",
         _obj({"reference": _S, "strength": {"type": "number"}, "per_clip": {"type": "boolean"},
               "in_place": {"type": "boolean"}, "name": _S, "look_name": _S}, ["reference"]),
         _match_look, SAFE_WRITE),
    Tool("build_multicam", "Multicam panel/interview, done end to end: syncs every camera and lav from "
         "their audio (no timecode needed), works out who is talking from the lavs, which camera is on whom "
         "(from on-camera motion, or camera_for={'Daniel': 'Cam B'}), and cuts it like a panel editor -- "
         "speaker's angle, wide for cross-talk/openings/long answers, no shot under 1.5 s. Imported as a REAL "
         "multicam clip + cut timeline with every lav on its own track, so any cut's angle can be switched "
         "in Resolve. people = names for the lavs in order (else from file names).",
         _obj({"camera_bin": _S, "cameras": {"type": "array", "items": _S}, "lav_bin": _S,
               "lavs": {"type": "array", "items": _S}, "people": {"type": "array", "items": _S},
               "camera_for": {"type": "object"}, "wide": _S, "name": _S}),
         _build_multicam, SAFE_WRITE, needs_timeline=False),
    Tool("add_captions", "Caption the open timeline with Resolve's own speech-to-text: a new subtitle "
         "track. style social (short single lines for 9:16/muted autoplay), broadcast, netflix.",
         _obj({"style": {"type": "string", "enum": ["social", "broadcast", "netflix"]}}), _add_captions, WRITE),
    # Resolve's own tools
    Tool("transcribe_timeline", "Transcribe the open timeline with Resolve's built-in speech-to-text "
         "(free, local, word-accurate timing, keeps ums/uhs). Runs on a throwaway copy -- the timeline "
         "is untouched. Returns sentences with times; detail='words' adds every word.",
         _obj({"detail": {"type": "string", "enum": ["sentences", "words"]}}), _transcribe_timeline, READ),
    Tool("make_social_version", "Build a social version of the open timeline at a new shape -- "
         "aspect 9:16 (Reels/TikTok/Shorts), 4:5 (feed), 1:1, or width/height -- on a DUPLICATE, with "
         "Resolve's Smart Reframe keeping the subject in frame on every clip. Then export_frame a few "
         "moments to check framing; titles/graphics need repositioning by hand or by you.",
         _obj({"aspect": _S, "width": _I, "height": _I, "name": _S, "smart_reframe": {"type": "boolean"}}),
         _make_social_version, WRITE),
    Tool("resolve_api_docs", "Look up what this Resolve's scripting API can do -- methods, setting "
         "keys, constants -- from its own documentation. query: words to search; or section: a whole "
         "object ('TimelineItem', 'Timeline', 'MediaPool', 'Project', 'Graph') or settings section. "
         "Use before call_resolve_api for anything the named tools don't cover.",
         _obj({"query": _S, "section": _S}), _resolve_api_docs, READ, needs_resolve=False),
    # reference videos
    Tool("study_reference", "Study a finished video file (the editor's reference): finds every "
         "cut and transition (dissolve, fade, wipe, flash, stinger), pacing, tempo and beat sync, "
         "what's said, and -- looking at every shot -- shot sizes, A/B-roll, overlays, titles, "
         "look. Returns an edit blueprint with a recipe to replicate it. Takes ~1 min per minute "
         "of video. Use whenever the editor gives you a video to learn from or copy.",
         _obj({"path": _S, "name": _S, "look_at_shots": {"type": "boolean"}}, ["path"]),
         _study_reference, READ, needs_resolve=False),
    Tool("list_references", "Reference videos already studied, by name.", _obj({}),
         _list_references, READ, needs_resolve=False),
    Tool("get_reference", "A studied reference: its blueprint, or the detail behind it -- "
         "part = blueprint | shots (every shot with timing, transition in/out, size, role, "
         "on-screen text, overlays) | transitions | music (tempo, beats, sync) | speech | "
         "transcript | pacing.",
         _obj({"name": _S, "part": {"type": "string", "enum": list(_REFERENCE_PARTS)}}, ["name"]),
         _get_reference, READ, needs_resolve=False),
    # escape hatch
    Tool("call_resolve_api", "ESCAPE HATCH: call any Resolve API method directly. Prefer the "
         "specific tools above; look methods up with resolve_api_docs first. targets: resolve, "
         "project_manager, project, media_pool, timeline, timeline_item (one clip: pass item = "
         "{track_type, track_index, item_index} from list_track_items -- e.g. Stabilize, "
         "SmartReframe, CreateMagicMask, SetProperty, SetClipColor, SetCDL, AddVersion).",
         _obj({"target": _S, "method": _S, "args": {"type": "array"},
               "item": _obj({"track_type": _TRACK, "track_index": _I, "item_index": _I})},
              ["target", "method"]),
         _call_resolve_api, NAVIGATE, needs_timeline=False),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def execute(tool: Tool, ctx: AgentContext, args: dict) -> ToolOutput:
    """Run one tool with all the safety rails. Never raises: a failure comes
    back as ``{"ok": False, "error": ...}`` for the model to read."""
    try:
        h = ctx.handles() if tool.needs_resolve else None
        if h is not None and tool.needs_timeline and (h.project is None or h.timeline is None):
            raise ToolError(
                "No timeline is open" + (f" in '{h.project.GetName()}'" if h.project else " (no project open)")
                + ". Open one first: list_projects / open_project, then list_timelines / "
                "set_current_timeline, or create_timeline.")
        if tool.kind in CONFIRMED_KINDS:
            if not ctx.confirm(f"{tool.name}({json.dumps(args)[:300]})"):
                return ToolOutput({"ok": False, "error": "The editor declined this action."})
        cp = None
        if tool.kind in MUTATING_KINDS:
            cp = safety.ensure_checkpoint(h, ctx.safety, ctx.instruction or tool.name)
            h = ctx.handles()
        out = tool.handler(ctx, h, **args)
        out = out if isinstance(out, ToolOutput) else ToolOutput(out)
        out.data = {"ok": True, **out.data}
        if tool.kind in MUTATING_KINDS:
            if cp:
                out.data["checkpoint_created"] = cp.name
            ctx.mutated, ctx.verified_since_mutation = True, False
            out.data["state_after"] = timeline_snapshot(ctx.handles())
        return out
    except (ToolError, safety.SafetyError) as exc:
        return ToolOutput({"ok": False, "error": str(exc)})
    except TypeError as exc:
        return ToolOutput({"ok": False, "error": f"Bad arguments for {tool.name}: {exc}"})
    except Exception as exc:  # noqa: BLE001 -- the model should see it, not crash the run
        return ToolOutput({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
