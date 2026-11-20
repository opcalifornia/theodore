"""Cut check: run on every interview plan before it goes on a timeline.

For each piece (source file, in, out) it measures the real audio, not whisper:
  CUT INTO WORD  - the in or out point lands while someone is speaking
  THOUGHT GOES ON - speech continues after the out point (or before the in point)
                    with no real pause, so dialogue is being cut off
  STALL          - a silence of STALL_S or more inside the piece (mumbo jumbo to remove)
Exit code 1 if anything is flagged, so a build can refuse to continue.

usage: cut_check.py plan.json wav_dir   (plan items: {"f","s","e"[,"text"]}, wav = <wav_dir>/<f>.wav 16 kHz mono)
"""
import json, sys, wave
import numpy as np

HOP = 0.01          # 10 ms analysis frames
STALL_S = 0.35      # the editor removes stalls from about this long
GAP_S = 0.8         # a real pause between thoughts
LOOK_S = 4.0        # how far past the out point to look for a continuing thought

def envelope(path):
    w = wave.open(path); sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float)
    n = int(sr * HOP); x = x[:len(x) // n * n]
    db = 20 * np.log10(np.sqrt((x.reshape(-1, n) ** 2).mean(1)) + 1)
    floor = np.percentile(db, 10)
    voiced = db > floor + 12
    # smooth: ignore single-frame blips and fill 50 ms dips between syllables
    v = voiced.copy()
    for i in range(1, len(v) - 1):
        if voiced[i - 1] and voiced[i + 1]: v[i] = True
    return v

def runs(mask, val):
    out, i = [], 0
    while i < len(mask):
        if mask[i] == val:
            j = i
            while j < len(mask) and mask[j] == val: j += 1
            out.append((i, j)); i = j
        else: i += 1
    return out

def check(plan, wav_dir, words=None):
    env, issues = {}, []
    for n, o in enumerate(plan, 1):
        v = env.setdefault(o["f"], envelope(f"{wav_dir}/{o['f']}.wav"))
        a, b = int(o["s"] / HOP), int(o["e"] / HOP)
        tag = f"#{n:02d} {o['f']} {o['s']:.2f}-{o['e']:.2f}"
        if v[max(b - 3, 0):b].all(): issues.append((tag, "CUT INTO WORD at out point"))
        if v[a:a + 3].all() and a > 0 and v[a - 3:a].all(): issues.append((tag, "CUT INTO WORD at in point"))
        # does speech carry on after the out point before a real pause?
        after = v[b:b + int(LOOK_S / HOP)]
        first_gap = next((i for i, j in runs(after, False) if j - i >= GAP_S / HOP), len(after))
        if after[:first_gap].sum() * HOP > 0.3:
            t1 = o["e"] + first_gap * HOP
            said = ""
            if words: said = " ".join(w["word"].strip() for w in words.get(o["f"], []) if o["e"] - 0.1 < w["start"] < t1)
            issues.append((tag, f"THOUGHT GOES ON for {first_gap*HOP:.1f}s after the out point: \"{said}\""))
        for i, j in runs(v[a:b], False):
            if a + i > a + 5 and a + j < b - 5 and (j - i) * HOP >= STALL_S:
                issues.append((tag, f"STALL {(j-i)*HOP:.2f}s at {o['s']+i*HOP:.2f}"))
    return issues

if __name__ == "__main__":
    plan = json.load(open(sys.argv[1])); wd = sys.argv[2]
    words = {}
    for f in {o["f"] for o in plan}:
        try: words[f] = [w for s in json.load(open(f"{wd}/{f}.json"))["segments"] for w in s["words"]]
        except FileNotFoundError: pass
    iss = check(plan, wd, words)
    for t, m in iss: print(t, m)
    stall = sum(float(m.split()[1][:-1]) for _, m in iss if m.startswith("STALL"))
    print(f"\n{len(iss)} issues; {stall:.1f}s of stalls inside pieces; "
          f"{sum(1 for _,m in iss if m.startswith('THOUGHT'))} cut-off thoughts; {sum(1 for _,m in iss if m.startswith('CUT INTO'))} cuts into words")
    sys.exit(1 if iss else 0)
