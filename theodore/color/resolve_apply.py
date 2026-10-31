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

from theodore.color import match

LUT_ROOT = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
LUT_DIR = LUT_ROOT / "Theodore"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "look"


def _clip_fps(mpi) -> float:
    try:
        return float(str(mpi.GetClipProperty("FPS") or 24).split()[0])
    except ValueError:
        return 24.0


def match_look(h, reference: str, *, strength: float = 0.85, per_clip: bool = True,
               on_duplicate: bool = True, name: Optional[str] = None, look_name: Optional[str] = None) -> dict:
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
            shots.append((it, mpi, path, it.GetSourceStartFrame() / fps, it.GetSourceEndFrame() / fps, f"V{idx}"))
    if not shots:
        raise RuntimeError("No graded-able clips on this timeline (only graphics/generators?).")

    pooled = None
    if not per_clip:
        import numpy as np
        pooled = match.LookModel(np.concatenate([match.frames_for_clip(p, a, b, 3) for _, _, p, a, b, _ in shots]),
                                 ref, strength)

    out_dir = LUT_DIR / look
    written = []
    for n, (it, mpi, path, a, b, track) in enumerate(shots):
        model = pooled or match.LookModel(match.frames_for_clip(path, a, b), ref, strength)
        cube = out_dir / f"{n + 1:03d}-{_slug(mpi.GetName())}-{match.cache_key(path, a, b, ref_path, strength)}.cube"
        match.write_cube(model, cube, title=f"Theodore {look}")
        written.append((it, mpi, path, a, track, cube, model))

    h.project.RefreshLUTList()
    applied, failed = [], []
    for it, mpi, path, a, track, cube, model in written:
        graph = it.GetNodeGraph()
        ok = False
        for candidate in (str(cube), str(cube.relative_to(LUT_ROOT))):
            if graph and graph.SetLUT(1, candidate):
                ok = True
                break
        (applied if ok else failed).append(f"{track} {mpi.GetName()}")

    preview = None
    try:
        _, _, path0, a0, _, cube0, _ = written[0]
        preview = str(match.preview(ref_path, path0, cube0, out_dir / "preview.jpg", seek=a0 + 0.5))
    except Exception:  # noqa: BLE001 -- the preview is a nicety
        pass
    summary = written[0][6].describe()
    (out_dir / "look.json").write_text(json.dumps({"reference": ref_path, "strength": strength,
                                                   "per_clip": per_clip, "look": summary}, indent=2))
    return {"timeline": tl.GetName(), "look": look, "reference": ref_path, "clips_graded": len(applied),
            "failed": failed, "per_clip_match": per_clip, "strength": strength, "first_clip_look": summary,
            "luts_in": str(out_dir), "preview_image": preview,
            "note": "LUT on node 1 of each clip. Refine on the Color page; lower strength for subtler."}
