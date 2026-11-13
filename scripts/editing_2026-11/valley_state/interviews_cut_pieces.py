"""Turn answer blocks into pieces: drop ums and stalls, cut inside the silence.
Rules from the editor's re-cuts (memory: interview-cut-craft):
  - a stall is any silence of STALL s or more inside an answer; cut it out, leaving
    LEAD s before the next sound and TAIL s after the last sound
  - an um/uh is cut from the dip after the previous real word to 0.03 s before the next
  - a block starts at the first sound and ends at the dip after the last word + 0.25 s
Audio is measured from the 16 kHz wav, not whisper times (whisper stretches words over pauses)."""
import json, re, sys, wave
import numpy as np
HOP, STALL, LEAD, TAIL, MINP = 0.01, 0.50, 0.06, 0.14, 0.30
FILL = re.compile(r"^(u+h*m+|u+h+|e+r+m*|a+h+|h+m+|m+h*m+|uhm+)$")
def norm(s): return re.sub(r"[^a-z']", "", s.lower())

def voiced(path):
    w = wave.open(path); sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float)
    n = int(sr * HOP); x = x[:len(x) // n * n]
    db = 20 * np.log10(np.sqrt((x.reshape(-1, n) ** 2).mean(1)) + 1)
    v = db > np.percentile(db, 10) + 12
    v2 = v.copy(); v2[1:-1] |= v[:-2] & v[2:]          # fill 10 ms dips inside syllables
    return v2

def pieces(wav, words, blocks):
    v = voiced(wav); T = len(v) * HOP
    def vz(t): return bool(v[min(max(int(t / HOP), 0), len(v) - 1)])
    out = []
    for bs, be in blocks:
        ws = [w for w in words if w["start"] >= bs - 0.3 and w["start"] < be]
        real = [w for w in ws if not FILL.match(norm(w["word"]))]
        if not real: continue
        # start: first sound at or before the first real word
        s = real[0]["start"]; t = s
        while t > s - 0.4 and vz(t - HOP): t -= HOP
        start = max(t - 0.03, 0)
        # end: first 150 ms of quiet after the last word starts, plus TAIL
        t = real[-1]["start"] + 0.1
        while t < T - 0.2 and any(vz(t + k * HOP) for k in range(15)): t += HOP
        end = min(t + 0.25, (words[words.index(real[-1]) + 1]["start"] - 0.05) if words.index(real[-1]) + 1 < len(words) else T)
        # holes: stalls measured from audio, ums from the transcript
        holes = []
        a = int(start / HOP); b = int(end / HOP); i = a
        while i < b:
            if not v[i]:
                j = i
                while j < b and not v[j]: j += 1
                if (j - i) * HOP >= STALL and i > a and j < b:
                    holes.append((i * HOP + TAIL, j * HOP - LEAD))
                i = j
            else: i += 1
        for k, w in enumerate(ws):
            if not FILL.match(norm(w["word"])): continue
            prev = next((x for x in reversed(ws[:k]) if not FILL.match(norm(x["word"]))), None)
            nxt = next((x for x in ws[k + 1:] if not FILL.match(norm(x["word"]))), None)
            if prev is None or nxt is None: continue
            t = max(prev["start"] + 0.08, w["start"] - 0.6)
            while t < w["start"] and vz(t): t += HOP
            holes.append((t + 0.03, nxt["start"] - 0.03))
        holes = sorted((h0, h1) for h0, h1 in holes if h1 - h0 > 0.08)
        merged = []
        for h in holes:
            if merged and h[0] <= merged[-1][1] + 0.05: merged[-1] = (merged[-1][0], max(merged[-1][1], h[1]))
            else: merged.append(h)
        # a piece too short to stand alone is never dropped (it may be a word):
        # the hole before it is closed instead
        keep, cur = [], start
        for h0, h1 in merged + [(end, end)]:
            seg = [cur, h0]
            if keep and seg[1] - seg[0] < MINP: keep[-1][1] = seg[1]
            elif seg[1] - seg[0] > 0: keep.append(seg)
            cur = max(cur, h1)
        out += [(round(a, 3), round(b, 3), (bs, be)) for a, b in keep]
    return out

if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from interviews_cut_blocks import BLOCKS, WAV
    V = sys.argv[1]; res = {}
    for clip, blocks in BLOCKS.items():
        words = json.load(open(f"{V}/{WAV[clip]}_um.json"))
        ps = pieces(f"{V}/{WAV[clip]}.wav", words, blocks); res[clip] = ps
        raw = sum(e - s for s, e in blocks); kept = sum(e - s for s, e, _ in ps)
        ums = sum(1 for w in words if FILL.match(norm(w["word"])) and any(s <= w["start"] < e for s, e in blocks))
        print(f"{clip:16} {len(blocks):2} answers {raw/60:5.1f} min -> {len(ps):3} pieces {kept/60:5.1f} min  ({ums} ums found)")
    json.dump(res, open(f"{V}/interview_pieces.json", "w"))
