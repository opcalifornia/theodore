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

    def api_spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.schema}


@dataclass
class AgentContext:
    """Everything a tool call needs, injected so tests can swap Resolve out."""
    connect: Callable = connection.connect
    confirm: Callable = lambda description: False
    project_name: str = ""
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
    names = []
    count = int(getattr(h.project, "GetTimelineCount", lambda: 0)() or 0)
    for i in range(1, count + 1):
        tl = h.project.GetTimelineByIndex(i)
        if tl:
            names.append(tl.GetName())
    return {"project": h.project.GetName(), "resolve": h.product, "version": h.version,
            "studio": h.is_studio, "current_timeline": h.timeline.GetName(),
            "timelines": names, "connected_via": h.via}


def _list_timelines(ctx, h):
    return {"current": h.timeline.GetName(), "timelines": _project_info(ctx, h)["timelines"]}


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


def _remember(ctx, h, key, value):
    memory.set_preference(ctx.project_name or h.project.GetName(), key, value, ctx.memory_root)
    return {"remembered": {key: value}}


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


def _call_resolve_api(ctx, h, target, method, args=None):
    if target not in _API_TARGETS:
        raise ToolError(f"target must be one of {sorted(_API_TARGETS)}")
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
    obj = _API_TARGETS[target](h)
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
    project = ctx.project_name or h.project.GetName()
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
         _obj({}), _project_info, READ),
    Tool("list_timelines", "Names of every timeline in the project and which is open.",
         _obj({}), _list_timelines, READ),
    Tool("set_current_timeline", "Open a timeline by name.", _obj({"name": _S}, ["name"]),
         _set_current_timeline, NAVIGATE),
    Tool("undo_last_checkpoint", "Undo: switch back to the checkpoint taken before this session's "
         "first edit. Non-destructive -- the edited timeline stays.", _obj({}), _undo, NAVIGATE),
    Tool("remember_preference", "Store an editor preference for this project (caption style, "
         "render preset, naming) so it's followed in future sessions.",
         _obj({"key": _S, "value": _S}, ["key", "value"]), _remember, READ),
    # media pool
    Tool("list_media_pool", "Every bin and the clip names in it.", _obj({}), _list_media_pool, READ),
    Tool("import_media", "Import files from disk into the media pool.",
         _obj({"paths": {"type": "array", "items": _S}}, ["paths"]), _import_media, SAFE_WRITE),
    Tool("create_bin", "Create a bin in the media pool root.", _obj({"name": _S}, ["name"]),
         _create_bin, SAFE_WRITE),
    # timeline
    Tool("get_timeline_info", "Name, fps, start frame, items per track, duration, marker count. "
         "Use this to check state.", _obj({}), _timeline_info, READ),
    Tool("list_track_items", "Clips on one track: name, start, end, duration (timeline frames).",
         _obj({"track_type": _TRACK, "track_index": _I}, ["track_type"]), _list_track_items, READ),
    Tool("create_timeline", "Create an empty timeline.", _obj({"name": _S}, ["name"]),
         _create_timeline, SAFE_WRITE),
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
    Tool("list_render_presets", "Render presets available.", _obj({}), _list_render_presets, READ),
    Tool("queue_render", "Load a preset and queue a render job for the open timeline.",
         _obj({"preset": _S, "target_dir": _S, "custom_name": _S}, ["preset", "target_dir"]),
         _queue_render, SAFE_WRITE),
    Tool("start_render", "Start rendering queued jobs (all, or the given ids). Asks the editor "
         "to confirm.", _obj({"job_ids": {"type": "array", "items": _S}}), _start_render, RENDER),
    Tool("get_render_status", "Whether a render is running, and one job's status.",
         _obj({"job_id": _S}), _render_status, READ),
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
    # escape hatch
    Tool("call_resolve_api", "ESCAPE HATCH: call any Resolve API method directly. Prefer the "
         "specific tools above; use this only for what they don't cover. targets: resolve, "
         "project_manager, project, media_pool, timeline.",
         _obj({"target": _S, "method": _S, "args": {"type": "array"}}, ["target", "method"]),
         _call_resolve_api, NAVIGATE),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def execute(tool: Tool, ctx: AgentContext, args: dict) -> ToolOutput:
    """Run one tool with all the safety rails. Never raises: a failure comes
    back as ``{"ok": False, "error": ...}`` for the model to read."""
    try:
        h = ctx.handles()
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
