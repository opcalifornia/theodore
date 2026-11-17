"""Measure where each A009 take sits in the song: REDline exports a short window with the camera scratch audio, FFT cross-correlation
against the song gives the song position of that window, so song time at the clip's first frame = position - window start.
Usage: measure_offsets.py out.json   (reads the open Resolve project: clips named A009_*, the .mp3 song)"""
import json, os, re, subprocess, sys, tempfile
import numpy as np
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
from theodore.resolve.connection import connect
SR = 8000; FPS = 24000 / 1001; WIN = 480
def load(p):
    b = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(b, np.float32).astype(np.float64)
def walk(f):
    for c in f.GetClipList() or []: yield c
    for s in f.GetSubFolderList() or []: yield from walk(s)
def main():
    out = sys.argv[1]; h = connect(require_timeline=False); r = getattr(h, "resolve", h)
    mp = r.GetProjectManager().GetCurrentProject().GetMediaPool(); clips = list(walk(mp.GetRootFolder()))
    song = [c for c in clips if c.GetName().lower().endswith(".mp3")][0].GetClipProperty("File Path")
    cams = sorted([(c.GetName(), c.GetClipProperty("File Path"), int(c.GetClipProperty("Frames") or 0), c.GetClipProperty("Type")) for c in clips if c.GetName().startswith("A009_") and c.GetClipProperty("Type") == "Video + Audio"])
    S = load(song); tmp = tempfile.mkdtemp(prefix="mv_")
    def one(c):
        name, path, frames, _ = c
        start = 1200 if frames > 1800 else max(0, min(frames - WIN - 1, frames // 4)); end = min(frames - 1, start + WIN)
        o = os.path.join(tmp, name[:9])
        subprocess.run(["REDline", "--i", path, "--o", o, "--format", "201", "--resizeX", "320", "--start", str(start), "--end", str(end), "--silent"], capture_output=True)
        r_ = load(o + ".mov")
        if len(r_) < SR * 5: return dict(clip=name, error="no audio window")
        r_ = r_ - r_.mean(); n = 1 << int(np.ceil(np.log2(len(S) + len(r_))))
        c_ = np.fft.irfft(np.fft.rfft(S, n) * np.conj(np.fft.rfft(r_, n)), n)[:len(S) - len(r_)]
        cs = np.concatenate([[0], np.cumsum(S * S)]); e = np.sqrt(cs[len(r_):len(r_) + len(c_)] - cs[:len(c_)]) + 1e-9
        nc = c_ / (e * np.linalg.norm(r_)); i = int(np.argmax(nc)); far = np.abs(np.arange(len(nc)) - i) > SR
        pos = i / SR; return dict(clip=name, frames=frames, window_start_frame=start, song_pos_s=round(pos, 4), song_time_at_clip_start_s=round(pos - start / FPS, 4),
                                  peak=round(float(nc[i]), 3), second=round(float(nc[far].max()), 3))
    with ThreadPoolExecutor(3) as ex: res = list(ex.map(one, cams))
    json.dump(res, open(out, "w"), indent=1)
    for x in res: print(x)
main()
