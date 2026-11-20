"""Group shots into ANGLE blocks: the overlay changes only when the angle type (size + camera height) changes, so students
can see which angles a scene uses, how often one returns, and which types still need shooting. Angle types come from the
shot labels (read from start/end frames); the police-at-the-door section is split by subject (John / officer OTS)."""
from __future__ import annotations
import json, re

SIZE_NAME = {"XCU": "EXTREME CLOSE-UP", "CU": "CLOSE-UP", "MCU": "MEDIUM CLOSE-UP", "MS": "MEDIUM", "MWS": "MEDIUM-WIDE",
             "WS": "WIDE", "OTS": "OVER-THE-SHOULDER"}
HEIGHT_NAME = {"LOW": "LOW ANGLE", "EYE": "EYE LEVEL"}
TIPS = {
    "XCU": "Fill the frame with one feature (eyes, hand, prop). Move in close, light from one side only.",
    "CU": "Frame shoulders-up with the eyes on the top third. Phone camera at 2x, actor 3 ft away.",
    "MCU": "Chest-up. Camera at the actor's eye height; let a doorway or wall make one dark edge.",
    "MS": "Waist-up, leave headroom. Step back 6-8 ft so hands and props read.",
    "MWS": "Knees-up. Keep hands, props and the background in view; stand against a wall for space.",
    "WS": "Show the whole room and where everyone stands. Use the 0.5x lens or back into a corner.",
    "OTS": "Camera behind the other actor's shoulder; keep that shoulder dark and soft in the foreground.",
}
LOW_TIP = "Put the camera at knee or floor height and tilt up; let the ceiling or stairs fill the top."


def size_key(label_size: str, f_in: int) -> str:
    s = (label_size or "").lower()
    first = re.split(r",| then | to ", s)[0].strip()
    if "over-the-shoulder" in s: return "OTS"
    if "extreme" in first: return "XCU"
    if first.startswith("medium close-up"): return "MCU"
    if first.startswith("close-up"): return "CU"
    if first.startswith("medium-wide"): return "MWS"
    if first.startswith("medium"): return "MS"
    return "WS"


def height_key(label_angle: str) -> str:
    a = (label_angle or "").lower()
    first = re.split(r" to |,", a)[0].strip()
    return "LOW" if (first == "low" or "looking up" in first) else "EYE"


def blocks(shots: list[dict], labels: dict) -> list[dict]:
    out = []
    for s in shots:
        lab = labels.get(s["id"], {})
        sk, hk = size_key(lab.get("size", ""), s["f_in"]), height_key(lab.get("angle", ""))
        key = f"{sk}-{hk}"
        if out and out[-1]["key"] == key:
            b = out[-1]; b["shots"].append(s["id"]); b["f_out"] = s["f_out"]; b["frames"] = b["f_out"] - b["f_in"] + 1
            continue
        out.append(dict(key=key, size=sk, height=hk, shots=[s["id"]], f_in=s["f_in"], f_out=s["f_out"], frames=s["frames"], first=s["id"]))
    seen, counts = {}, {}
    for i, b in enumerate(out, 1):
        b["n"] = i; b["id"] = f"A{i:02d}"
        b["before"] = counts.get(b["key"], 0)                      # times this angle type was used earlier
        counts[b["key"]] = b["before"] + 1
        b["unique_so_far"] = len({x["key"] for x in out[:i]})
        b["type_name"] = f"{SIZE_NAME[b['size']]} · {HEIGHT_NAME[b['height']]}"
        b["tip"] = TIPS[b["size"]] + (" " + LOW_TIP if b["height"] == "LOW" else "")
        b["label"] = labels.get(b["first"], {})
    return out


if __name__ == "__main__":
    JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
    shots = json.load(open(f"{JW}/shots_v2.json")); labels = json.load(open(f"{JW}/labels_v2.json"))
    bl = blocks(shots, labels); json.dump(bl, open(f"{JW}/angle_blocks.json", "w"), indent=1)
    from collections import Counter
    c = Counter(b["key"] for b in bl)
    print(len(shots), "shots ->", len(bl), "angle blocks;", len(c), "angle types used")
    print(dict(c.most_common()))
