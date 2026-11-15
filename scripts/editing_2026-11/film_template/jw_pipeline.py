"""One pass from corrected cuts to angle blocks: snap cuts to the single-frame structural peak, drop known false cuts (flash,
flicker, camera swing), fingerprint every shot, label it, group by picture into angle blocks."""
import json, sys
import numpy as np
sys.path.insert(0, ".")
import jw_labels, jw_angles2
JW = "/private/tmp/claude-503/-Users-OPCALIFORNIA-theodore/2b4f9d35-ee54-5280-90ff-5d5f8269ad90/scratchpad/jw"
D = np.load(f"{JW}/conf2_D.npy"); F = np.load(f"{JW}/conf2_F.npy"); N = len(D)
v3 = json.load(open(f"{JW}/shots_v3.json")); old = json.load(open(f"{JW}/shots.json"))
FALSE = [345]                                   # muzzle flash, not a cut
REGION = [261, 863, 3574, 3896, 4256, 4307, 4420, 4501, 4699, 4782]   # checked frame by frame (the rest come from single-frame peaks)
cuts = []
for s in v3[1:]:
    c = s["f_in"]
    if c >= 3560 or any(abs(c - f) <= 1 for f in FALSE): continue
    cuts.append(int(max(range(c - 3, c + 4), key=lambda n: D[n])))
cuts = sorted({0} | set(cuts) | set(REGION)); END = 4844
shots = []
for k, c in enumerate(cuts):
    e = (cuts[k + 1] if k + 1 < len(cuts) else END) - 1
    shots.append(dict(n=k + 1, id=f"M01-L02-S{k + 1:02d}", f_in=int(c), f_out=int(e), frames=int(e - c + 1)))
json.dump(shots, open(f"{JW}/shots_v4.json", "w"))
weak = [(s["id"][-2:], s["f_in"], round(float(D[s["f_in"]]), 2)) for s in shots[1:] if D[s["f_in"]] < 0.35]
print(len(shots), "shots; cuts with weak single-frame evidence (D<0.35):", weak)
def z(a): a = a - a.mean(); return a / (a.std() + 1e-6)
def reps(s):
    f0, f1 = s["f_in"], s["f_out"]; L = f1 - f0 + 1
    pts = [f0 + 2, f0 + L // 3, f0 + L // 2, f0 + 2 * L // 3, max(f0, f1 - 2)] if L >= 10 else [f0, f1]
    return [z(np.median(F[max(f0, p - 1):min(f1, p + 1) + 1], axis=0)) for p in pts]
R = [reps(s) for s in shots]; n = len(shots); M = np.zeros((n, n))
for i in range(n):
    for j in range(n): M[i, j] = max(float((a * b).mean()) for a in R[i] for b in R[j])
np.save(f"{JW}/sim_v4.npy", M)
labels = jw_labels.labels_for(shots, old); json.dump(labels, open(f"{JW}/labels_v4.json", "w"), indent=1)
bl = jw_angles2.build(shots, labels, M); json.dump(bl, open(f"{JW}/angle_blocks_v4.json", "w"), indent=1)
print(len(bl), "angle blocks;", len({b["cluster"] for b in bl}), "distinct angles; returns:", [(b["id"], b["shots"][0][-2:] + ("-" + b["shots"][-1][-2:] if len(b["shots"]) > 1 else "")) for b in bl if b["before"]])
