"""Content-hash-based transcript caching so Theodore never re-bills
Deepgram for footage it has already transcribed."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def cache_path(project_dir: Path, audio_hash: str) -> Path:
    d = project_dir / "transcript_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{audio_hash}.json"


def load_cached(project_dir: Path, audio_hash: str) -> Optional[dict]:
    p = cache_path(project_dir, audio_hash)
    if p.exists():
        return json.loads(p.read_text())
    return None


def store(project_dir: Path, audio_hash: str, raw_response: dict) -> Path:
    p = cache_path(project_dir, audio_hash)
    p.write_text(json.dumps(raw_response, indent=2))
    return p
