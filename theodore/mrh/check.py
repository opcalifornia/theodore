"""Gate 2: measure the built timeline and check it against the spine and the method's numbers."""
from __future__ import annotations

import os
import statistics as st


def metrics(tl, dialogue=("audio", 1), music=(("audio", 2), ("audio", 3)), broll=("video", 2)) -> dict:
    fps = float(tl.GetSetting("timelineFrameRate")); s0 = int(tl.GetStartFrame()); end = (int(tl.GetEndFrame()) - s0) / fps
    def items(spec):
        kind, i = spec
        return [x for x in (tl.GetItemListInTrack(kind, i) or []) if x.GetType() != "transition"]
    dia = sorted(items(dialogue), key=lambda x: x.GetStart())
    d = [((x.GetStart() - s0) / fps, x.GetDuration() / fps) for x in dia]
    allgaps = [round(d[i + 1][0] - (d[i][0] + d[i][1]), 2) for i in range(len(d) - 1)]
    gaps = [g for g in allgaps if g < 5]          # a 5 s+ gap is a music swell on purpose, not a pause
    swells = len(allgaps) - len(gaps)
    speech = sum(x[1] for x in d)
    br = [x.GetDuration() / fps for x in items(broll)]
    def cov(a, b): return sum(max(0, min(b, y.GetStart() / fps - s0 / fps + y.GetDuration() / fps) - max(a, y.GetStart() / fps - s0 / fps)) for y in items(broll))
    covered = sum(cov(a, a + dur) for a, dur in d)
    mus = sorted([x for spec in (music if isinstance(music[0], tuple) else (music,)) for x in items(spec)], key=lambda x: x.GetStart())
    vols = [(round((x.GetStart() - s0) / fps, 1), round(x.GetDuration() / fps, 1), round(float(x.GetProperty("AudioVolume") or 0), 1)) for x in mus]
    fades = mus[-1].GetFades() if mus else None
    return dict(length_s=round(end, 1), pieces=len(d), speech_s=round(speech, 1), speech_pct=round(100 * speech / end),
                butt_joins=sum(1 for g in gaps if abs(g) < 0.05), joins=len(gaps), swell_gaps=swells, longest_silence_s=max(allgaps) if allgaps else 0,
                broll_clips=len(br), broll_median_s=round(st.median(br), 1) if br else None,
                broll_cover_pct=round(100 * covered / speech) if speech else 0, music=vols, last_music_fades=fades)


def verify(sel_stats: dict, m: dict, built_text: list[str], spine_texts: list[str]) -> list[tuple[str, bool, str]]:
    """Returns (check, passed, detail). built_text = transcript of what was actually placed, in order."""
    res = []
    res.append(("kept about 15% of the source", sel_stats.get("kept_pct") is not None and sel_stats["kept_pct"] <= 22, f"{sel_stats.get('kept_pct')}%"))
    res.append(("joins have no pauses", m["butt_joins"] >= 0.8 * max(1, m["joins"]), f"{m['butt_joins']} of {m['joins']} joins butt-joined ({m.get('swell_gaps',0)} swell gaps excluded)"))
    res.append(("B-roll cuts about 1.5 s", m["broll_median_s"] is not None and m["broll_median_s"] <= 2.2, f"median {m['broll_median_s']} s"))
    res.append(("B-roll covers the talking", m["broll_cover_pct"] >= 35, f"{m['broll_cover_pct']}% of speech"))
    low = [v for v in m["music"] if v[2] <= -12]
    res.append(("music ducked to about -16 dB under speech", len(low) >= 1, f"{len(low)} ducked sections"))
    res.append(("music swells up between blocks", any(v[2] >= -6 for v in m["music"]), f"levels {[v[2] for v in m['music']][:8]}"))
    res.append(("ends on a long fade", bool(m["last_music_fades"]) and (m["last_music_fades"].get("FadeOut") or 0) >= 12, str(m["last_music_fades"])))
    norm = lambda t: " ".join("".join(c.lower() if c.isalnum() else " " for c in t).split())
    ok = all(norm(a)[:40] in norm(b) or norm(b)[:40] in norm(a) for a, b in zip(spine_texts, built_text)) and len(built_text) == len(spine_texts)
    res.append(("transcript of the result reads in spine order", ok, f"{len(built_text)} pieces vs {len(spine_texts)} picked"))
    return res


def built_texts(tl, track=("audio", 1), stt_py=os.path.expanduser("~/.theodore-stt/bin/python")) -> list[str]:
    """Transcribe each dialogue piece actually placed on the timeline (what the audience will hear), in timeline order."""
    import json, subprocess, tempfile
    from pathlib import Path
    worker = str(Path(__file__).with_name("stt_worker.py")); out = []
    its = sorted([x for x in (tl.GetItemListInTrack(*track) or []) if x.GetType() != "transition"], key=lambda x: x.GetStart())
    for x in its:
        mpi = x.GetMediaPoolItem(); path = mpi.GetClipProperty("File Path"); ss = float(x.GetSourceStartTime()); d = float(x.GetSourceEndTime()) - ss
        wav = tempfile.mktemp(suffix=".wav"); js = tempfile.mktemp(suffix=".json")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0, ss - .1):.3f}", "-t", f"{d + .2:.3f}", "-i", path, wav], check=True)
        subprocess.run([stt_py, worker, wav, js], check=True, stderr=subprocess.DEVNULL)
        out.append(" ".join(g["t"] for g in json.load(open(js)))); os.remove(wav); os.remove(js)
    return out
