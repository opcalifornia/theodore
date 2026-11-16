"""Self-test of the Mr.H toolkit on real lav audio in a THROWAWAY scratch project (never the editor's projects).
Usage: ~/theodore/.venv/bin/python selftest.py <lav.wav> [<second lav.wav>]"""
import json, os, re, subprocess, sys, time
from theodore.mrh import corpus, selection, build, check, tagger
from theodore.resolve import connection

files = sys.argv[1:]; T = os.path.expanduser("~/Documents/Theodore/MrH Cache/selftest"); os.makedirs(T, exist_ok=True)
sh = lambda *a: subprocess.run(a, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
music = f"{T}/music_bed.wav"
if not os.path.exists(music): sh("ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=330", "-f", "lavfi", "-i", "sine=f=330:d=330", "-filter_complex", "amix=inputs=2", "-ar", "48000", music)
brolls = []
for i, col in enumerate(["0x884422", "0x225588", "0x228844", "0x882255", "0x558822", "0x442288"]):
    p = f"{T}/broll_{i}.mp4"; brolls.append(p)
    if not os.path.exists(p): sh("ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={col}:s=1280x720:r=23.976:d=8", "-pix_fmt", "yuv420p", p)
t0 = time.time(); C = corpus.transcribe(files); print(f"corpus: {sum(len(r['sentences']) for r in C)} sentences in {round(time.time()-t0)}s")
cands = corpus.candidates(C); print("candidates (top 40%):", len(cands))
find = lambda rx, f=None: next(c for c in cands if re.search(rx, c["t"], re.I) and (f is None or f in c["file"]))
beats = [("purpose", "pass the baton|give back to community"), ("gratitude", "taking away so much more"), ("belief", "power of youth"),
         ("proof", "came here as a very quiet|shy"), ("giving", "so much more than I'm ever giving"), ("action", "can't stop with this"),
         ("closing", "resonate")]
lines = []
for beat, rx in beats:
    try: c = find(rx)
    except StopIteration: print("  (fixture: no match for", beat, ")"); continue
    lines.append(dict(beat=beat, file=c["file"], start=c["s"], end=c["e"], text=c["t"], swell_before=beat in ("belief", "giving")))
sel = dict(project="selftest", source_minutes=sum(r["minutes"] for r in C), lines=lines)
json.dump(sel, open(f"{T}/selection.json", "w"), indent=1); print(selection.report(sel))
h = connection.connect(require_project=False, require_timeline=False); pm = h.project_manager
home = pm.GetCurrentProject().GetName(); pm.SaveProject()
name = "Theodore MrH Scratch " + time.strftime("%m%d-%H%M"); proj = (pm.LoadProject(next((n for n in pm.GetProjectListInCurrentFolder() if n.startswith("Theodore MrH Scratch")), name)) and pm.GetCurrentProject()) or pm.CreateProject(name); proj = pm.GetCurrentProject(); assert proj, "scratch project failed"
for k, v in (("timelineFrameRate", "23.976"), ("timelinePlaybackFrameRate", "23.976")): proj.SetSetting(k, v)
h2 = connection.connect(require_timeline=False)
pool = proj.GetMediaPool(); root = pool.GetRootFolder(); fol = pool.AddSubFolder(root, "Test B-roll"); pool.SetCurrentFolder(fol); pool.ImportMedia(brolls)
out = build.build(h2, sel, "Mr.H test recap", music=music, broll_bins=["Test B-roll"], dry_run=False)
print({k: v for k, v in out.items() if k not in ("dialogue", "music")}); print("dialogue read-back (index, placed src s, wanted src s):", out["dialogue"]); print("music (section, kind, set dB, read-back dB):", out["music"])
tl = proj.GetCurrentTimeline(); m = check.metrics(tl, dialogue=("audio", 1), broll=("video", 2)); print("metrics:", m)
res = check.verify(selection.stats(sel), m, check.built_texts(tl), [l["text"] for l in sel["lines"]])
print("\nGATE 2"); [print(f"  {'PASS' if ok else 'FAIL'}  {c}: {d}") for c, ok, d in res]
pm.SaveProject(); pm.LoadProject(home); print("\nrestored project:", pm.GetCurrentProject().GetName(), "| scratch project left in place:", name)
