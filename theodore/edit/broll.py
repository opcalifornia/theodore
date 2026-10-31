"""B-roll: Claude looks at the cutaways, then lays them over the right lines.

1. Catalogue: three frames from each B-roll clip (read straight from the
   file) go to Claude, which logs what it shows, shot size, movement, mood and
   the best moments to use. Cached per file -- a library is looked at once.
2. Plan: with the timeline's transcript, Claude decides what covers what, the
   way an editor does -- show the speaker first, cut on phrases, match picture
   to words, never repeat a shot, leave the emotional beats on the face.
3. Place: video-only, on the first free track above the interview, so the
   interview's sound is untouched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


from theodore import config
from theodore.llm import make_client
from theodore.analyze.claude_client import CostTracker, call_json
from theodore.edit import EDITOR_MODEL, VISION_MODEL

CATALOG_SYSTEM = """You are an assistant editor logging B-roll for a commercial edit. Be \
concrete: what is in frame, shot size, camera movement, mood, and the best usable moments."""

CATALOG_INSTRUCTIONS = """For EACH clip above return JSON only:
{"clips": [{"name": "<clip name>", "description": "<what it shows, one sentence>",
  "subjects": ["..."], "shot_size": "WS|MS|CU|ECU|insert|aerial", "movement": "static|pan|tilt|push|pull|handheld|gimbal|drone",
  "mood": "<few words>", "usable": true, "best_moments": [{"at_seconds": <n>, "what": "<...>"}]}]}"""

PLAN_SYSTEM = """You are the lead editor at a top commercial post house placing B-roll over an \
interview cut. Rules you follow:
- Let the audience see the speaker for at least the first ~3 seconds of the piece and whenever \
they introduce themselves or hit an emotional peak -- don't cover those moments.
- Cover lines that describe something visual; match the picture to the words.
- Each cutaway 2-5 seconds, in and out on phrase boundaries (word timings given).
- Don't reuse the same clip unless the library is thin; vary shot sizes (wide then detail).
- Cover roughly the requested share of the runtime."""

PLAN_INSTRUCTIONS = """Return JSON only:
{"placements": [{"clip": "<exact clip name>", "source_start_seconds": <n>, "timeline_start_seconds": <n>,
                 "duration_seconds": <n>, "covers": "<the words it plays over>", "why": "<short>"}],
 "notes": "<missing shots you'd want>"}
timeline_start_seconds is measured from the start of the timeline."""


def _cache_file(path: str) -> Path:
    try:
        st = Path(path).stat()
        key = f"{path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = path
    return Path(config.DATA_ROOT) / "broll" / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def _frames(path: str, duration: float, out_dir: Path) -> list[Path]:
    shots = []
    for k, frac in enumerate((0.2, 0.5, 0.8)):
        out = out_dir / f"{k}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(duration * frac, 0):.2f}", "-i", path,
                        "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5", str(out)],
                       capture_output=True, timeout=120)
        if out.exists():
            shots.append(out)
    return shots


def _clip_seconds(clip) -> float:
    try:
        frames = float(clip.GetClipProperty("Frames") or 0)
        fps = float(str(clip.GetClipProperty("FPS") or 24).split()[0])
        return frames / fps if fps else 0.0
    except ValueError:
        return 0.0


def catalog(clips: list, client=None, progress=None) -> dict[str, dict]:
    """{clip name: log entry}, looking only at clips not seen before."""
    say = progress or (lambda m: None)
    out, todo = {}, []
    for c in clips:
        path = c.GetClipProperty("File Path") or ""
        cache = _cache_file(path)
        if path and cache.exists():
            out[c.GetName()] = {**json.loads(cache.read_text(encoding="utf-8")), "name": c.GetName()}
        elif path:
            todo.append(c)
    client = client or (make_client() if todo else None)
    for b in range(0, len(todo), 8):
        batch = todo[b:b + 8]
        say(f"Looking at B-roll {b + 1}-{b + len(batch)} of {len(todo)}")
        content = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, c in enumerate(batch):
                secs = _clip_seconds(c)
                content.append({"type": "text", "text": f"Clip '{c.GetName()}' ({secs:.1f}s): frames at 20%, 50%, 80%"})
                d = Path(tmp) / str(i)
                d.mkdir()
                for f in _frames(c.GetClipProperty("File Path"), secs, d):
                    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                               "data": base64.b64encode(f.read_bytes()).decode()}})
            content.append({"type": "text", "text": CATALOG_INSTRUCTIONS})
            data = call_json(client, pass_name="broll-catalog", model=VISION_MODEL, system=CATALOG_SYSTEM,
                             user=content, max_tokens=4000, cost_tracker=CostTracker())
        logged = {str(e.get("name")): e for e in data.get("clips", [])}
        for c in batch:
            entry = logged.get(c.GetName()) or {"description": "(not logged)", "usable": True}
            entry["seconds"] = round(_clip_seconds(c), 2)
            cache = _cache_file(c.GetClipProperty("File Path"))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(entry, indent=2), encoding="utf-8")
            out[c.GetName()] = {**entry, "name": c.GetName()}
    return out


def _free_track(tl, start: int, end: int, lowest: int = 2) -> int:
    count = int(tl.GetTrackCount("video") or 0)
    for idx in range(lowest, count + 1):
        if all(not (it.GetStart() < end and start < it.GetEnd()) for it in (tl.GetItemListInTrack("video", idx) or [])):
            return idx
    tl.AddTrack("video")
    return int(tl.GetTrackCount("video"))


def style_from_reference(name: str) -> dict:
    """Cutaway habits measured from a studied reference -> placement targets and prompt rules."""
    from theodore.study import reference
    ref = reference.load_reference(name)
    lay = (ref.get("analysis") or {}).get("layers") or {}
    sm = lay.get("summary") or {}
    if not sm.get("cutaways"):
        raise RuntimeError(f"The reference {name!r} has no measured cutaways over speech (no transcript, or no B-roll "
                           "over dialogue). Pick another or study it again with transcription.")
    secs = sm.get("cutaway_seconds") or {}
    time_pct = sm.get("time_pct") or {}
    cover = (time_pct.get("broll_over_speech", 0) or 0) / 100
    rules = [
        f"Match the reference '{name}' (measured, not guessed):",
        f"- Cutaways run {secs.get('min')}-{secs.get('max')}s, median {secs.get('median')}s "
        f"({sm.get('cutaway_shots_median')} shot(s) each).",
        f"- First cutaway at about {sm.get('first_cutaway_at')}s; the longest unbroken stretch on the face is "
        f"{sm.get('longest_uninterrupted_face_seconds')}s.",
        f"- A cutaway comes in a median {sm.get('lead_in_seconds_median')}s into the line it covers; "
        f"{sm.get('cutaways_on_phrase_pct')}% start on a phrase break; "
        f"{sm.get('cutaways_return_before_line_end_pct')}% return to the face before the line ends.",
        f"- {sm.get('dialogue_covered_by_broll_pct')}% of spoken time is under B-roll.",
        f"- B-roll shot sizes used: {sm.get('broll_shot_sizes')}.",
    ]
    bp = ref.get("blueprint") or {}
    from theodore.study import love
    loved = love.rules_text(bp, ("place_broll", "other"))
    if loved:
        rules.append("The editor told me what they love about this reference -- lead with it:\n" + loved)
    system = bp.get("dialogue_broll_system") or {}
    if system.get("picture_to_words"):
        rules.append(f"- Picture to words: {system['picture_to_words']}")
    if system.get("what_stays_on_face"):
        rules.append(f"- Stays on the face: {system['what_stays_on_face']}")
    examples = [f"  \"{c['covers']}\" -> {', '.join(str(x) for x in c['pictures'] if x)}"
                for c in (lay.get("cutaways") or [])[:4] if c.get("covers")]
    if examples:
        rules.append("- Examples of words -> picture in the reference:\n" + "\n".join(examples))
    return {"coverage": round(cover, 2) if cover else None, "min_seconds": secs.get("min"),
            "max_seconds": secs.get("max"), "rules": "\n".join(rules)}


def place_broll(h, clips: list, *, coverage: float = 0.4, brief: str = "", on_duplicate: bool = True,
                name: Optional[str] = None, client=None, progress=None, reference: Optional[str] = None) -> dict:
    from theodore.resolve import timeline_ops, transcribe

    style = style_from_reference(reference) if reference else None
    if style:
        brief = (brief + "\n\n" if brief else "") + style["rules"]
        coverage = style["coverage"] or coverage

    say = progress or (lambda m: None)
    client = client or make_client()
    library = catalog(clips, client, progress=say)
    usable = {k: v for k, v in library.items() if v.get("usable", True)}
    if not usable:
        raise RuntimeError("None of those clips looked usable as B-roll.")
    say("Reading the cut")
    tr = transcribe.transcribe_timeline(h, word_level=True)
    fps = tr["fps"]
    tl = h.timeline
    runtime = max((it.GetEnd() for it in (tl.GetItemListInTrack("video", 1) or [])), default=tl.GetStartFrame())
    runtime = (runtime - tl.GetStartFrame()) / fps
    lines = [f"[{s['start']:.2f}-{s['end']:.2f}s] {s['text']}" for s in tr["sentences"]]
    lib_text = json.dumps(list(usable.values()), separators=(",", ":"))
    say("Claude is choosing cutaways")
    plan = call_json(client, pass_name="broll-plan", model=EDITOR_MODEL, system=PLAN_SYSTEM,
                     user=(f"Brief: {brief or '(none)'}\nRuntime: {runtime:.1f}s. Cover about "
                           f"{int(coverage * 100)}% of it.\n\nTranscript with timings:\n" + "\n".join(lines)
                           + f"\n\nB-roll library:\n{lib_text}\n\n" + PLAN_INSTRUCTIONS),
                     max_tokens=4000, cost_tracker=CostTracker())

    # Handles are a snapshot: after duplicating, work on the timeline we were given back.
    tl = timeline_ops.duplicate_for_edit(h, name, "b-roll") if on_duplicate else h.timeline
    start = int(tl.GetStartFrame())
    pool = h.project.GetMediaPool()
    by_name = {c.GetName(): c for c in clips}
    placed, refused = [], []
    for p in plan.get("placements", []):
        clip = by_name.get(str(p.get("clip")))
        if clip is None:
            refused.append(f"unknown clip {p.get('clip')!r}")
            continue
        clip_frames = int(float(clip.GetClipProperty("Frames") or 0))
        want = float(p.get("duration_seconds") or 3)
        if style and style["min_seconds"] and style["max_seconds"]:   # stay inside the reference's range
            want = min(max(want, float(style["min_seconds"])), float(style["max_seconds"]))
        dur = int(round(want * fps))
        src = int(round(float(p.get("source_start_seconds") or 0) * fps))
        src = max(0, min(src, max(clip_frames - dur, 0)))
        dur = min(dur, clip_frames - src) if clip_frames else dur
        rec = start + int(round(float(p.get("timeline_start_seconds") or 0) * fps))
        if dur < int(fps):  # under a second reads as a flash frame
            refused.append(f"{clip.GetName()}: too short")
            continue
        track = _free_track(tl, rec, rec + dur)
        got = timeline_ops._placed_item(pool.AppendToTimeline([{
            "mediaPoolItem": clip, "startFrame": src, "endFrame": src + dur, "recordFrame": rec,
            "trackIndex": track, "mediaType": 1}]))
        if got is None:
            refused.append(f"{clip.GetName()} at {p.get('timeline_start_seconds')}s: Resolve refused")
            continue
        placed.append({"clip": clip.GetName(), "at_seconds": round((rec - start) / fps, 2),
                       "seconds": round(dur / fps, 2), "track": f"V{track}", "covers": p.get("covers"),
                       "why": p.get("why")})
    covered = sum(p["seconds"] for p in placed)
    return {"timeline": tl.GetName(), "placed": placed, "refused": refused,
            "coverage_pct": round(100 * covered / runtime, 1) if runtime else 0,
            "notes": plan.get("notes"), "library_logged": len(library),
            "matched_reference": reference}
