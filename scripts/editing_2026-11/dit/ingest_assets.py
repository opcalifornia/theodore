"""DIT asset intake: sort music, scoring, transitions, SFX and graphics into 'Project Assets' next to the raw footage,
import them into matching Resolve bins, and (optionally) place one where the editor said.
Copies only: originals are never moved or deleted. Usage:
  ingest_assets.py --root <folder that holds the raw footage> [--resolve "Project name"] [--place "file@frame:track"] files...
"""
import argparse, json, os, shutil, sys
from pathlib import Path

KINDS = [  # (folder, filename hints, extensions)
    ("Transitions", ("transition", "swish", "wipe", "zip", "whip", "glitch", "flash", "dissolve", "wipe"), ()),
    ("Scoring",     ("score", "scoring", "cue", "stem", "theme", "underscore"), ()),
    ("SFX",         ("sfx", "foley", "whoosh", "hit", "impact", "riser", "sting", "ambience", "ambient"), ()),
    ("Music",       ("music", "song", "track", "bed", "beat"), (".mp3", ".wav", ".aif", ".aiff", ".m4a", ".flac")),
    ("Graphics",    ("logo", "title", "lower", "card", "graphic"), (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".svg", ".mov", ".mp4")),
    ("Presets",     (), (".drx", ".cube", ".drfx", ".dctl", ".3dl")),
]

def classify(p: Path) -> str:
    n = p.stem.lower().replace("_", " ").replace("-", " ")
    for folder, hints, exts in KINDS:
        if any(h in n for h in hints): return folder
    for folder, hints, exts in KINDS:
        if p.suffix.lower() in exts: return folder
    return "Other"

def unique(dst: Path) -> Path:
    i = 1; out = dst
    while out.exists():
        out = dst.with_name(f"{dst.stem} ({i}){dst.suffix}"); i += 1
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); ap.add_argument("--resolve"); ap.add_argument("--place"); ap.add_argument("files", nargs="+")
    a = ap.parse_args(); root = Path(a.root).expanduser() / "Project Assets"; manifest = []
    for f in a.files:
        src = Path(f).expanduser()
        if not src.is_file(): print("skip (not a file):", src); continue
        kind = classify(src); dst = unique(root / kind / src.name); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
        manifest.append(dict(kind=kind, source=str(src), copy=str(dst)))
        print(f"{kind:12s} {src.name}  ->  {dst}")
    (root).mkdir(parents=True, exist_ok=True); (root / "manifest.json").write_text(json.dumps(manifest, indent=1))
    if not a.resolve: return
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from theodore.resolve.connection import connect
    h = connect(); r = getattr(h, "resolve", h); pm = r.GetProjectManager()
    if pm.GetCurrentProject().GetName() != a.resolve: pm.LoadProject(a.resolve)
    pj = pm.GetCurrentProject(); mp = pj.GetMediaPool(); rootf = mp.GetRootFolder()
    def sub(parent, name):
        for s in parent.GetSubFolderList() or []:
            if s.GetName() == name: return s
        return mp.AddSubFolder(parent, name)
    top = sub(rootf, "Project Assets"); placed = {}
    for kind in sorted({m["kind"] for m in manifest}):
        mp.SetCurrentFolder(sub(top, kind)); items = mp.ImportMedia([m["copy"] for m in manifest if m["kind"] == kind]) or []
        for it in items: placed[it.GetName()] = it
        print(f"imported {len(items)} into Project Assets/{kind}")
    if a.place:
        name, rest = a.place.rsplit("@", 1); frame, track = (rest.split(":") + ["1"])[:2]
        it = placed.get(name) or next((v for k, v in placed.items() if name.lower() in k.lower()), None)
        tl = pj.GetCurrentTimeline()
        if it is None or tl is None: print("could not place:", name); return
        is_audio = it.GetClipProperty("Type") == "Audio"
        got = mp.AppendToTimeline([{"mediaPoolItem": it, "recordFrame": int(tl.GetStartFrame()) + int(frame), "mediaType": 2 if is_audio else 1, "trackIndex": int(track)}])
        print("placed", name, "at", frame, "track", track, bool(got))

if __name__ == "__main__": main()
