"""Get audio or a small proxy of a media pool clip OUT of Resolve.

Formats ffmpeg can't read (RED R3D, ARRIRAW) still need syncing and analysis. Resolve can decode them, so
these helpers render through Resolve on a throwaway timeline (deleted afterwards) and give back an ordinary
file that ffmpeg understands.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Optional


class ExportError(RuntimeError):
    pass


def _codec_for(project, fmt: str, want: tuple) -> str:
    codecs = project.GetRenderCodecs(fmt) or {}
    for label, code in codecs.items():
        if any(w == code.lower() or w in label.lower().replace(" ", "") for w in want):
            return code
    raise ExportError(f"Resolve has no {want} codec for {fmt!r} (offers {list(codecs.values())}).")


def _render(h, clip, settings: dict, fmt: str, codec_hint: tuple, out_dir: Path, name: str, timeout: float,
            frames: Optional[tuple] = None) -> Path:
    try:
        return _render_once(h, clip, settings, fmt, codec_hint, out_dir, name, timeout, frames)
    except ExportError as exc:
        if "wrote no file" not in str(exc):
            raise
        time.sleep(2)   # Resolve sometimes drops the first render after the project changes: once more
        return _render_once(h, clip, settings, fmt, codec_hint, out_dir, name, timeout, frames)


def _render_once(h, clip, settings: dict, fmt: str, codec_hint: tuple, out_dir: Path, name: str, timeout: float,
                 frames: Optional[tuple] = None) -> Path:
    project = h.project
    pool = project.GetMediaPool()
    page = h.resolve.GetCurrentPage()
    original = project.GetCurrentTimeline()
    label = f"_theodore_export_{int(time.time() * 1000)}"
    if frames:
        # Only a stretch of the clip: [start_frame, end_frame) in the clip's own frame numbers.
        temp = pool.CreateEmptyTimeline(label)
        if temp:
            project.SetCurrentTimeline(temp)
            if not pool.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": int(frames[0]),
                                           "endFrame": int(frames[1])}]):
                pool.DeleteTimelines([temp])
                temp = None
    else:
        temp = pool.CreateTimelineFromClips(label, [clip])
    if not temp:
        raise ExportError(f"Could not make a temporary timeline for {clip.GetName()!r}.")
    job = None
    status = None
    try:
        project.SetCurrentTimeline(temp)
        formats = project.GetRenderFormats() or {}
        code = next((c for label, c in formats.items() if c == fmt or label.lower() == fmt), None)
        if code is None:
            raise ExportError(f"Resolve doesn't offer the {fmt!r} render format (has {list(formats.values())}).")
        if not project.SetCurrentRenderFormatAndCodec(code, _codec_for(project, code, codec_hint)):
            raise ExportError("Resolve rejected the render format/codec.")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(out_dir), "CustomName": name,
                                          **settings}):
            raise ExportError("Resolve rejected the render settings.")
        job = project.AddRenderJob()
        if not job:
            raise ExportError("Resolve would not queue the render.")
        project.StartRendering([job], isInteractiveMode=False)
        start = time.time()
        while project.IsRenderingInProgress():
            if time.time() - start > timeout:
                project.StopRendering()
                raise ExportError(f"Rendering {clip.GetName()!r} took longer than {timeout:.0f}s.")
            time.sleep(1)
        status = project.GetRenderJobStatus(job) if hasattr(project, "GetRenderJobStatus") else None
    finally:
        if job:
            try:
                project.DeleteRenderJob(job)
            except Exception:  # noqa: BLE001
                pass
        if original:
            project.SetCurrentTimeline(original)
        try:
            pool.DeleteTimelines([temp])
        except Exception:  # noqa: BLE001
            pass
        if page:
            h.resolve.OpenPage(page)
    files = sorted(out_dir.glob(f"{name}*"))
    if not files:
        raise ExportError(f"Resolve finished but wrote no file (job status: {status}).")
    return files[0]


def render_audio(h, clip, out_dir: Optional[Path] = None, name: Optional[str] = None, timeout: float = 3600) -> Path:
    """The clip's audio as a WAV (all its channels mixed the way Resolve mixes a timeline)."""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="theodore-audio-"))
    # Resolve's "Wave" format lists no codecs, so render a QuickTime with the picture off and PCM sound:
    # ffmpeg reads the audio from it like any WAV.
    return _render(h, clip, {"ExportVideo": False, "ExportAudio": True, "AudioCodec": "lpcm", "AudioBitDepth": 16,
                             "AudioSampleRate": 48000}, "mov", ("proresproxy", "prores422p", "422proxy"),
                   out_dir, name or Path(clip.GetClipProperty("File Path") or clip.GetName()).stem, timeout)


def render_proxy(h, clip, out_dir: Optional[Path] = None, name: Optional[str] = None, width: int = 640,
                 height: int = 360, timeout: float = 14400) -> Path:
    """A small H.264 picture-only copy (for analysis and syncing by picture)."""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="theodore-proxy-"))
    return _render(h, clip, {"ExportVideo": True, "ExportAudio": False, "FormatWidth": width, "FormatHeight": height},
                   "mp4", ("h264", "h.264"), out_dir, name or Path(clip.GetClipProperty("File Path") or clip.GetName()).stem + "_proxy",
                   timeout)


def render_proxy_range(h, clip, start_frame: int, end_frame: int, out_dir: Optional[Path] = None,
                       name: str = "window", width: int = 640, height: int = 360, timeout: float = 1800) -> Path:
    """A small picture-only H.264 of just frames [start_frame, end_frame) of the clip: seconds, not minutes."""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="theodore-window-"))
    return _render(h, clip, {"ExportVideo": True, "ExportAudio": False, "FormatWidth": width, "FormatHeight": height},
                   "mp4", ("h264", "h.264"), out_dir, name, timeout, frames=(start_frame, end_frame))
