"""Transcription with Resolve's own speech-to-text -- no outside service.

Resolve Studio's "Create Subtitles from Audio" runs its transcription model
locally and lays the result on a subtitle track. Asking for one character per
line makes it emit one word (occasionally two) per subtitle, each with its own
frame-accurate in and out -- in effect a word-level transcript, filler words
included. That is everything story editing and filler/silence cutting need.

It always runs on a throwaway duplicate (or a throwaway timeline made from the
file), so the editor's timeline never gains a subtitle track it didn't ask
for, and Resolve is left on the timeline it was showing.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

# Numeric values of resolve.SUBTITLE_* / AUTO_CAPTION_* in Resolve 21 -- used
# when the constants aren't reachable as attributes (e.g. over the bridge).
_CONST = {"SUBTITLE_LANGUAGE": 0, "SUBTITLE_CHARS_PER_LINE": 2, "SUBTITLE_LINE_BREAK": 3,
          "SUBTITLE_GAP": 4, "AUTO_CAPTION_LINE_SINGLE": 1}
REFERENCE_BIN = "Theodore References"
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")


class TranscribeError(RuntimeError):
    pass


def _const(resolve, name):
    value = getattr(resolve, name, None) if resolve is not None else None
    return value if isinstance(value, (int, float)) else _CONST[name]


def _settings(resolve, word_level: bool) -> dict:
    if not word_level:
        return {}
    return {_const(resolve, "SUBTITLE_CHARS_PER_LINE"): 1,
            _const(resolve, "SUBTITLE_LINE_BREAK"): _const(resolve, "AUTO_CAPTION_LINE_SINGLE")}


def _fps(tl, project) -> float:
    raw = tl.GetSetting("timelineFrameRate") or project.GetSetting("timelineFrameRate") or 24
    try:
        return float(str(raw).split()[0])
    except ValueError:
        return 24.0


def _words_from_track(tl, fps: float) -> list[dict]:
    start = int(tl.GetStartFrame())
    words = []
    count = int(tl.GetTrackCount("subtitle") or 0)
    items = []
    for i in range(1, count + 1):
        items += list(tl.GetItemListInTrack("subtitle", i) or [])
    items.sort(key=lambda it: it.GetStart())
    for it in items:
        text = (it.GetName() or "").strip()
        if not text:
            continue
        s, e = int(it.GetStart()) - start, int(it.GetEnd()) - start
        tokens = text.split()
        # A chunk can hold two short words ("and I"): share its time evenly.
        span = (e - s) / max(len(tokens), 1)
        for k, tok in enumerate(tokens):
            ws, we = s + span * k, s + span * (k + 1)
            words.append({
                "word": re.sub(r"[^\w'-]", "", tok).lower() or tok,
                "punctuated_word": tok,
                "start": round(ws / fps, 3), "end": round(we / fps, 3),
                "start_frame": int(round(ws)), "end_frame": int(round(we)),
            })
    return words


def sentences(words: list[dict]) -> list[dict]:
    """Group words into sentences on terminal punctuation (and long pauses)."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 0
        if _SENTENCE_END.search(w["punctuated_word"]) or gap > 1.2:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"text": " ".join(x["punctuated_word"] for x in s), "start": s[0]["start"], "end": s[-1]["end"],
             "start_frame": s[0]["start_frame"], "end_frame": s[-1]["end_frame"]} for s in out]


def _run_on(h, tl, word_level: bool) -> dict:
    project = h.project
    t0 = time.time()
    if not tl.CreateSubtitlesFromAudio(_settings(h.resolve, word_level)):
        raise TranscribeError(
            "Resolve's Create Subtitles from Audio failed. It needs DaVinci Resolve Studio and "
            "audio on the timeline; the first run may also download Resolve's speech model.")
    fps = _fps(tl, project)
    words = _words_from_track(tl, fps)
    return {"fps": fps, "words": words, "sentences": sentences(words),
            "text": " ".join(w["punctuated_word"] for w in words),
            "engine": "DaVinci Resolve (Create Subtitles from Audio)",
            "seconds_taken": round(time.time() - t0, 1)}


def _delete_timeline(project, tl) -> None:
    try:
        project.GetMediaPool().DeleteTimelines([tl])
    except Exception:  # noqa: BLE001 -- a leftover temp timeline is untidy, not harmful
        pass


def transcribe_timeline(h, word_level: bool = True) -> dict:
    """Transcribe the open timeline's audio without touching it."""
    project, original = h.project, h.timeline
    temp = original.DuplicateTimeline(f"Theodore transcribe {int(time.time())}")
    if not temp:
        raise TranscribeError("Could not make a temporary copy of the timeline to transcribe.")
    try:
        project.SetCurrentTimeline(temp)
        for i in range(int(temp.GetTrackCount("subtitle") or 0), 0, -1):
            temp.DeleteTrack("subtitle", i)  # only the new transcription, not old captions
        result = _run_on(h, temp, word_level)
    finally:
        project.SetCurrentTimeline(original)
        _delete_timeline(project, temp)
    result["timeline"] = original.GetName()
    return result


def _reference_bin(pool):
    root = pool.GetRootFolder()
    for sub in list(root.GetSubFolderList() or []):
        if sub.GetName() == REFERENCE_BIN:
            return sub
    return pool.AddSubFolder(root, REFERENCE_BIN) or root


def transcribe_file(h, path: Path, word_level: bool = True) -> dict:
    """Transcribe a media file through Resolve. It's imported into a
    "Theodore References" bin (re-used if already there); the temporary
    timeline is removed and Resolve returns to the timeline it was on."""
    project = h.project
    if project is None:
        raise TranscribeError("Open a project in Resolve first -- transcription runs inside it.")
    pool = project.GetMediaPool()
    path = str(Path(path).expanduser().resolve())
    folder = _reference_bin(pool)
    clip = next((c for c in (folder.GetClipList() or [])
                 if (c.GetClipProperty("File Path") or "") == path), None)
    if clip is None:
        previous_folder = pool.GetCurrentFolder()
        pool.SetCurrentFolder(folder)
        imported = list(pool.ImportMedia([path]) or [])
        if previous_folder:
            pool.SetCurrentFolder(previous_folder)
        if not imported:
            raise TranscribeError(f"Resolve could not import {path}.")
        clip = imported[0]
    original = project.GetCurrentTimeline()
    temp = pool.CreateTimelineFromClips(f"Theodore transcribe {int(time.time())}", [clip])
    if not temp:
        raise TranscribeError("Could not create a temporary timeline to transcribe on.")
    try:
        project.SetCurrentTimeline(temp)
        result = _run_on(h, temp, word_level)
    finally:
        if original:
            project.SetCurrentTimeline(original)
        _delete_timeline(project, temp)
    result["source"] = path
    return result


def try_transcribe_file(path: Path, word_level: bool = True) -> tuple[Optional[dict], Optional[str]]:
    """(result, None) or (None, why not) -- for callers where Resolve is optional."""
    from theodore.resolve import connection
    try:
        h = connection.connect(require_project=True, require_timeline=False)
    except Exception as exc:  # noqa: BLE001
        return None, f"Resolve isn't reachable for transcription ({str(exc).splitlines()[0]})"
    try:
        return transcribe_file(h, path, word_level), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# ---------------------------------------------------------------------------
# Raw clips (interview footage in the media pool), cached
# ---------------------------------------------------------------------------

def _cache_path(clip) -> Optional[Path]:
    import hashlib
    from theodore import config
    path = clip.GetClipProperty("File Path") or ""
    try:
        st = Path(path).stat()
        key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = f"{path}|{clip.GetName()}|{clip.GetClipProperty('Frames')}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return Path(config.DATA_ROOT) / "transcripts" / f"{digest}.json"


def transcribe_clip(h, clip, word_level: bool = True, use_cache: bool = True) -> dict:
    """Transcribe one media pool clip. Frames in the result are SOURCE frames
    of the clip (0 = its first frame), exactly what AppendToTimeline's
    startFrame/endFrame take, so selects can be cut straight from them."""
    import json
    cache = _cache_path(clip)
    if use_cache and cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    project = h.project
    pool = project.GetMediaPool()
    original = project.GetCurrentTimeline()
    temp = pool.CreateTimelineFromClips(f"Theodore transcribe {int(time.time() * 1000)}", [clip])
    if not temp:
        raise TranscribeError(f"Could not make a temporary timeline for '{clip.GetName()}'.")
    try:
        project.SetCurrentTimeline(temp)
        result = _run_on(h, temp, word_level)
    finally:
        if original:
            project.SetCurrentTimeline(original)
        _delete_timeline(project, temp)
    result["clip"] = clip.GetName()
    result["file"] = clip.GetClipProperty("File Path")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Captions on the timeline itself
# ---------------------------------------------------------------------------

CAPTION_STYLES = {
    # chars per line, double line?
    "social": (22, False),       # short punchy lines for 9:16 / muted autoplay
    "broadcast": (42, True),     # standard two-line subtitles
    "netflix": (42, True),
}


def add_captions(h, style: str = "social") -> dict:
    """Create a subtitle track from the open timeline's audio with Resolve's
    own captioning. Additive: a new subtitle track; nothing else changes."""
    if style not in CAPTION_STYLES:
        raise TranscribeError(f"style must be one of {sorted(CAPTION_STYLES)}")
    tl, r = h.timeline, h.resolve
    chars, double = CAPTION_STYLES[style]
    settings = {_const(r, "SUBTITLE_CHARS_PER_LINE"): chars,
                _const(r, "SUBTITLE_LINE_BREAK"): (getattr(r, "AUTO_CAPTION_LINE_DOUBLE", 2) if double
                                                   else _const(r, "AUTO_CAPTION_LINE_SINGLE"))}
    if style == "netflix" and hasattr(r, "SUBTITLE_CAPTION_PRESET"):
        settings[r.SUBTITLE_CAPTION_PRESET] = r.AUTO_CAPTION_NETFLIX
    before = int(tl.GetTrackCount("subtitle") or 0)
    if not tl.CreateSubtitlesFromAudio(settings):
        raise TranscribeError("Resolve's Create Subtitles from Audio failed (Studio only; needs audio).")
    after = int(tl.GetTrackCount("subtitle") or 0)
    items = list(tl.GetItemListInTrack("subtitle", after) or []) if after else []
    return {"timeline": tl.GetName(), "subtitle_track": f"ST{after}", "captions": len(items),
            "style": style, "first": [it.GetName() for it in items[:3]],
            "note": ("Burn in or export as SRT from the Deliver page. Caption look (font, box, position) is "
                     "set on the subtitle track's Inspector." + (" New track added." if after > before else ""))}
