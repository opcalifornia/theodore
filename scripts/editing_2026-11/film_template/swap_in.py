"""Match slated footage to shots. DRY RUN ONLY: reads, reports, writes nothing to the timeline.

  python swap_in.py --bin "Dailies" [--analyze]

--analyze runs Resolve's Slate Finder on the bin first (folder.AnalyzeForSlate) and waits for it. Whether the
scripting call also fills Scene / Take is UNTESTED (it needs real slated footage). Where metadata is empty the
report says so and the clip must be slated by hand (Metadata Editor) or via the Slate ID button in the UI.
Matching: a clip's Scene metadata equals a shot's scene (e.g. "4A"); the highest Take number wins; ties and
missing takes are flagged for the editor, never guessed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_template as T  # noqa: E402
from theodore.resolve import connection  # noqa: E402


def norm(scene: str) -> str:
    return re.sub(r"\s+", "", (scene or "").upper()).lstrip("0") or ""


def take_no(v: str) -> int:
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else -1


def main() -> None:
    args = sys.argv[1:]
    name = args[args.index("--bin") + 1] if "--bin" in args else "Dailies"
    h = connection.connect()
    project, pool = h.project, h.project.GetMediaPool()

    def find(folder):
        if folder.GetName() == name:
            return folder
        for sub in folder.GetSubFolderList() or []:
            got = find(sub)
            if got:
                return got
    bin_ = find(pool.GetRootFolder())
    if bin_ is None:
        sys.exit(f"No bin named {name!r} in this project. Import the dailies into a bin with that name "
                 "(or pass --bin NAME). Nothing was changed.")
    if "--analyze" in args:
        print("AnalyzeForSlate:", bin_.AnalyzeForSlate())
        for _ in range(600):
            st = project.GetAnalyzeForSlateStatus() or {}
            if st.get("JobStatus") in (None, "Inactive", "Complete", "Completed", "Failed"):
                break
            time.sleep(2)
        print("analysis status:", project.GetAnalyzeForSlateStatus())
    data = json.load(open(os.path.join(T.MEDIA, "plan.json")))
    shots = {norm(s["scene"]): s for s in data["plan"]["shots"]}
    clips = list(bin_.GetClipList() or [])
    print(f"{len(clips)} clip(s) in {name!r}\n")
    groups, unslated = {}, []
    for c in clips:
        sc, tk = c.GetMetadata("Scene"), c.GetMetadata("Take")
        if not norm(sc):
            unslated.append(c.GetName())
            continue
        groups.setdefault(norm(sc), []).append((take_no(tk), c.GetName(), tk))
    for scene, takes in sorted(groups.items()):
        shot = shots.get(scene)
        takes.sort(key=lambda t: t[0])
        if shot is None:
            print(f"scene {scene}: no shot in the plan -> {[t[1] for t in takes]}")
            continue
        best = takes[-1]
        flag = "" if best[0] >= 0 and [t[0] for t in takes].count(best[0]) == 1 else "  << take number missing/duplicate: editor decides"
        print(f"{shot['id']} (scene {scene}, planned {shot['frames'] / 24:g} s): {len(takes)} take(s), would use '{best[1]}'{flag}")
    for k in sorted(set(shots) - set(groups)):
        print(f"{shots[k]['id']} (scene {k}): no footage yet")
    if unslated:
        print(f"\n{len(unslated)} clip(s) with no Scene metadata (not slated / not read): {unslated[:8]}")


if __name__ == "__main__":
    main()
