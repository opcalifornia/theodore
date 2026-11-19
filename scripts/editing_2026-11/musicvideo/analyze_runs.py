"""Find where a take restarts: slide a window over the take's FULL camera audio, cross-correlate each window with the song, and group windows
that agree on one offset (song time at the take's first frame). Usage: analyze_runs.py song.mp3 clip.mov [clip.mov ...]"""
import json, subprocess, sys
import numpy as np
SR = 8000; WIN = 14; HOP = 7
def load(p):
    b = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)
def peaks(Sf, S2, wav, k=6):
    r = wav - wav.mean(); n = len(Sf) * 2 if False else None
    nfft = 1 << int(np.ceil(np.log2(len(S2) + len(r))))
    c = np.fft.irfft(Sf(nfft) * np.conj(np.fft.rfft(r, nfft)), nfft)[:len(S2) - len(r)]
    cs = np.concatenate([[0], np.cumsum(S2 * S2)]); e = np.sqrt(cs[len(r):len(r) + len(c)] - cs[:len(c)]) + 1e-9
    nc = c / (e * np.linalg.norm(r) + 1e-9); out = []
    for _ in range(k):
        i = int(np.argmax(nc)); out.append((i / SR, float(nc[i]))); nc[max(0, i - SR):i + SR] = -1
    return out
def main():
    S = load(sys.argv[1]); cache = {}
    def Sf(n):
        if n not in cache: cache[n] = np.fft.rfft(S, n)
        return cache[n]
    res = {}
    for path in sys.argv[2:]:
        A = load(path); name = path.split("/")[-1].split(".")[0]; dur = len(A) / SR; votes = []
        t = 0.0
        while t + WIN <= dur + 0.01:
            w = A[int(t * SR):int((t + WIN) * SR)]
            if np.std(w) > 1e-5:
                for pos, v in peaks(Sf, S, w):
                    votes.append((round(pos - t, 2), v, t))
            t += HOP
        # cluster offsets (tolerance 0.25 s), score = sum of peak values, support = distinct window starts
        votes.sort(); clusters = []
        for o, v, t0 in votes:
            for cl in clusters:
                if abs(cl["o"] - o) <= 0.25: cl["v"] += v; cl["t"].add(t0); cl["os"].append(o); break
            else: clusters.append(dict(o=o, v=v, t={t0}, os=[o]))
        for cl in clusters: cl["o"] = float(np.median(cl["os"]))
        clusters.sort(key=lambda c: -c["v"])
        res[name] = dict(duration_s=round(dur, 1), top=[dict(offset=round(c["o"], 2), score=round(c["v"], 2), windows=len(c["t"]), first=min(c["t"]), last=max(c["t"]) + WIN) for c in clusters[:5]])
        print(name, round(dur, 1), "s"); [print("   ", x) for x in res[name]["top"]]
    json.dump(res, open(sys.argv[2].rsplit("/", 1)[0] + "/runs.json", "w"), indent=1)
main()
