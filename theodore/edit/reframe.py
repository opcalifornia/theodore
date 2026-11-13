"""Speaker-follow vertical: one wide shot with several people -> a 9:16 cut
that punches in on whoever is talking.

Works on any camera Resolve reads (RED R3D, ARRIRAW, BRAW, ProRes...): the
analysis runs on a small proxy that Resolve itself renders, not on the
camera files.

1. Proxy: Resolve renders the timeline at 640x360 to a temp folder.
2. People: Claude looks at frames and boxes every visible face.
3. Lines: Resolve's transcript of the timeline, as sentences with frames.
4. Speaker per line: the face whose mouth area moves most during the line,
   relative to how much that face moves when others talk.
5. Build: a 9:16 duplicate where every line is its own segment, framed on its
   speaker (face in the upper third, zoomed so the frame is filled, never
   past an edge). Speaker changes are cuts; consecutive lines by the same
   person stay one shot.

Pan/Tilt in Resolve aren't plain pixels (Pan's scale depends on the source
shape), so each source/timeline shape is calibrated once with a test dot
and cached.
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

from theodore import config
from theodore.analyze.claude_client import CostTracker, call_json
from theodore.edit import VISION_MODEL
from theodore.llm import make_client

FACE_Y = 0.36        # where the speaker's face sits, from the top of the 9:16 frame
DEFAULT_ZOOM = 1.35  # punch-in on a speaker (on top of the fill-the-frame scale)


class ReframeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 1. analysis proxy, rendered by Resolve (reads any camera format)
# ---------------------------------------------------------------------------

def render_proxy(h, tl, width: int = 640, height: int = 360, timeout: float = 1800) -> Path:
    project = h.project
    page = h.resolve.GetCurrentPage()   # rendering moves Resolve to Deliver; put the editor back
    out_dir = Path(tempfile.mkdtemp(prefix="theodore-proxy-"))
    name = f"proxy_{int(time.time())}"
    project.SetCurrentTimeline(tl)
    project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    ok = project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(out_dir), "CustomName": name,
                                    "ExportVideo": True, "ExportAudio": True, "FormatWidth": width,
                                    "FormatHeight": height})
    if not ok:
        raise ReframeError("Resolve rejected the analysis-proxy render settings.")
    job = project.AddRenderJob()
    if not job:
        raise ReframeError("Resolve would not queue the analysis proxy.")
    try:
        project.StartRendering([job], isInteractiveMode=False)
        start = time.time()
        while project.IsRenderingInProgress():
            if time.time() - start > timeout:
                project.StopRendering()
                raise ReframeError("The analysis proxy took too long to render.")
            time.sleep(1)
    finally:
        try:
            project.DeleteRenderJob(job)  # leave the editor's render queue as it was
        except Exception:  # noqa: BLE001
            pass
        if page:
            h.resolve.OpenPage(page)
    files = sorted(out_dir.glob(f"{name}*"))
    if not files:
        raise ReframeError("Resolve finished but wrote no proxy file.")
    return files[0]


# ---------------------------------------------------------------------------
# 2. faces, from Claude looking at frames
# ---------------------------------------------------------------------------

FACES_PROMPT = """These are frames from one locked-off wide shot. Box every person's FACE \
(forehead to chin) who appears in the shot. Use the same order left-to-right in every answer. \
Return ONLY JSON: {"people": [{"label": "<short description, e.g. 'man in grey hoodie'>", \
"face": [x0, y0, x1, y1]}]} with coordinates as fractions of the frame (0,0 = top-left). \
Use the frame where each face is clearest."""


def find_faces(proxy: Path, duration: float, client=None) -> list[dict]:
    client = client or make_client()
    content = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, frac in enumerate((0.2, 0.5, 0.8)):
            out = Path(tmp) / f"{k}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{duration * frac:.2f}", "-i", str(proxy),
                            "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True, timeout=120)
            if out.exists():
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                           "data": base64.b64encode(out.read_bytes()).decode()}})
    content.append({"type": "text", "text": FACES_PROMPT})
    data = call_json(client, pass_name="reframe-faces", model=VISION_MODEL,
                     system="You locate people in video frames precisely.", user=content,
                     max_tokens=1500, cost_tracker=CostTracker())
    people = []
    for p in data.get("people", []):
        box = [float(v) for v in (p.get("face") or [])][:4]
        if len(box) == 4 and box[2] > box[0] and box[3] > box[1]:
            people.append({"label": p.get("label") or f"person {len(people) + 1}", "face": box})
    if not people:
        raise ReframeError("Claude found no faces in the shot.")
    return sorted(people, key=lambda p: p["face"][0])


# ---------------------------------------------------------------------------
# 3-4. who says each line
# ---------------------------------------------------------------------------

def _gray_frames(proxy: Path, fps: int = 12, w: int = 320, h: int = 180) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(proxy), "-vf", f"fps={fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, timeout=1800).stdout
    a = np.frombuffer(raw, np.uint8)
    return a[: len(a) // (w * h) * (w * h)].reshape(-1, h, w).astype(np.float32)


def mouth_motion(proxy: Path, people: list[dict], fps: int = 12) -> np.ndarray:
    """(people, frames) motion in the lower half of each face box."""
    frames = _gray_frames(proxy, fps)
    if len(frames) < 2:
        return np.zeros((len(people), 1))
    _, h, w = frames.shape
    diff = np.abs(np.diff(frames, axis=0))
    out = []
    for p in people:
        x0, y0, x1, y1 = p["face"]
        ya, yb = int((y0 + (y1 - y0) * 0.45) * h), int(min(y1 + (y1 - y0) * 0.1, 1) * h)
        xa, xb = int(x0 * w), int(x1 * w)
        region = diff[:, max(ya, 0):max(yb, ya + 1), max(xa, 0):max(xb, xa + 1)]
        out.append(region.mean(axis=(1, 2)))
    return np.stack(out)


def attribute_lines(sentences: list[dict], motion: np.ndarray, fps: int = 12) -> list[int]:
    """Index of the speaking person for each sentence."""
    if motion.shape[0] == 1:
        return [0] * len(sentences)
    base = np.median(motion, axis=1, keepdims=True) + 1e-6
    norm = motion / base      # each face against its own resting motion
    who = []
    for s in sentences:
        a, b = int(s["start"] * fps), max(int(s["end"] * fps), int(s["start"] * fps) + 1)
        seg = norm[:, a:b]
        who.append(int(np.argmax(seg.mean(axis=1))) if seg.size else (who[-1] if who else 0))
    return who


# ---------------------------------------------------------------------------
# calibration of Pan / Tilt / fill scale for a source and timeline shape
# ---------------------------------------------------------------------------

def _calibration_file() -> Path:
    return Path(config.DATA_ROOT) / "calibration" / "reframe.json"


def calibrate(h, src_w: int, src_h: int, tl_w: int, tl_h: int) -> dict:
    key = f"{src_w}x{src_h}->{tl_w}x{tl_h}"
    cache = _calibration_file()
    data = json.loads(cache.read_text()) if cache.exists() else {}
    if key in data:
        return data[key]
    from theodore.resolve import timecode
    project, pool = h.project, h.project.GetMediaPool()
    tmp = Path(tempfile.mkdtemp(prefix="theodore-cal-"))
    dot = tmp / "dot.mov"
    sz = max(src_w // 48, 8)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=black:size={src_w}x{src_h}:rate=24:duration=2,drawbox=x={src_w // 2 - sz // 2}:"
                    f"y={src_h // 2 - sz // 2}:w={sz}:h={sz}:color=white:t=fill",
                    "-c:v", "prores_ks", "-profile:v", "0", str(dot)], check=True, capture_output=True)
    original = project.GetCurrentTimeline()
    clip = pool.ImportMedia([str(dot)])[0]
    tl = pool.CreateTimelineFromClips(f"Theodore calibration {int(time.time())}", [clip])
    try:
        project.SetCurrentTimeline(tl)
        for k, v in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(tl_w)),
                     ("timelineResolutionHeight", str(tl_h)), ("timelineInputResMismatchBehavior", "scaleToCrop")):
            tl.SetSetting(k, v)
        h.resolve.OpenPage("edit")
        item = tl.GetItemListInTrack("video", 1)[0]
        frame = tl.GetStartFrame() + 12

        def centre(pan=0.0, tilt=0.0):
            item.SetProperty("Pan", pan)
            item.SetProperty("Tilt", tilt)
            tl.SetCurrentTimecode(timecode.frames_to_timecode(frame, "24"))
            still = tmp / "still.png"
            project.ExportCurrentFrameAsStill(str(still))
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(still), "-vf", f"scale={tl_w}:{tl_h},format=gray",
                                  "-f", "rawvideo", "-"], capture_output=True).stdout
            img = np.frombuffer(raw, np.uint8).reshape(tl_h, tl_w)
            ys, xs = np.nonzero(img > 128)
            if not len(xs):
                raise ReframeError("Calibration dot not found.")
            return xs.mean(), ys.mean(), xs.max() - xs.min() + 1
        x0, y0, size = centre()
        x1, _, _ = centre(pan=10)
        _, y1, _ = centre(tilt=10)
        result = {"scale": float(size / sz), "px_per_pan": float((x1 - x0) / 10), "px_per_tilt": float((y1 - y0) / 10)}
    finally:
        if original:
            project.SetCurrentTimeline(original)
        pool.DeleteTimelines([tl])
        try:
            pool.DeleteClips([clip])
        except Exception:  # noqa: BLE001
            pass
    data[key] = result
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=2))
    return result


def framing(face: list[float], src_w: int, src_h: int, tl_w: int, tl_h: int, cal: dict,
            zoom: float = DEFAULT_ZOOM) -> dict:
    """Pan/Tilt/Zoom that put this face centred, in the upper third, frame filled."""
    s = cal["scale"] * zoom
    fx, fy = (face[0] + face[2]) / 2, (face[1] + face[3]) / 2
    # Where the face would land with no pan/tilt, relative to the frame centre (output px).
    dx = (fx - 0.5) * src_w * s
    dy = (fy - 0.5) * src_h * s
    want_x, want_y = 0.0, (FACE_Y - 0.5) * tl_h
    shift_x, shift_y = want_x - dx, want_y - dy
    # Never show past the picture's edge.
    max_x = max((src_w * s - tl_w) / 2, 0)
    max_y = max((src_h * s - tl_h) / 2, 0)
    shift_x, shift_y = float(np.clip(shift_x, -max_x, max_x)), float(np.clip(shift_y, -max_y, max_y))
    return {"Pan": shift_x / cal["px_per_pan"], "Tilt": shift_y / cal["px_per_tilt"], "ZoomX": zoom, "ZoomY": zoom}


# ---------------------------------------------------------------------------
# 5. build
# ---------------------------------------------------------------------------

def speaker_follow(h, *, aspect: str = "9:16", zoom: float = DEFAULT_ZOOM, names: Optional[list[str]] = None,
                   name: Optional[str] = None, progress=None) -> dict:
    from theodore.resolve import timeline_ops, transcribe

    say = progress or (lambda m: None)
    source_tl, project = h.timeline, h.project
    items = [it for it in (source_tl.GetItemListInTrack("video", 1) or []) if it.GetMediaPoolItem()]
    if not items:
        raise ReframeError("No clips on V1 to reframe.")
    fps = float(str(source_tl.GetSetting("timelineFrameRate") or 24).split()[0])
    tl_start = int(source_tl.GetStartFrame())

    say("Resolve is rendering a small analysis copy")
    proxy = render_proxy(h, source_tl)
    project.SetCurrentTimeline(source_tl)
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                     "csv=p=0", str(proxy)], capture_output=True, text=True).stdout or 0)
    say("Finding everyone in the shot")
    people = find_faces(proxy, duration)
    if names:
        for p, n in zip(people, names):
            p["label"] = n
    say("Transcribing and working out who says each line")
    tr = transcribe.transcribe_timeline(h, word_level=True)
    sentences = tr["sentences"]
    if not sentences:
        raise ReframeError("No speech found to follow.")
    who = attribute_lines(sentences, mouth_motion(proxy, people))

    # Shots: consecutive lines by the same person are one shot; each shot runs
    # from its first line to the next speaker's first line (no gaps).
    shots = []
    for s, w in zip(sentences, who):
        if shots and shots[-1]["who"] == w:
            continue
        shots.append({"who": w, "start": tl_start + s["start_frame"], "line": s["text"]})
    timeline_end = max(it.GetEnd() for it in items)
    for i, shot in enumerate(shots):
        shot["start"] = tl_start if i == 0 else shot["start"] - 2  # cut a hair before the first word
        shot["end"] = shots[i + 1]["start"] - 2 if i + 1 < len(shots) else timeline_end

    say("Building the vertical cut")
    tw, th = timeline_ops.ASPECTS.get(aspect, (1080, 1920))
    dup = timeline_ops.duplicate_for_edit(h, name, f"{aspect.replace(':', 'x')} speaker follow")
    for k, v in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(tw)), ("timelineResolutionHeight", str(th)),
                 ("timelineOutputResolutionWidth", str(tw)), ("timelineOutputResolutionHeight", str(th)),
                 ("timelineInputResMismatchBehavior", "scaleToCrop")):
        dup.SetSetting(k, v)
    snaps = [timeline_ops._snapshot(it, "video", 1) for it in (dup.GetItemListInTrack("video", 1) or [])
             if it.GetMediaPoolItem()]
    dup.DeleteClips([s.item for s in snaps], False)
    pool = project.GetMediaPool()
    placed, cal_cache = [], {}
    for shot in shots:
        for s in snaps:
            lo, hi = max(shot["start"], s.start), min(shot["end"], s.end)
            if hi <= lo:
                continue
            got = timeline_ops._placed_item(pool.AppendToTimeline([{
                "mediaPoolItem": s.mpi, "startFrame": s.src_in + (lo - s.start), "endFrame": s.src_in + (hi - s.start),
                "recordFrame": lo, "trackIndex": 1, "mediaType": 1}]))
            if got is None:
                continue
            res = str(s.mpi.GetClipProperty("Resolution") or "1920x1080")
            try:
                sw, sh = (int(v) for v in res.lower().split("x")[:2])
            except ValueError:
                sw, sh = 1920, 1080
            key = (sw, sh)
            if key not in cal_cache:
                cal_cache[key] = calibrate(h, sw, sh, tw, th)
                project.SetCurrentTimeline(dup)
            for k, v in framing(people[shot["who"]]["face"], sw, sh, tw, th, cal_cache[key], zoom).items():
                got.SetProperty(k, v)
            try:
                s.item.CopyGrades([got])
            except Exception:  # noqa: BLE001
                pass
            placed.append(shot)
    return {"timeline": dup.GetName(), "from": source_tl.GetName(), "people": [p["label"] for p in people],
            "shots": [{"at_seconds": round((sh["start"] - tl_start) / fps, 2), "on": people[sh["who"]]["label"],
                       "line": sh["line"][:80]} for sh in shots],
            "resolution": f"{tw}x{th}", "zoom": zoom,
            "note": "Each shot is its own clip on V1 with Pan/Tilt/Zoom set -- adjust any one in the Inspector."}
