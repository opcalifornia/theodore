"""Write shot-list data into Resolve and keep status visible.

For every placeholder: Scene / Shot / Description / Comments / Keywords / Camera # / Roll Card # metadata on the
media pool clip (so Resolve's own columns, Smart Bins and Metadata export work), plus a clip colour for its status
on the pool clip and on its timeline item. Never deletes anything.

  python stamp_metadata.py                      # stamp every shot from shot_list.csv
  python stamp_metadata.py --set hook=shot      # change one shot's status in the CSV, then re-stamp
Statuses: planned, shot, selected, synced, graded.
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_template as T  # noqa: E402
from theodore.resolve import connection  # noqa: E402

STATUS_COLOR = {"planned": "Beige", "shot": "Yellow", "selected": "Green", "synced": "Teal", "graded": "Purple"}
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_list.csv")


def set_status(updates: dict) -> None:
    rows = list(csv.DictReader(open(CSV_PATH, newline="")))
    keys = {r["key"] for r in rows}
    for k, v in updates.items():
        if k not in keys:
            sys.exit(f"no shot with key {k!r} in shot_list.csv (keys: {sorted(keys)})")
        if v not in STATUS_COLOR:
            sys.exit(f"status must be one of {sorted(STATUS_COLOR)}")
    for r in rows:
        if r["key"] in updates:
            r["status"] = updates[r["key"]]
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def walk(folder):
    for c in folder.GetClipList() or []:
        yield c
    for sub in folder.GetSubFolderList() or []:
        yield from walk(sub)


def main() -> None:
    updates = {}
    for a in sys.argv[1:]:
        if a.startswith("--set"):
            continue
        if "=" in a:
            k, v = a.split("=", 1)
            updates[k] = v
    if updates:
        set_status(updates)
    data = json.load(open(os.path.join(T.MEDIA, "plan.json")))
    extra = T.load_shot_list()[2]
    h = connection.connect()
    tl, pool = h.timeline, h.project.GetMediaPool()
    lesson = next(f for f in pool.GetRootFolder().GetSubFolderList() if f.GetName() == T.LESSON_BIN)
    by_id = {}
    for c in walk(lesson):
        by_id.setdefault(c.GetName()[:11], []).append(c)
    items = {}
    for i in range(1, int(tl.GetTrackCount("video")) + 1):
        for it in tl.GetItemListInTrack("video", i) or []:
            if it.GetType() != "transition":
                items.setdefault(it.GetName()[:11], []).append(it)
    ok = bad = 0
    for s in data["plan"]["shots"]:
        x = extra.get(s["key"], {})
        status = x.get("status") or "planned"
        planned = [f"{k.replace('planned_', '').upper()} {x[k]}" for k in ("planned_iso", "planned_kelvin", "planned_tint") if x.get(k)]
        comment = " | ".join(p for p in (x.get("location") and f"Location: {x['location']}",
                                         planned and "Planned " + ", ".join(planned),
                                         x.get("notes")) if p)
        meta = {"Scene": s["scene"], "Shot": s["id"], "Description": s["desc"], "Comments": comment,
                "Keywords": f"{status}, {s['kind']}, {s['track']}", "Camera #": s["camera"], "Roll Card #": s["roll"]}
        for c in by_id.get(s["id"], []):
            good = all(c.SetMetadata(k, v) for k, v in meta.items())
            good = c.SetClipColor(STATUS_COLOR[status]) and good
            ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        for it in items.get(s["id"], []):
            it.SetClipColor(STATUS_COLOR[status])
    # read-back
    wrong = 0
    for s in data["plan"]["shots"]:
        want = (extra.get(s["key"], {}).get("status") or "planned")
        for c in by_id.get(s["id"], []):
            if c.GetMetadata("Shot") != s["id"] or c.GetMetadata("Scene") != s["scene"] or c.GetClipColor() != STATUS_COLOR[want]:
                wrong += 1
    print(f"stamped {ok} pool clips ({bad} write failures); read-back mismatches: {wrong}")
    from collections import Counter
    print("status:", dict(Counter((extra.get(s['key'], {}).get('status') or 'planned') for s in data['plan']['shots'])))
    h.project_manager.SaveProject()


if __name__ == "__main__":
    main()
