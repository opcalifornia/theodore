"""Claude looks at every shot: what it is, and everything laid over it.

Signals can say *where* the cuts are; only looking can say a shot is a
medium close-up of the host with a lower third bottom-left and the logo
bug top-right. One frame from the middle of each shot (plus one near the end
of longer shots, so a title animating on or a push-in shows up) goes to
Claude in batches, with the timing of each shot written beside its frames.
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from theodore.analyze.claude_client import CostTracker, call_json

STUDY_MODEL = os.environ.get("THEODORE_STUDY_MODEL", "claude-sonnet-5-5")
FRAME_WIDTH = 640
BATCH = 16
MAX_SHOTS = 400  # beyond this, an evenly spaced sample: enough to learn a style

SYSTEM = """You are a senior post-production editor and finishing artist logging a \
finished piece shot by shot, so another editor can rebuild its style on new footage. \
Be concrete and literal: describe what is on screen, not what it might mean. Transcribe \
on-screen text exactly. Positions use a 3x3 grid: top-left, top-center, top-right, \
middle-left, center, middle-right, bottom-left, bottom-center, bottom-right."""

INSTRUCTIONS = """For EACH shot above return one object. Respond with ONLY JSON:
{"shots": [{
  "index": <shot number>,
  "shot_size": "EWS|WS|MWS|MS|MCU|CU|ECU|insert|graphic",
  "angle": "eye|high|low|overhead|dutch|POV|aerial",
  "camera": "static|pan|tilt|push_in|pull_out|tracking|handheld|gimbal|drone|zoom|unknown (infer from the two frames when given)",
  "subject": "<who/what, short>",
  "role": "a_roll (on-camera talent/interview/performance)|b_roll|establishing|product|graphic|title_card|end_card|reaction|insert",
  "on_screen_text": ["<exact text>", ...],
  "overlays": [{"type": "lower_third|title|subtitle|caption|logo_bug|text_animation|graphic|sticker|split_screen|picture_in_picture|frame_border|light_leak|grain|vignette|letterbox|other",
                "position": "<grid position>", "description": "<style: font weight/case, colour, box/plate, animation if visible>"}],
  "look": "<colour/contrast/grade in a few words>",
  "notes": "<anything an editor needs to replicate this shot: speed ramp, freeze, mask, slow motion...>"
}]}
Every shot must appear, in order. Use [] when there is no text or overlay."""


def extract_frames(video: Path, frame_indices: list[int], out_dir: Path) -> dict[int, Path]:
    """One decode pass, writing each requested frame as a JPEG named by index."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(frame_indices))
    missing = [i for i in wanted if not (out_dir / f"f{i:07d}.jpg").exists()]
    if missing:
        tmp = out_dir / "_batch"
        tmp.mkdir(exist_ok=True)
        # Chunked so the select expression stays a sane length.
        for c in range(0, len(missing), 200):
            chunk = missing[c:c + 200]
            expr = "+".join(f"eq(n\\,{i})" for i in chunk)
            for old in tmp.glob("*.jpg"):
                old.unlink()
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", "0:v:0",
                 "-vf", f"select='{expr}',scale={FRAME_WIDTH}:-2", "-fps_mode", "vfr", "-q:v", "4",
                 str(tmp / "%06d.jpg")],
                check=True, capture_output=True, timeout=3600,
            )
            produced = sorted(tmp.glob("*.jpg"))
            for idx, path in zip(chunk, produced):
                path.rename(out_dir / f"f{idx:07d}.jpg")
        for leftover in tmp.glob("*"):
            leftover.unlink()
        tmp.rmdir()
    return {i: out_dir / f"f{i:07d}.jpg" for i in wanted if (out_dir / f"f{i:07d}.jpg").exists()}


def frames_for_shot(shot: dict) -> list[int]:
    frames = [shot["key_frame"]]
    if shot["duration"] >= 3.0:
        frames.append(shot["start"] + int((shot["end"] - shot["start"]) * 0.85))
    return frames


def _image_block(path: Path) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                       "data": base64.b64encode(path.read_bytes()).decode()}}


def describe_shots(client, video: Path, shots: list[dict], frames_dir: Path,
                   cost: CostTracker, progress=None) -> dict[int, dict]:
    if len(shots) > MAX_SHOTS:
        step = len(shots) / MAX_SHOTS
        shots = [shots[int(i * step)] for i in range(MAX_SHOTS)]
    wanted = {s["index"]: frames_for_shot(s) for s in shots}
    files = extract_frames(video, [f for fs in wanted.values() for f in fs], frames_dir)

    results: dict[int, dict] = {}
    for b in range(0, len(shots), BATCH):
        batch = shots[b:b + BATCH]
        content: list[dict] = []
        for s in batch:
            content.append({"type": "text", "text": (
                f"Shot {s['index']}: {s['start_time']:.2f}s, lasts {s['duration']:.2f}s, "
                f"enters by {s['transition_in']}, leaves by {s['transition_out']}"
                + (" (frames: middle, then near the end)" if len(wanted[s['index']]) > 1 else ""))})
            for f in wanted[s["index"]]:
                if f in files:
                    content.append(_image_block(files[f]))
        content.append({"type": "text", "text": INSTRUCTIONS})
        if progress:
            progress(f"Looking at shots {batch[0]['index']}-{batch[-1]['index']} of {shots[-1]['index']}")
        data = call_json(client, pass_name="study-vision", model=STUDY_MODEL, system=SYSTEM,
                         user=content, max_tokens=8000, cost_tracker=cost)
        for row in data.get("shots", []):
            try:
                results[int(row.get("index"))] = row
            except (TypeError, ValueError):
                continue
    return results
