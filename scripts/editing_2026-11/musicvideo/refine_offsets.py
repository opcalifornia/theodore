"""Second window per take + consensus: the true offset is the song position that BOTH windows agree on (repeated choruses fool a single window).
Usage: refine_offsets.py first_windows_dir out.json"""
import glob, json, os, subprocess, sys, tempfile
import numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from theodore.resolve.connection import connect
SR = 8000; FPS = 24000 / 1001; WIN = 480
def load(p):
    b = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)
def peaks(S, wav, k=8):
    r = wav - wav.mean(); n = 1 << int(np.ceil(np.log2(len(S) + len(r))))
    c = np.fft.irfft(np.fft.rfft(S, n) * np.conj(np.fft.rfft(r, n)), n)[:len(S) - len(r)]
    cs = np.concatenate([[0], np.cumsum(S * S)]); e = np.sqrt(cs[len(r):len(r) + len(c)] - cs[:len(c)]) + 1e-9
    nc = c / (e * np.linalg.norm(r)); out = []
    for _ in range(k):
        i = int(np.argmax(nc)); out.append((i / SR, float(nc[i]))); nc[max(0, i - SR):i + SR] = -1
    return out
def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)
def main():
    d1, out = sys.argv[1], sys.argv[2]
    h = connect(require_timeline=False); r = getattr(h, "resolve", h); mp = r.GetProjectManager().GetCurrentProject().GetMediaPool(); clips = list(walk(mp.GetRootFolder()))
    S = load([c for c in clips if c.GetName().lower().endswith(".mp3")][0].GetClipProperty("File Path"))
    first = {x["clip"]: x for x in json.load(open(os.path.expanduser("~/Documents/Theodore/Takes/mv_offsets.json"))) if "frames" in x}
    cams = {c.GetName(): c.GetClipProperty("File Path") for c in clips if c.GetName().startswith("A009_")}
    tmp = tempfile.mkdtemp(prefix="mv2_")
    def one(name):
        f = first[name]; frames = f["frames"]; s1 = f["window_start_frame"]; e1 = min(frames - 1, s1 + WIN)
        s2 = s1 + 900 if s1 + 900 + WIN < frames else (s1 - 700 if s1 - 700 >= 0 else None)
        w1 = os.path.join(d1, name[:9] + ".mov")
        if not os.path.exists(w1): return dict(clip=name, error="no first window")
        p1 = peaks(S, load(w1))
        if s2 is None: return dict(clip=name, frames=frames, note="short clip: single window", cands=[(round(p - s1 / FPS, 3), round(v, 3)) for p, v in p1[:4]], offset=round(p1[0][0] - s1 / FPS, 3), conf="SINGLE")
        o = os.path.join(tmp, name[:9]); subprocess.run(["REDline", "--i", cams[name], "--o", o, "--format", "201", "--resizeX", "320", "--start", str(s2), "--end", str(min(frames - 1, s2 + WIN)), "--silent"], capture_output=True)
        p2 = peaks(S, load(o + ".mov")); best = None
        for a, va in p1:
            for b, vb in p2:
                oa, ob = a - s1 / FPS, b - s2 / FPS
                if abs(oa - ob) < 0.2 and (best is None or va + vb > best[2]): best = (oa, ob, va + vb, va, vb)
        if best: return dict(clip=name, frames=frames, offset=round((best[0] + best[1]) / 2, 3), conf="AGREE", scores=(round(best[3], 3), round(best[4], 3)))
        return dict(clip=name, frames=frames, conf="DISAGREE", w1=[(round(p - s1 / FPS, 2), round(v, 2)) for p, v in p1[:3]], w2=[(round(p - s2 / FPS, 2), round(v, 2)) for p, v in p2[:3]])
    with ThreadPoolExecutor(3) as ex: res = list(ex.map(one, sorted(first)))
    json.dump(res, open(out, "w"), indent=1)
    for x in res: print(x)
main()
