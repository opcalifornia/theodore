"""`theodore study`: turn a finished video into an edit blueprint.

Output, under ``<data>/references/<name>/``:

  analysis.json   every measurement: shots, transitions, pacing, tempo,
                  beat/speech sync, loudness, Claude's log of every shot
  blueprint.json  the style, as rules and a recipe the agent can replicate
  report.md       the same, readable
  frames/         the frames Claude looked at
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Callable, Optional


from theodore import config
from theodore.llm import make_client
from theodore.analyze.claude_client import CostTracker, call_json
from theodore.study import layers, rhythm, shots as shot_mod, signals, vision

REFERENCES_DIR = "references"

SYNTH_SYSTEM = """You are the head of editorial at a top commercial and music-video post \
house. You are given a complete technical breakdown of a finished piece: every shot and \
how it ends, pacing, music tempo and how cuts sit on the beat, what is said and how cuts \
sit against speech, loudness, and a shot-by-shot visual log. Reverse-engineer HOW it was \
edited -- the system behind it -- precisely enough that another editor (or an AI driving \
DaVinci Resolve) could cut new footage the same way. Ground every claim in the numbers \
and the log; say "unclear" rather than invent."""

SYNTH_INSTRUCTIONS = """Respond with ONLY a JSON object:
{
 "title": "<short name for this style>",
 "genre": "commercial|music_video|interview|documentary|social_short|promo|sports_highlight|trailer|narrative|event_recap|other",
 "driven_by": "story|music|hybrid",
 "driven_by_why": "<one or two sentences citing the numbers>",
 "summary": "<4-6 sentences an editor would say about how this is cut>",
 "structure": [{"section": "<hook/intro/verse/chorus/body/CTA/outro...>", "start": <s>, "end": <s>,
                "purpose": "...", "pacing": "<ASL and feel>", "audio": "<what the audio does>"}],
 "pacing_rules": ["<rule>", ...],
 "transition_vocabulary": [{"kind": "...", "count": <n>, "duration_frames": <typical>,
                            "when_used": "...", "resolve_how": "<how to build it in Resolve>"}],
 "music_sync_rules": ["..."],
 "story_rules": ["<how speech, A-roll and B-roll are cut together>"],
 "a_roll_b_roll": {"a_roll_pct": <0-100>, "b_roll_pct": <0-100>, "pattern": "..."},
 "story_arc": [{"beat": "<hook/setup/stakes/turn/payoff/button...>", "start": <s>, "end": <s>,
                "what_is_said": "<the point being made, quoting key words>", "picture": "<talking head, or what B-roll shows>"}],
 "dialogue_broll_system": {"when_cutaways_start": "<how far into a line, vs the first face time>",
                           "how_long": "<seconds, single shot or montage>",
                           "where_they_cut": "<on phrase breaks? mid-line? return to face before the line ends?>",
                           "picture_to_words": "<literal | illustrative | emotional | rhythmic -- with 2-3 quoted examples of words -> picture from `cutaways`>",
                           "what_stays_on_face": "<the moments the editor never covers>",
                           "broll_shot_choice": "<sizes, movement, subjects, how B-roll shots are sequenced against each other>"},
 "score_system": "<how the music bed is used: entry, ducking under speech from score_vs_speech, rises in pauses, peaks and what picture sits on them, exit>",
 "overlay_system": [{"type": "...", "position": "...", "style": "...", "timing": "<when/how long>",
                     "resolve_how": "<Text+/Fusion title/generator/composite...>"}],
 "text_and_titles": ["..."],
 "look": {"description": "...", "contrast": "...", "saturation": "...", "resolve_how": "..."},
 "audio": {"loudness_target_lufs": <number or null>, "music": "...", "sfx": "...", "dialogue": "..."},
 "signature_moves": ["<the 3-6 things that make this edit feel like itself>"],
 "recipe": ["<ordered, imperative steps to recreate this style on NEW footage>", ...],
 "footage_needed": ["<what shots/assets a new project needs to pull this off>"]
}"""


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return slug[:60] or "reference"


def references_root() -> Path:
    return Path(config.DATA_ROOT) / REFERENCES_DIR


def list_references() -> list[dict]:
    root = references_root()
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        bp = d / "blueprint.json"
        if bp.exists():
            try:
                data = json.loads(bp.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"name": d.name, "title": data.get("title"), "genre": data.get("genre"),
                        "driven_by": data.get("driven_by"), "source": data.get("source")})
    return out


def load_reference(name: str) -> dict:
    d = references_root() / slugify(name)
    if not (d / "blueprint.json").exists():
        raise FileNotFoundError(f"No studied reference called {name!r}. "
                                f"Known: {', '.join(r['name'] for r in list_references()) or 'none'}")
    return {"blueprint": json.loads((d / "blueprint.json").read_text(encoding="utf-8")),
            "analysis": json.loads((d / "analysis.json").read_text(encoding="utf-8")), "dir": str(d)}


def _transcript_words(video: Path, ref_dir: Path) -> tuple[list[dict], str]:
    """Word-level transcript from Resolve's own speech-to-text (free, local).
    Raises with the reason when Resolve can't do it, so the study records
    "speech unknown" rather than "nobody speaks"."""
    from theodore.resolve import transcribe
    result, why = transcribe.try_transcribe_file(video, word_level=True)
    if result is None:
        raise RuntimeError(why)
    (ref_dir / "transcript.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result["words"], result["text"]


def _transitions_summary(transitions: list[dict]) -> dict:
    by_kind: dict[str, list[int]] = {}
    for t in transitions:
        if t["kind"] == "flash_in_shot":
            continue
        by_kind.setdefault(t["kind"], []).append(t["frames"])
    return {k: {"count": len(v), "typical_frames": int(sorted(v)[len(v) // 2])} for k, v in by_kind.items()}


def _shot_table(shots: list[dict], log: dict[int, dict]) -> list[dict]:
    rows = []
    for s in shots:
        v = log.get(s["index"], {})
        rows.append({
            "i": s["index"], "t": s["start_time"], "dur": s["duration"],
            "in": s["transition_in"], "out": s["transition_out"], "motion": s["motion"],
            "size": v.get("shot_size"), "role": v.get("role"), "cam": v.get("camera"),
            "subject": v.get("subject"), "text": v.get("on_screen_text") or [],
            "overlays": [f"{o.get('type')}@{o.get('position')}: {o.get('description')}"
                         for o in (v.get("overlays") or [])],
            "look": v.get("look"), "notes": v.get("notes"),
        })
    return rows


def study(video: Path, name: Optional[str] = None, *, use_vision: bool = True,
          use_transcript: bool = True, progress: Optional[Callable[[str], None]] = None,
          client=None) -> dict:
    """Study ``video`` and return {"name", "dir", "blueprint", "analysis"}."""
    video = Path(video).expanduser().resolve()
    if not video.exists():
        raise FileNotFoundError(f"No such video: {video}")
    say = progress or (lambda msg: None)
    name = slugify(name or video.stem)
    ref_dir = references_root() / name
    ref_dir.mkdir(parents=True, exist_ok=True)
    cost = CostTracker()

    say("Reading the video")
    info = signals.probe(video)
    crop = signals.active_area(video, info.duration, info.width, info.height)
    vsig = signals.video_signals(video, crop=crop)
    fps = info.fps
    duration = info.duration or vsig.n / fps

    say("Finding every cut and transition")
    transitions = shot_mod.detect(vsig, fps)
    shot_objs = shot_mod.shots_from_transitions(vsig, transitions)
    shots = [s.to_dict(fps) for s in shot_objs]
    trans = [t.to_dict(fps) for t in transitions]
    cut_times = [s["start_time"] for s in shots[1:]]

    analysis: dict = {
        "source": str(video), "name": name,
        "media": {"duration": round(duration, 3), "fps": round(fps, 3), "fps_str": info.fps_str,
                  "width": info.width, "height": info.height, "frames": vsig.n,
                  "aspect": f"{info.width}:{info.height}",
                  "active_picture": crop and {**crop, "aspect": round(crop["w"] / crop["h"], 3),
                                              "note": "picture is letterboxed/pillarboxed inside the frame"}},
        "pacing": rhythm.pacing([s["duration"] for s in shots], duration),
        "transitions": trans,
        "transition_summary": _transitions_summary(trans),
        "shots": shots,
    }

    words: list[dict] = []
    rms = None
    transcript_text = ""
    if info.has_audio:
        say("Listening: tempo, beats, energy, loudness")
        y = signals.audio_samples(video)
        env = signals.onset_envelope(y)
        rms = signals.rms_envelope(y)
        bpm, periodicity = rhythm.estimate_tempo(env)
        beats = rhythm.track_beats(env, bpm) if periodicity >= 0.1 else []
        phase = rhythm.downbeat_phase(env, beats) if len(beats) else 0
        analysis["music"] = {
            "tempo_bpm": bpm, "beat_periodicity": periodicity,
            "rhythmic_music_likely": bool(periodicity >= 0.25),
            "beats": [round(float(b), 3) for b in beats][:2000],
            "downbeat_phase": phase,
            "sync": rhythm.music_sync(cut_times, beats, bpm, phase, fps) if len(beats) else {},
        }
        bin_s = max(1.0, duration / 120)
        analysis["energy"] = {"seconds_per_bin": round(bin_s, 2),
                              "curve": rhythm.energy_curve(rms, bin_s),
                              "cut_rate_vs_loudness_r": rhythm.cut_energy_correlation(cut_times, rms, duration)}
        analysis["loudness"] = signals.integrated_loudness(video)
        if use_transcript:
            say("Transcribing what's said (Resolve speech-to-text)")
            try:
                words, transcript_text = _transcript_words(video, ref_dir)
            except Exception as exc:  # noqa: BLE001 -- the rest of the study still stands
                analysis["transcript_error"] = str(exc)
                say(f"Transcription failed ({str(exc)[:120]}) -- continuing without it")
        if use_transcript and "transcript_error" not in analysis:
            analysis["speech"] = rhythm.speech_relation(cut_times, words, duration)
        else:
            # Unknown is not the same as silent: never let a failed or skipped
            # transcription read as "nobody speaks in this piece".
            analysis["speech"] = {"unknown": True, "reason": analysis.get("transcript_error")
                                  or "transcription skipped"}
        analysis["transcript"] = transcript_text

    shot_log: dict[int, dict] = {}
    client = client or make_client()
    if use_vision:
        say(f"Looking at all {len(shots)} shots")
        shot_log = vision.describe_shots(client, video, shots, ref_dir / "frames", cost, progress=say)
    analysis["shot_log"] = {str(k): v for k, v in sorted(shot_log.items())}

    if shot_log and words:
        say("Laying picture over speech: talking heads, cutaways, music bed")
        spans = rhythm.speech_spans(words)
        rows = layers.label_shots(shots, shot_log, spans)
        cuts = layers.cutaways(rows, words, spans)
        analysis["layers"] = {
            "summary": layers.summarize(rows, cuts, spans, duration),
            "cutaways": cuts,
            "shots": rows,
            "score": layers.score_vs_speech(rms, spans, duration, rhythm.ENV_RATE, rows) if rms is not None else {},
        }

    say("Working out how it was edited")
    payload = {
        "media": analysis["media"], "pacing": analysis["pacing"],
        "transition_summary": analysis["transition_summary"],
        "music": {k: v for k, v in analysis.get("music", {}).items() if k != "beats"},
        "energy": analysis.get("energy"), "loudness": analysis.get("loudness"),
        "speech": analysis.get("speech"),
        "transcript": transcript_text[:12000],
        "shots": _shot_table(shots, shot_log),
    }
    roles = Counter((v.get("role") or "unknown") for v in shot_log.values())
    payload["role_counts"] = dict(roles)
    if "layers" in analysis:
        lay = analysis["layers"]
        payload["picture_over_speech"] = {"summary": lay["summary"], "score_vs_speech": lay["score"],
                                          "cutaways": lay["cutaways"][:60]}
        labels = {r["index"]: r["label"] for r in lay["shots"]}
        for row in payload["shots"]:
            row["label"] = labels.get(row["i"])
    blueprint = call_json(
        client, pass_name="study-blueprint", model=vision.STUDY_MODEL, system=SYNTH_SYSTEM,
        user="Breakdown:\n" + json.dumps(payload, separators=(",", ":")) + "\n\n" + SYNTH_INSTRUCTIONS,
        max_tokens=8000, cost_tracker=cost,
    )
    blueprint["source"] = str(video)
    blueprint["name"] = name
    blueprint["measured"] = {
        "duration": analysis["media"]["duration"], "fps": analysis["media"]["fps"],
        "resolution": f"{info.width}x{info.height}",
        "shots": analysis["pacing"].get("shots"),
        "average_shot_length": analysis["pacing"].get("average_shot_length"),
        "cuts_per_minute": analysis["pacing"].get("cuts_per_minute"),
        "transitions": analysis["transition_summary"],
        "tempo_bpm": analysis.get("music", {}).get("tempo_bpm"),
        "cuts_on_beat_pct": analysis.get("music", {}).get("sync", {}).get("cuts_on_beat_pct"),
        "speech_ratio": analysis.get("speech", {}).get("speech_ratio"),
        "integrated_lufs": analysis.get("loudness", {}).get("integrated_lufs"),
        "picture_over_speech": analysis.get("layers", {}).get("summary"),
        "score_vs_speech": analysis.get("layers", {}).get("score"),
    }
    analysis["claude_calls"] = cost.calls

    (ref_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    (ref_dir / "blueprint.json").write_text(json.dumps(blueprint, indent=2), encoding="utf-8")
    (ref_dir / "report.md").write_text(render_report(blueprint, analysis), encoding="utf-8")
    shutil.rmtree(ref_dir / "audio", ignore_errors=True)
    say("Done")
    return {"name": name, "dir": str(ref_dir), "blueprint": blueprint, "analysis": analysis}


def _bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items or []) or "- (none)"


def render_report(bp: dict, an: dict) -> str:
    m = bp.get("measured", {})
    lines = [
        f"# {bp.get('title') or bp.get('name')}",
        f"Source: `{bp.get('source')}`",
        "",
        f"**{bp.get('genre')} · {bp.get('driven_by')}-driven** — {bp.get('driven_by_why', '')}",
        "",
        bp.get("summary", ""),
        "",
        "## Measured",
        f"- {m.get('duration')}s at {m.get('fps')} fps, {m.get('resolution')}",
        f"- {m.get('shots')} shots, average {m.get('average_shot_length')}s, {m.get('cuts_per_minute')} cuts/min",
        f"- Transitions: " + ", ".join(f"{k} ×{v['count']}" for k, v in (m.get("transitions") or {}).items()),
        f"- Music: {m.get('tempo_bpm')} BPM, {m.get('cuts_on_beat_pct')}% of cuts on a beat "
        f"(chance {an.get('music', {}).get('sync', {}).get('chance_on_beat_pct')}%)",
        (f"- Speech covers {round(100 * m['speech_ratio'])}% of the runtime"
         if m.get("speech_ratio") is not None else
         f"- Speech: not measured ({(an.get('speech') or {}).get('reason') or 'no audio'})"),
        f"- Loudness {m.get('integrated_lufs')} LUFS integrated",
        "",
        "## Structure",
    ]
    for s in bp.get("structure") or []:
        lines.append(f"- **{s.get('section')}** {s.get('start')}–{s.get('end')}s: {s.get('purpose')} "
                     f"({s.get('pacing')}; audio: {s.get('audio')})")
    lines += ["", "## Signature moves", _bullets(bp.get("signature_moves")),
              "", "## Pacing", _bullets(bp.get("pacing_rules")),
              "", "## Transitions"]
    for t in bp.get("transition_vocabulary") or []:
        lines.append(f"- **{t.get('kind')}** ×{t.get('count')} (~{t.get('duration_frames')}f): "
                     f"{t.get('when_used')} — *Resolve:* {t.get('resolve_how')}")
    lines += ["", "## Music sync", _bullets(bp.get("music_sync_rules")),
              "", "## Story / A-roll & B-roll", _bullets(bp.get("story_rules")),
              f"- Pattern: {(bp.get('a_roll_b_roll') or {}).get('pattern')}"]
    lay = an.get("layers") or {}
    if lay:
        sm = lay["summary"]
        lines += ["", "## Picture over speech (measured)",
                  f"- Time on screen: {sm['time_pct']}",
                  f"- {sm['dialogue_covered_by_broll_pct']}% of dialogue is under B-roll; {sm['cutaways']} cutaways, "
                  f"{sm.get('cutaway_seconds')}; first at {sm.get('first_cutaway_at')}s; "
                  f"{sm['cutaways_on_phrase_pct']}% start on a phrase break; "
                  f"{sm['cutaways_return_before_line_end_pct']}% return to the face before the line ends",
                  f"- Sequence (T talking head, B B-roll over speech, b B-roll clean, C card): {sm['sequence']}"]
        if lay.get("score"):
            lines.append(f"- Music bed vs speech: {lay['score']}")
    system = bp.get("dialogue_broll_system") or {}
    if system:
        lines += ["", "## How B-roll is laid over dialogue"] + [f"- **{k.replace('_', ' ')}:** {v}" for k, v in system.items()]
    if bp.get("story_arc"):
        lines += ["", "## Story arc"] + [f"- {b.get('start')}–{b.get('end')}s **{b.get('beat')}**: "
                                        f"{b.get('what_is_said')} → {b.get('picture')}" for b in bp["story_arc"]]
    if bp.get("score_system"):
        lines += ["", "## Score", str(bp["score_system"])]
    lines += ["", "## Overlays & graphics"]
    for o in bp.get("overlay_system") or []:
        lines.append(f"- **{o.get('type')}** at {o.get('position')}: {o.get('style')}; {o.get('timing')} "
                     f"— *Resolve:* {o.get('resolve_how')}")
    look = bp.get("look") or {}
    audio = bp.get("audio") or {}
    lines += ["", "## Text & titles", _bullets(bp.get("text_and_titles")),
              "", "## Look", f"{look.get('description')} — *Resolve:* {look.get('resolve_how')}",
              "", "## Audio", f"- Target: {audio.get('loudness_target_lufs')} LUFS",
              f"- Music: {audio.get('music')}", f"- SFX: {audio.get('sfx')}", f"- Dialogue: {audio.get('dialogue')}",
              "", "## Recipe"]
    lines += [f"{i}. {step}" for i, step in enumerate(bp.get("recipe") or [], 1)]
    lines += ["", "## Footage needed", _bullets(bp.get("footage_needed")), ""]
    return "\n".join(lines)
