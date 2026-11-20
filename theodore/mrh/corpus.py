"""Step 1: transcribe every lav recording once (cached) into timed sentences."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import tagger

STT_PY = os.path.expanduser("~/.theodore-stt/bin/python")
WORKER = str(Path(__file__).with_name("stt_worker.py"))
CACHE = os.path.expanduser("~/Documents/Theodore/MrH Cache")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aif", ".aiff")


def discover(h, name_hint: tuple = ("lav", "dji", "mic")) -> list[str]:
    """Audio files in the open Resolve project's media pool that look like lav/mic recordings (read-only)."""
    pool = h.project.GetMediaPool(); found = {}

    def walk(folder):
        for c in folder.GetClipList() or []:
            p = c.GetClipProperty("File Path") or ""
            if p.lower().endswith(AUDIO_EXT) and os.path.exists(p) and (
                    any(k in p.lower() or k in folder.GetName().lower() for k in name_hint)):
                found[p] = 1
        for f in folder.GetSubFolderList() or []: walk(f)
    walk(pool.GetRootFolder())
    return sorted(found)


def transcribe(files: list[str], cache: str = CACHE) -> list[dict]:
    os.makedirs(cache, exist_ok=True); out = []
    for f in files:
        key = hashlib.sha1(f"{f}|{os.path.getmtime(f)}|{os.path.getsize(f)}".encode()).hexdigest()[:16]
        cj = os.path.join(cache, f"{key}.json")
        if not os.path.exists(cj):
            subprocess.run([STT_PY, WORKER, f, cj], check=True, stderr=subprocess.DEVNULL)
        seg = json.load(open(cj))
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", f],
                                   capture_output=True, text=True).stdout or 0)
        out.append(dict(file=f, minutes=round(dur / 60, 1), sentences=tagger.sentences(seg)))
    return out


def candidates(corpus: list[dict], top: float = 0.40) -> list[dict]:
    """The best `top` fraction of sentences per recording (ranked), each carrying file + times; Theodore reads these and picks."""
    cands = []
    for rec in corpus:
        r = tagger.rank([dict(x) for x in rec["sentences"]])
        for x in r[: max(1, int(len(r) * top))]:
            cands.append(dict(x, file=rec["file"]))
    return sorted(cands, key=lambda x: (x["file"], x["s"]))
