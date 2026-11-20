"""ffmpeg audio extraction for transcription: mono 16kHz WAV. Video is never
transcoded -- Theodore only ever touches Resolve via markers on the
editor's own media, not by re-encoding anything.
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("theodore.ingest.audio")


class AudioExtractionError(RuntimeError):
    pass


def content_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash of a file's content. Used both to name the extracted WAV and as
    the transcript cache key, so re-running Theodore on the same footage
    never re-extracts audio or re-bills transcription."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def extract_wav(source: Path, out_dir: Path) -> Path:
    """Extract a mono 16kHz WAV for Deepgram, named by the source file's
    content hash so it doubles as a cache key. Skips re-extraction if the
    file already exists."""
    source = Path(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = content_hash(source)
    out_path = out_dir / f"{digest}.wav"
    if out_path.exists():
        logger.info("Audio already extracted for %s (cache hit)", source.name)
        return out_path

    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError as exc:
        raise AudioExtractionError(
            "ffmpeg not found. Install ffmpeg and make sure it's on your PATH."
        ) from exc
    if result.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise AudioExtractionError(f"ffmpeg failed on {source}: {result.stderr.strip()[-2000:]}")
    return out_path
