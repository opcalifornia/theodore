"""Render timelines for the client: H.264 MP4, versioned names, a short note.

One call takes the open timeline (or the named ones -- e.g. the 16:9 master
and its 9:16 cut-down) to a delivery folder:

  <Project>_<Timeline>_v<N>.mp4     N is one past the highest version already
                                    in the folder, so a re-send never
                                    overwrites what the client has.
  <Project>_v<N>_delivery.md        what was sent, size, length, how to review.

Rendering never touches the timelines. Resolve moves to the Deliver page while
it works; the editor is put back on the page they were on.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

DEFAULT_ROOT = Path.home() / "Movies" / "Theodore Deliveries"

#: Resolve's own bitrate presets for H.264; "Auto" lets Resolve choose.
QUALITIES = {"auto": "Auto", "high": "Best", "medium": "Medium", "low": "Low"}

_VERSION = re.compile(r"_v(\d+)(?:\D|$)")


class DeliverError(RuntimeError):
    pass


def safe_name(text: str) -> str:
    text = re.sub(r"[^\w\- ]+", "", text or "").strip().replace(" ", "_")
    return re.sub(r"_+", "_", text) or "Untitled"


def next_version(folder: Path, project: str) -> int:
    """One past the highest `<project>_..._vN` already delivered here."""
    top = 0
    if folder.is_dir():
        for f in folder.iterdir():
            if f.name.startswith(project + "_"):
                m = _VERSION.search(f.name)
                if m:
                    top = max(top, int(m.group(1)))
    return top + 1


def probe(path: Path) -> dict:
    """Duration/size/dimensions of a delivered file (ffprobe if present)."""
    info = {"size_mb": round(path.stat().st_size / 1e6, 1)}
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height,codec_name:format=duration", "-of", "default=nw=1", str(path)],
                capture_output=True, text=True, timeout=30).stdout
            kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            if "duration" in kv:
                info["seconds"] = round(float(kv["duration"]), 1)
            if "width" in kv:
                info["size"] = f"{kv['width']}x{kv['height']}"
            if "codec_name" in kv:
                info["codec"] = kv["codec_name"]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return info


def _find_timeline(project, name: str):
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    raise DeliverError(f"No timeline named {name!r}.")


def _wait(project, job, timeout: float, progress=None):
    start = time.time()
    while True:
        st = project.GetRenderJobStatus(job) or {}
        status = str(st.get("JobStatus", ""))
        if progress and "CompletionPercentage" in st:
            progress(st["CompletionPercentage"])
        if status in ("Complete", "Failed", "Cancelled"):
            return status, st
        if not project.IsRenderingInProgress() and status not in ("Rendering", "Ready"):
            return status or "Unknown", st
        if time.time() - start > timeout:
            project.StopRendering()
            raise DeliverError("The render took too long and was stopped.")
        time.sleep(1)


def deliver(h, timelines: Optional[list[str]] = None, target_dir: Optional[str] = None, *,
            quality: str = "high", audio: bool = True, note: str = "", timeout: float = 4 * 3600,
            progress=None) -> dict:
    project = h.project
    if quality not in QUALITIES:
        raise DeliverError(f"quality must be one of {sorted(QUALITIES)}.")
    proj_name = safe_name(project.GetName())
    folder = Path(target_dir).expanduser() if target_dir else DEFAULT_ROOT / proj_name
    folder.mkdir(parents=True, exist_ok=True)

    original = project.GetCurrentTimeline()
    if timelines:
        targets = [_find_timeline(project, n) for n in timelines]
    elif original is not None:
        targets = [original]
    else:
        raise DeliverError("No timeline is open to deliver.")

    version = next_version(folder, proj_name)
    page = h.resolve.GetCurrentPage()
    sent, failed = [], []
    try:
        for tl in targets:
            tl_name = tl.GetName()
            base = f"{proj_name}_{safe_name(tl_name)}_v{version}"
            project.SetCurrentTimeline(tl)
            if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
                raise DeliverError("Resolve would not switch to H.264 MP4.")
            settings = {"SelectAllFrames": True, "TargetDir": str(folder), "CustomName": base,
                        "ExportVideo": True, "ExportAudio": bool(audio), "VideoQuality": QUALITIES[quality]}
            if not project.SetRenderSettings(settings):
                raise DeliverError(f"Resolve rejected the render settings for {tl_name!r} "
                                   "(does the folder exist and is it writable?).")
            job = project.AddRenderJob()
            if not job:
                raise DeliverError(f"Resolve would not queue {tl_name!r}.")
            try:
                if not project.StartRendering([job], isInteractiveMode=False):
                    raise DeliverError(f"Resolve would not start rendering {tl_name!r}.")
                status, _ = _wait(project, job, timeout, progress)
            finally:
                try:
                    project.DeleteRenderJob(job)  # leave the editor's render queue as it was
                except Exception:  # noqa: BLE001
                    pass
            files = sorted(folder.glob(f"{base}.*"))
            if status != "Complete" or not files:
                failed.append({"timeline": tl_name, "status": status})
                continue
            sent.append({"timeline": tl_name, "file": str(files[0]), **probe(files[0])})
    finally:
        if original is not None:
            project.SetCurrentTimeline(original)
        if page:
            h.resolve.OpenPage(page)

    note_path = None
    if sent:
        note_path = folder / f"{proj_name}_v{version}_delivery.md"
        note_path.write_text(_note(project.GetName(), version, sent, failed, note), encoding="utf-8")
    result = {"version": version, "folder": str(folder), "delivered": sent, "failed": failed,
              "note": str(note_path) if note_path else None}
    if failed:
        result["warning"] = "Some renders did not finish -- check the Deliver page's render queue."
    return result


def _note(project: str, version: int, sent: list, failed: list, extra: str) -> str:
    lines = [f"# {project} -- v{version}", "", time.strftime("Rendered %Y-%m-%d %H:%M"), ""]
    if extra:
        lines += [extra.strip(), ""]
    lines += ["| File | Cut | Size | Length | Frame |", "|---|---|---|---|---|"]
    for s in sent:
        secs = s.get("seconds")
        length = f"{int(secs // 60)}:{int(secs % 60):02d}" if secs is not None else "-"
        lines.append(f"| {Path(s['file']).name} | {s['timeline']} | {s['size_mb']} MB | {length} | "
                     f"{s.get('size', '-')} |")
    if failed:
        lines += ["", "Did not render: " + ", ".join(f["timeline"] for f in failed)]
    lines += ["", "H.264 MP4, AAC audio. Reply with timecodes and notes and the next version goes out "
              f"as v{version + 1}; v{version} is never overwritten.", ""]
    return "\n".join(lines)
