"""ffprobe-based media metadata extraction.

The one rule that matters here: fps comes from ffprobe's raw r_frame_rate
field, an exact rational string like "24000/1001" -- never a rounded float.
Everything downstream (timecode.py, marker placement) depends on that
exactness.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("theodore.ingest.media")


class MediaProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    fps: str  # exact rational, e.g. "24000/1001" -- never a rounded float
    duration_seconds: float
    start_timecode: str
    has_video: bool
    has_audio: bool
    audio_hash: str = ""  # content hash of the source file; set at ingest time


def _run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise MediaProbeError(
            "ffprobe not found. Install ffmpeg (which bundles ffprobe) and "
            "make sure it's on your PATH."
        ) from exc
    if result.returncode != 0:
        raise MediaProbeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(f"ffprobe produced unparseable output for {path}") from exc


def probe(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise MediaProbeError(f"No such file: {path}")

    data = _run_ffprobe(path)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams and not audio_streams:
        raise MediaProbeError(f"{path} has no video or audio streams ffprobe could read")

    fps = "0/1"
    start_timecode = "00:00:00:00"

    if video_streams:
        v = video_streams[0]
        # r_frame_rate is already an exact rational string -- this is the
        # whole reason we read this field instead of avg_frame_rate or any
        # computed float.
        fps = v.get("r_frame_rate", "0/1")
        tc_tag = v.get("tags", {}).get("timecode")
        if tc_tag:
            start_timecode = tc_tag

    if start_timecode == "00:00:00:00":
        tc_tag = fmt.get("tags", {}).get("timecode")
        if tc_tag:
            start_timecode = tc_tag

    duration_source = fmt.get("duration") or (video_streams or audio_streams)[0].get("duration") or 0.0
    duration = float(duration_source)

    return MediaInfo(
        path=str(path),
        fps=fps,
        duration_seconds=duration,
        start_timecode=start_timecode,
        has_video=bool(video_streams),
        has_audio=bool(audio_streams),
    )


def save_media_info(info: MediaInfo, project_dir: Path) -> Path:
    out = project_dir / "media.json"
    existing = json.loads(out.read_text()) if out.exists() else []
    existing = [e for e in existing if e["path"] != info.path]
    existing.append(asdict(info))
    out.write_text(json.dumps(existing, indent=2))
    return out


def load_media_info(project_dir: Path) -> list[dict]:
    out = project_dir / "media.json"
    if not out.exists():
        return []
    return json.loads(out.read_text())
