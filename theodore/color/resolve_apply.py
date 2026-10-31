"""Grade a timeline to match a reference image, inside Resolve.

Per clip: sample the clip's own source frames (what node 1 sees), build its
LUT toward the reference (theodore.color.match), write it into Resolve's LUT
folder, refresh Resolve's LUT list, and set it on the clip's first node. On a
duplicate timeline by default; graphics and generators are left alone.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from theodore.color import cameras, match

LUT_ROOT = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
LUT_DIR = LUT_ROOT / "Theodore"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "look"


def _clip_fps(mpi) -> float:
    try:
        return float(str(mpi.GetClipProperty("FPS") or 24).split()[0])
    except ValueError:
        return 24.0


def _clip_pixels(h, mpi, path: str, a: float, b: float):
    """Source pixels as recorded (log stays log). ffmpeg reads ProRes/H.264/DNx;
    for RED R3D, ARRIRAW, BRAW Resolve renders a small proxy of the clip."""
    try:
        return match.frames_for_clip(path, a, b)
    except RuntimeError:
        from theodore.edit import reframe
        project, pool = h.project, h.project.GetMediaPool()
        current = project.GetCurrentTimeline()
        fps = _clip_fps(mpi)
        temp = pool.CreateTimelineFromClips(f"Theodore colour sample {abs(hash(path)) % 10**6}", [
            {"mediaPoolItem": mpi, "startFrame": int(a * fps), "endFrame": max(int(b * fps), int(a * fps) + 2)}])
        try:
            proxy = reframe.render_proxy(h, temp, 960, 540)
            return match.frames_for_clip(str(proxy), 0, max(b - a, 0.1))
        finally:
            if current:
                project.SetCurrentTimeline(current)
            pool.DeleteTimelines([temp])


def match_look(h, reference: str, *, strength: float = 0.85, per_clip: bool = True,
               on_duplicate: bool = True, name: Optional[str] = None, look_name: Optional[str] = None,
               camera_transform: str = "auto") -> dict:
    from theodore.resolve import graphics, timeline_ops

    ref_path = str(Path(reference).expanduser())
    if not Path(ref_path).exists():
        raise FileNotFoundError(f"No reference image at {ref_path}")
    ref = match.decode_image(ref_path)
    look = _slug(look_name or Path(ref_path).stem)
    tl = timeline_ops.duplicate_for_edit(h, name, f"look {look}") if on_duplicate else h.timeline

    shots = []
    for idx in range(1, int(tl.GetTrackCount("video") or 0) + 1):
        for it in tl.GetItemListInTrack("video", idx) or []:
            mpi = it.GetMediaPoolItem()
            path = mpi.GetClipProperty("File Path") if mpi else ""
            if not mpi or not path or graphics.is_compound(mpi):
                continue
            fps = _clip_fps(mpi)
            cam = (cameras.detect(mpi.GetClipProperty(), path) if camera_transform == "auto"
                   else {"key": "display", "label": "camera transform off"})
            shots.append((it, mpi, path, it.GetSourceStartFrame() / fps, it.GetSourceEndFrame() / fps, f"V{idx}", cam))
    if not shots:
        raise RuntimeError("No graded-able clips on this timeline (only graphics/generators?).")

    pixels = {}
    for n, (it, mpi, path, a, b, track, cam) in enumerate(shots):
        pixels[n] = cameras.to_display(cam["key"])(_clip_pixels(h, mpi, path, a, b))
    project_tl = h.project.GetCurrentTimeline()
    if project_tl is not None and project_tl.GetName() != tl.GetName():
        h.project.SetCurrentTimeline(tl)
    pooled = None
    if not per_clip:
        import numpy as np
        pooled = match.LookModel(np.concatenate(list(pixels.values())), ref, strength)

    out_dir = LUT_DIR / look
    written = []
    for n, (it, mpi, path, a, b, track, cam) in enumerate(shots):
        model = pooled or match.LookModel(pixels[n], ref, strength)
        cube = out_dir / f"{n + 1:03d}-{_slug(mpi.GetName())}-{match.cache_key(path, a, b, ref_path, strength, cam['key'])}.cube"
        match.write_cube(model, cube, title=f"Theodore {look}", pre=cameras.to_display(cam["key"]))
        written.append((it, mpi, path, a, track, cube, model, cam))

    h.project.RefreshLUTList()
    applied, failed = [], []
    for it, mpi, path, a, track, cube, model, cam in written:
        graph = it.GetNodeGraph()
        ok = False
        for candidate in (str(cube), str(cube.relative_to(LUT_ROOT))):
            if graph and graph.SetLUT(1, candidate):
                ok = True
                break
        (applied if ok else failed).append(f"{track} {mpi.GetName()}")

    preview = None
    try:
        _, _, path0, a0, _, cube0, _, _ = written[0]
        preview = str(match.preview(ref_path, path0, cube0, out_dir / "preview.jpg", seek=a0 + 0.5))
    except Exception:  # noqa: BLE001 -- the preview is a nicety
        pass
    summary = written[0][6].describe()
    (out_dir / "look.json").write_text(json.dumps({"reference": ref_path, "strength": strength,
                                                   "per_clip": per_clip, "look": summary}, indent=2))
    return {"timeline": tl.GetName(), "look": look, "reference": ref_path, "clips_graded": len(applied),
            "failed": failed, "per_clip_match": per_clip,
            "cameras": sorted({w[7]["label"] for w in written}),
            "lenses": sorted({str(w[1].GetClipProperty("Lens Type") or "") for w in written} - {""}), "strength": strength, "first_clip_look": summary,
            "luts_in": str(out_dir), "preview_image": preview,
            "note": "LUT on node 1 of each clip. Refine on the Color page; lower strength for subtler."}
