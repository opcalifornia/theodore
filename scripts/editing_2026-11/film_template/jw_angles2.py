"""Angle blocks from the PICTURE, not from label wording: shots whose images match (fine edge fingerprint) share one angle ID;
the overlay changes when the angle changes, and an angle that comes back is flagged as a return. Size/height type for the
diversity grid still comes from the shot labels."""
from __future__ import annotations
import json
import numpy as np
from jw_angles import SIZE_NAME, HEIGHT_NAME, TIPS, LOW_TIP, size_key, height_key

T = 0.85


def build(shots, labels, sim):
    n = len(shots); cl = [-1] * n; nxt = 0
    for k in range(n):
        best, who = 0.0, -1
        for j in range(k):
            if sim[k, j] > best: best, who = sim[k, j], j
        if who >= 0 and best >= T: cl[k] = cl[who]
        else: cl[k] = nxt; nxt += 1
    out = []
    for k, s in enumerate(shots):
        lab = labels.get(s["id"], {})
        if out and out[-1]["cluster"] == cl[k]:
            b = out[-1]; b["shots"].append(s["id"]); b["f_out"] = s["f_out"]; b["frames"] = b["f_out"] - b["f_in"] + 1; continue
        sk, hk = size_key(lab.get("size", ""), s["f_in"]), height_key(lab.get("angle", ""))
        out.append(dict(cluster=cl[k], key=f"{sk}-{hk}", size=sk, height=hk, shots=[s["id"]], f_in=s["f_in"], f_out=s["f_out"], frames=s["frames"], first=s["id"], label=lab,
                        type_name=f"{SIZE_NAME[sk]} · {HEIGHT_NAME[hk]}", tip=TIPS[sk] + (" " + LOW_TIP if hk == "LOW" else "")))
    seen, keys = {}, set()
    for i, b in enumerate(out, 1):
        b["n"] = i; b["id"] = f"A{b['cluster'] + 1:02d}"
        b["before"] = seen.get(b["cluster"], 0); seen[b["cluster"]] = b["before"] + 1
        keys.add(b["key"]); b["unique_so_far"] = len(keys)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import jw_labels
    JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
    shots = json.load(open(f"{JW}/shots_v3.json")); old = json.load(open(f"{JW}/shots.json"))
    labels = jw_labels.labels_for(shots, old); json.dump(labels, open(f"{JW}/labels_v3.json", "w"), indent=1)
    sim = np.load(f"{JW}/sim_fine.npy")
    bl = build(shots, labels, sim); json.dump(bl, open(f"{JW}/angle_blocks_v2.json", "w"), indent=1)
    print(len(shots), "shots ->", len(bl), "angle blocks;", len({b["cluster"] for b in bl}), "distinct angles;", "returns:", sum(1 for b in bl if b["before"]))
    print([(b["id"], b["shots"][0][-2:] + ("-" + b["shots"][-1][-2:] if len(b["shots"]) > 1 else "")) for b in bl][-16:])
