"""Segment each take into runs: per-window song matches (10 s windows every 2 s over the FULL camera audio), then a Viterbi pass that keeps the
same offset while the evidence supports it and switches when the take restarts. Output: runs.json  {clip: [{t0,t1,offset,score}]}
Usage: segment_runs.py song.mp3 out.json clip.mov [...]   (confirmed offsets are given a bonus via PRIOR in the file name map below)"""
import json, subprocess, sys
import numpy as np
SR = 8000; WIN = 10; HOP = 2; TOL = 0.25; SWITCH = 0.25; NONE = 0.06; MINSEG = 6.0
PRIOR = {"c002": -3.081, "c005": 3.173, "c006": -1.342, "c007": -1.059, "c008": -14.391, "c009": 53.679, "c010": 60.987, "c011": 21.613,
         "c013": 115.197, "c014": -18.622, "c019": 33.875, "c021": 36.107, "c025": 140.996}
def load(p):
    b = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)
def main():
    S = load(sys.argv[1]); out = sys.argv[2]; cache = {}; cs = np.concatenate([[0], np.cumsum(S * S)])
    def Sf(n):
        if n not in cache: cache[n] = np.fft.rfft(S, n)
        return cache[n]
    def peaks(w, k=8):
        r = w - w.mean(); n = 1 << int(np.ceil(np.log2(len(S) + len(r))))
        c = np.fft.irfft(Sf(n) * np.conj(np.fft.rfft(r, n)), n)[:len(S) - len(r)]
        e = np.sqrt(cs[len(r):len(r) + len(c)] - cs[:len(c)]) + 1e-9; nc = c / (e * np.linalg.norm(r) + 1e-9); o = []
        for _ in range(k):
            i = int(np.argmax(nc)); o.append((i / SR, float(nc[i]))); nc[max(0, i - SR // 2):i + SR // 2] = -1
        return o
    res = {}
    for path in sys.argv[3:]:
        A = load(path); name = path.split("/")[-1].split(".")[0].replace("c014f", "c014"); dur = len(A) / SR; W = []
        t = 0.0
        while t + WIN <= dur + 0.01:
            w = A[int(t * SR):int((t + WIN) * SR)]
            W.append((t, peaks(w) if np.std(w) > 1e-5 else [])); t += HOP
        if not W: res[name] = []; continue
        votes = sorted((round(p - t0, 2), v) for t0, ps in W for p, v in ps)
        cl = []
        for o, v in votes:
            for c in cl:
                if abs(c["o"] - o) <= TOL: c["v"].append(v); c["os"].append(o); break
            else: cl.append(dict(o=o, v=[v], os=[o]))
        states = [dict(o=float(np.median(c["os"])), n=len(c["v"]), s=sum(c["v"])) for c in cl if len(c["v"]) >= 3]
        states.sort(key=lambda c: -c["s"]); states = states[:12]
        if name in PRIOR and not any(abs(s["o"] - PRIOR[name]) <= TOL for s in states): states.append(dict(o=PRIOR[name], n=0, s=0))
        K = len(states)
        def em(ps, o): return max([v for p, v in ps if abs((p - 0) - o - 0) <= TOL] + [0]) if False else None
        E = np.full((len(W), K + 1), NONE)
        for wi, (t0, ps) in enumerate(W):
            for si, s in enumerate(states):
                best = 0.0
                for p, v in ps:
                    if abs((p - t0) - s["o"]) <= TOL: best = max(best, v)
                E[wi, si] = best + (0.03 if name in PRIOR and abs(s["o"] - PRIOR[name]) <= TOL else 0)
        # viterbi
        D = np.zeros_like(E); B = np.zeros(E.shape, int); D[0] = E[0]
        for wi in range(1, len(W)):
            for si in range(K + 1):
                stay = D[wi - 1, si]; sw = D[wi - 1].max() - SWITCH; j = int(np.argmax(D[wi - 1]))
                if stay >= sw: D[wi, si] = stay + E[wi, si]; B[wi, si] = si
                else: D[wi, si] = sw + E[wi, si]; B[wi, si] = j
        path = [int(np.argmax(D[-1]))]
        for wi in range(len(W) - 1, 0, -1): path.append(int(B[wi, path[-1]]))
        path.reverse(); runs = []
        i = 0
        while i < len(path):
            j = i
            while j + 1 < len(path) and path[j + 1] == path[i]: j += 1
            if path[i] < K:
                t0 = max(0.0, W[i][0] + WIN / 2 - HOP / 2); t1 = min(dur, W[j][0] + WIN / 2 + HOP / 2)
                if i == 0: t0 = 0.0
                if j == len(path) - 1: t1 = dur
                if t1 - t0 >= MINSEG: runs.append(dict(t0=round(t0, 2), t1=round(t1, 2), offset=round(states[path[i]]["o"], 2), score=round(float(E[i:j + 1, path[i]].mean()), 3)))
            i = j + 1
        res[name] = runs; print(name, round(dur, 1), "s ->", runs)
    json.dump(res, open(out, "w"), indent=1)
main()
