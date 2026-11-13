"""Speaker-follow cut for Creative Couch Ep2 on the editor's sync (clock 0 = frame 86400 of 'Synced - RED + Lavs')."""
import pickle, json, numpy as np
from fractions import Fraction as F
from theodore.edit.panel import HOP, _runs
S = "/private/tmp/claude-501/-Users-admin-theodore/aa91f910-6ebd-5bc7-80ad-a021f2ecd6b8/scratchpad/mc"
m = pickle.load(open(f"{S}/masks.pkl", "rb"))
IN, OUT = 1085, 46814                          # synced-timeline frames after 86400: 45.21 s .. 1950.58 s
RED, ALEXA = "RED - LaShonda", "Alexa - Alisona"
who_cam = {"Lav1": RED, "Lav2": ALEXA}
alexa_ok = [(335/24, 42515/24), (44587/24, 46999/24)]   # A036, A037 on the clock
def alexa_at(t): return any(a + 0.1 <= t <= b - 0.1 for a, b in alexa_ok)
MIN_TURN, MIN_SHOT, LEAD = 1.0, 1.5, 0.35
# frame by frame: the one person talking; nobody or both -> hold the current angle
names = list(m); n = len(m[names[0]])
t_in, t_out = IN / 24, OUT / 24
cur = ALEXA
cuts = [[t_in, cur, "opening: Alisona intro"]]
for k in range(int(t_in / HOP), min(n, int(t_out / HOP))):
    on = [p for p in names if m[p][k]]
    if len(on) != 1: continue
    want = who_cam[on[0]]
    if want != cuts[-1][1]:
        cuts.append([max(k * HOP - LEAD, t_in), want, ("LaShonda" if on[0] == "Lav1" else "Alisona") + " talking"])
# card swap: Alexa missing -> RED
fixed = []
for i, (a, cam, why) in enumerate(cuts):
    b = cuts[i + 1][0] if i + 1 < len(cuts) else t_out
    if cam == ALEXA:
        for ga, gb in ((42515/24, 44587/24),):
            if a < gb and b > ga:
                fixed += [[a, cam, why]] if a < ga else []
                fixed += [[max(a, ga), RED, "Alexa card swap"]]
                if b > gb: fixed += [[gb, ALEXA, why]]
                break
        else: fixed.append([a, cam, why])
    else: fixed.append([a, cam, why])
cuts = fixed
# frame snap, merge same-angle neighbours, no shot under MIN_SHOT (fold into the shot before)
fr = lambda t: int(round(t * 24))
segs = []
for i, (a, cam, why) in enumerate(cuts):
    b = cuts[i + 1][0] if i + 1 < len(cuts) else t_out
    A, B = fr(a), fr(b)
    if B <= A: continue
    if segs and segs[-1][2] == cam: segs[-1][1] = B; continue
    segs.append([A, B, cam, why])
changed = True
while changed:
    changed = False
    for i in range(1, len(segs)):
        if segs[i][1] - segs[i][0] < MIN_SHOT * 24 and not (segs[i][3] == "Alexa card swap"):
            segs[i - 1][1] = segs[i][1]; del segs[i]
            if i < len(segs) and segs[i][2] == segs[i - 1][2]:
                segs[i - 1][1] = segs[i][1]; del segs[i]
            changed = True; break
assert segs[0][0] == IN and segs[-1][1] == OUT and all(x[1] == y[0] for x, y in zip(segs, segs[1:]))
json.dump(segs, open(f"{S}/segs.json", "w"))
from collections import Counter
print(len(segs), "shots;", Counter(s[2] for s in segs), "seconds:", {c: round(sum(s[1]-s[0] for s in segs if s[2]==c)/24) for c in (RED, ALEXA)})
print("shortest", min(s[1]-s[0] for s in segs)/24, "longest", max(s[1]-s[0] for s in segs)/24)
for s in segs[:15]: print(round(s[0]/24,2), round(s[1]/24,2), s[2], s[3])
