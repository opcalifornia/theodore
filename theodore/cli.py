"""Theodore CLI entry point."""
from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic
import click

from theodore import commands, config, edits, registry
from theodore.analyze import coverage as analyze_coverage
from theodore.analyze import delivery as analyze_delivery
from theodore.analyze import learning as analyze_learning
from theodore.analyze import redundancy as analyze_redundancy
from theodore.analyze import search as analyze_search
from theodore.analyze.claude_client import CostTracker
from theodore.analyze.question_guide import apply_canonical_ids, match_to_guide
from theodore.analyze.segmenter import run_segmenter
from theodore.analyze.selects import run_selects
from theodore.analyze.themes import run_themes
from theodore.assembly import builder as assembly_builder
from theodore.assembly import ordering as assembly_ordering
from theodore.assembly import plan as assembly_plan
from theodore.assembly import trim as assembly_trim
from theodore.export import captions as export_captions
from theodore.export import edl as export_edl
from theodore.export import notes as export_notes
from theodore.export import review as export_review
from theodore.ingest import audio as ingest_audio
from theodore.ingest import media as ingest_media
from theodore.resolve import connection as resolve_connection
from theodore.resolve import markers as resolve_markers
from theodore.resolve import multicam as resolve_multicam
from theodore.resolve import timecode
from theodore.transcribe import deepgram as dg

logger = logging.getLogger("theodore.cli")

MODEL_TIER_CHOICE = click.Choice(["economy", "standard", "premium"])
ADDRESSING_CHOICE = click.Choice(["sequential", "canonical"])
MODE_CHOICE = click.Choice(list(assembly_ordering.MODES))
MEDIA_EXTS = {".mov", ".mp4", ".mxf", ".wav", ".mp3", ".m4a", ".braw", ".r3d"}


def _setup_logging(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    log_path = project_dir / "theodore.log"
    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. `theodore run` invoking sub-commands)
    root.setLevel(logging.INFO)
    root.addHandler(logging.FileHandler(log_path))
    root.addHandler(logging.StreamHandler(sys.stderr))
    for handler in root.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))


class Stopwatch:
    """Prints progress with elapsed time -- a 90-minute interview takes real
    minutes to process, and a silent terminal feels broken."""

    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self.start = time.monotonic()
        click.echo(f"-> {self.label}...")
        return self

    def __exit__(self, *exc):
        click.echo(f"   done in {time.monotonic() - self.start:.1f}s")


def _media_files(source: Path) -> list[Path]:
    """A single file, or every media file under `source`, searched
    recursively -- real camera/audio recorder dumps are almost always
    organized into subfolders (per card, per file type, per session), so a
    flat one-level scan would silently find nothing even though the media
    is right there. AppleDouble sidecar files (macOS writes a hidden
    "._name.ext" next to every real file on exFAT/FAT32 drives, which most
    camera SSDs use) are skipped -- same extension as the real file, but a
    few KB of metadata, not media ffprobe can read.
    """
    if source.is_file():
        return [source]
    return sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS and not p.name.startswith("._")
    )


def _load_registry(project: str) -> tuple[Path, dict]:
    project_dir = config.project_dir(project)
    return project_dir, registry.load_project(project_dir, project_name=project)


def _require_subject_dir(project_dir: Path, reg: dict, subject: str) -> Path:
    try:
        registry.require_subject(reg, subject)
    except registry.RegistryError as exc:
        raise click.ClickException(str(exc)) from exc
    return registry.subject_dir(project_dir, subject)


@click.group()
def cli():
    """Theodore -- AI post-production assistant for interview/documentary editing."""


def _env_get(lines: list[str], key: str) -> str:
    for line in lines:
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].strip()
    return ""


def _env_set(lines: list[str], key: str, value: str) -> list[str]:
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    return new_lines


@cli.command()
def setup():
    """Interactive first-time setup: writes your Anthropic/Deepgram API
    keys into .env and checks that ffmpeg is installed.

    Safe to re-run -- it never overwrites a key that's already set (so it
    never prints one back to the screen either), and just checks
    everything else again.
    """
    env_path = config.PROJECT_ROOT / ".env"
    example_path = config.PROJECT_ROOT / ".env.example"

    if not env_path.exists():
        env_path.write_text(
            example_path.read_text() if example_path.exists()
            else "ANTHROPIC_API_KEY=\nDEEPGRAM_API_KEY=\n"
        )
        click.echo(f"Created {env_path}")

    lines = env_path.read_text().splitlines()
    changed = False

    if _env_get(lines, "ANTHROPIC_API_KEY"):
        click.echo("Anthropic API key: already set.")
    else:
        click.echo("\nTheodore needs an Anthropic API key for its Claude passes "
                    "(segmenter, selects, themes, dupes, find, say, ...).")
        click.echo("Get one at https://console.anthropic.com/ if you don't have one.")
        key = click.prompt("Paste your Anthropic API key (blank to skip for now)",
                            hide_input=True, default="", show_default=False)
        if key.strip():
            lines = _env_set(lines, "ANTHROPIC_API_KEY", key.strip())
            changed = True
        else:
            click.echo("   Skipped -- Claude-powered commands won't work until this is set.")

    if _env_get(lines, "DEEPGRAM_API_KEY"):
        click.echo("Deepgram API key: already set.")
    else:
        click.echo("\nTheodore needs a Deepgram API key to transcribe audio.")
        click.echo("Get one at https://console.deepgram.com/ if you don't have one.")
        key = click.prompt("Paste your Deepgram API key (blank to skip for now)",
                            hide_input=True, default="", show_default=False)
        if key.strip():
            lines = _env_set(lines, "DEEPGRAM_API_KEY", key.strip())
            changed = True
        else:
            click.echo("   Skipped -- `theodore transcribe` won't work until this is set.")

    if changed:
        env_path.write_text("\n".join(lines) + "\n")
        click.echo(f"\nSaved to {env_path}")

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    click.echo(f"\nffmpeg on PATH:  {'yes' if ffmpeg_ok else 'NO -- install it from https://ffmpeg.org/ and make sure it is on PATH'}")
    click.echo(f"ffprobe on PATH: {'yes' if ffprobe_ok else 'NO -- ffprobe ships with ffmpeg; check your install'}")

    ready = _env_get(lines, "ANTHROPIC_API_KEY") and _env_get(lines, "DEEPGRAM_API_KEY") and ffmpeg_ok and ffprobe_ok
    click.echo()
    if ready:
        click.echo("Everything's set. Try: theodore run <video file> --project <name> --subject <id>")
    else:
        click.echo("Not fully set up yet -- fix what's flagged above, then re-run `theodore setup`.")


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--project", required=True)
@click.option("--subject", required=True, help="Subject id for this footage (e.g. 'haylee'). Registered automatically if new.")
@click.option("--display-name", default=None, help="Human display name for a newly registered subject (default: the subject id).")
def ingest(source: Path, project: str, subject: str, display_name: Optional[str]):
    """Ingest a video file, audio file, or directory of files for one subject."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    try:
        registry.register_subject(reg, subject, display_name=display_name)
    except registry.RegistryError as exc:
        raise click.ClickException(str(exc)) from exc
    subj_dir = registry.subject_dir(project_dir, subject)

    files = _media_files(source)
    if not files:
        raise click.ClickException(f"No media files found at {source}")

    succeeded: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for f in files:
        try:
            with Stopwatch(f"Probing {f.name}"):
                info = ingest_media.probe(f)
            click.echo(f"   fps={info.fps}  duration={info.duration_seconds:.1f}s  start_tc={info.start_timecode}")

            with Stopwatch(f"Extracting audio from {f.name}"):
                wav_path = ingest_audio.extract_wav(f, subj_dir / "audio")
            click.echo(f"   -> {wav_path}")
        except (ingest_media.MediaProbeError, ingest_audio.AudioExtractionError) as exc:
            # One unreadable file (a codec ffprobe can't parse, a corrupt
            # transfer) must not throw away every file already ingested
            # before it, or skip every file still queued after it -- a
            # multi-hour batch failing over one bad clip is worse than the
            # bad clip itself. Reported clearly, both here and in the
            # summary, rather than silently dropped.
            click.echo(f"   SKIPPED -- {exc}")
            failed.append((f, str(exc)))
            continue

        info.audio_hash = wav_path.stem
        ingest_media.save_media_info(info, subj_dir)
        succeeded.append(f)

    if not succeeded:
        raise click.ClickException(
            f"None of the {len(files)} file(s) found at {source} could be ingested -- see the "
            "SKIPPED reasons above."
        )

    source_files = set(reg["subjects"][subject].get("source_files", []))
    source_files.update(f.name for f in succeeded)
    reg["subjects"][subject]["source_files"] = sorted(source_files)
    registry.save_project(project_dir, reg)

    click.echo(f"\nIngested {len(succeeded)} of {len(files)} file(s) for subject '{subject}' in project '{project}'.")
    if failed:
        click.echo(f"\n{len(failed)} file(s) skipped -- fix these and re-run `theodore ingest` on them if needed:")
        for f, reason in failed:
            click.echo(f"  - {f.name}: {reason}")


def _prompt_speaker_names(transcript: dict) -> dict:
    samples: dict[str, list[str]] = {}
    for u in transcript["utterances"]:
        words = samples.setdefault(u["speaker"], [])
        if len(words) < 30:
            words.extend(u["text"].split())

    click.echo("\nName the speakers Deepgram detected (press Enter to keep the default):\n")
    names = {}
    for speaker_id, words in samples.items():
        preview = " ".join(words[:30])
        click.echo(f'Speaker {speaker_id}: "{preview}..."')
        names[speaker_id] = click.prompt(f"  Name for Speaker {speaker_id}", default=f"Speaker {speaker_id}")
    return names


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--interviewer", default=None, help="Speaker id to hint as the interviewer (e.g. '1').")
@click.option("--force", is_flag=True, help="Re-transcribe even if a cached transcript exists.")
def transcribe(project: str, subject: str, interviewer: Optional[str], force: bool):
    """Transcribe ALL of one subject's ingested audio with Deepgram Nova-3,
    merged into one continuous transcript, and name speakers."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    media_list = ingest_media.load_media_info(subj_dir)
    media_by_hash = {m["audio_hash"]: m for m in media_list if m.get("audio_hash")}
    wavs = list((subj_dir / "audio").glob("*.wav"))
    if not wavs:
        raise click.ClickException(f"No extracted audio found for subject '{subject}' -- run `theodore ingest` first.")

    # Ordered by the ORIGINAL source file's name, not the content-hash the
    # extracted wav is named after -- a hash sorts arbitrarily, but a
    # recorder's sequential filenames (take 1, take 2, ...) sort
    # chronologically, which is what turns separate files back into one
    # continuous conversation in the right order.
    def _original_name(wav_path: Path) -> str:
        media = media_by_hash.get(wav_path.stem)
        return Path(media["path"]).name if media else wav_path.name
    wavs.sort(key=_original_name)

    if len(wavs) > 1:
        click.echo(f"Transcribing {len(wavs)} audio files for '{subject}', in this order:")
        for w in wavs:
            click.echo(f"  - {_original_name(w)}")

    entries: list[tuple[dict, float]] = []
    for wav_path in wavs:
        media = media_by_hash.get(wav_path.stem) or (media_list[0] if media_list else None)
        if media is None:
            raise click.ClickException("No media metadata found -- run `theodore ingest` first.")
        with Stopwatch(f"Transcribing {_original_name(wav_path)} with Deepgram Nova-3"):
            raw = dg.transcribe_audio(wav_path, subj_dir, force=force)
        one = dg.normalize(raw, media["path"], media["fps"], media["start_timecode"])
        entries.append((one, media.get("duration_seconds", 0.0)))

    transcript = dg.merge_normalized_transcripts(entries)

    speakers_path = subj_dir / "speakers.json"
    if speakers_path.exists():
        names = json.loads(speakers_path.read_text())
    else:
        names = _prompt_speaker_names(transcript)
        speakers_path.write_text(json.dumps(names, indent=2))
    transcript = dg.apply_speaker_names(transcript, names)

    if interviewer:
        transcript["interviewer"] = interviewer

    dg.save_normalized(transcript, subj_dir)

    reg["subjects"][subject]["speaker_map"] = names
    registry.save_project(project_dir, reg)

    click.echo(f"   {len(transcript['utterances'])} utterances, {len(transcript['speakers'])} speakers")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--addressing", type=ADDRESSING_CHOICE, default=None,
              help="Overrides the project's addressing mode for this run (default: canonical if a question_guide exists, else sequential).")
@click.option("--allow-cost-over", is_flag=True, help="Proceed even if the pre-flight estimate exceeds THEODORE_MAX_COST_USD.")
def analyze(project: str, subject: str, model_tier: str, addressing: Optional[str], allow_cost_over: bool):
    """Run the segmenter, selects, and themes Claude passes for one subject."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    transcript_path = subj_dir / "transcript.json"
    if not transcript_path.exists():
        raise click.ClickException(f"No transcript for '{subject}' -- run `theodore transcribe` first.")
    transcript = json.loads(transcript_path.read_text())

    transcript_chars = sum(len(u["text"]) for u in transcript["utterances"])
    est_cost = config.estimate_preflight_cost(transcript_chars, model_tier)
    click.echo(f"Pre-flight cost estimate: ~${est_cost:.2f} (rough; model tier '{model_tier}')")
    if est_cost > config.MAX_ESTIMATED_COST_USD and not allow_cost_over:
        raise click.ClickException(
            f"Estimated cost ~${est_cost:.2f} exceeds the ${config.MAX_ESTIMATED_COST_USD:.2f} "
            "guardrail (THEODORE_MAX_COST_USD). Re-run with --allow-cost-over to proceed anyway."
        )

    cost_tracker = CostTracker()
    interviewer = transcript.get("interviewer")

    segments_path = subj_dir / "segments.json"
    existing_segments = json.loads(segments_path.read_text())["segments"] if segments_path.exists() else []
    next_number = registry.next_question_number(reg, subject)

    with Stopwatch("Segmenting Q/A structure (immutable ids preserved across re-runs)"):
        seg_result, next_number = run_segmenter(
            transcript, subject_id=subject, interviewer=interviewer, model_tier=model_tier,
            cost_tracker=cost_tracker, existing_segments=existing_segments, next_question_number=next_number,
        )
    click.echo(f"   {len(seg_result['segments'])} segments")
    registry.set_next_question_number(reg, subject, next_number)

    guide = reg.get("question_guide") or []
    effective_addressing = addressing or reg.get("addressing") or ("canonical" if guide else "sequential")
    if effective_addressing == "canonical" and guide:
        with Stopwatch("Matching questions to the project's canonical guide"):
            matches = match_to_guide(seg_result["segments"], guide, cost_tracker=cost_tracker)
            apply_canonical_ids(seg_result["segments"], matches)
        click.echo(f"   matched {len(matches)}/{len(seg_result['segments'])} segments to the guide")
    reg["addressing"] = effective_addressing
    registry.save_project(project_dir, reg)

    segments_path.write_text(json.dumps(seg_result, indent=2))

    delivery = analyze_delivery.load_delivery(subj_dir)
    if delivery:
        click.echo("   found delivery.json -- scoring selects with delivery as a second axis")
    with Stopwatch("Scoring selects"):
        selects_result = run_selects(
            seg_result["segments"], transcript, model_tier=model_tier,
            cost_tracker=cost_tracker, delivery=delivery,
        )
    (subj_dir / "selects.json").write_text(json.dumps(selects_result, indent=2))

    with Stopwatch("Tagging themes"):
        themes_result = run_themes(seg_result["segments"], model_tier=model_tier, cost_tracker=cost_tracker)
    (subj_dir / "themes.json").write_text(json.dumps(themes_result, indent=2))

    analysis = {
        "segments": seg_result["segments"],
        "utterances": transcript["utterances"],
        "selects": selects_result["selects"],
        "themes": themes_result["themes"],
        "theme_assignments": themes_result["assignments"],
    }
    (subj_dir / "analysis.json").write_text(json.dumps(analysis, indent=2))

    click.echo(f"\nEstimated cost this run:\n{cost_tracker.summary()}")


def _load_transcript(subj_dir: Path, subject: str) -> dict:
    path = subj_dir / "transcript.json"
    if not path.exists():
        raise click.ClickException(f"No transcript for '{subject}' -- run `theodore transcribe` first.")
    return json.loads(path.read_text())


def _load_analysis(subj_dir: Path, subject: str) -> dict:
    path = subj_dir / "analysis.json"
    if not path.exists():
        raise click.ClickException(f"No analysis for '{subject}' -- run `theodore analyze` first.")
    return json.loads(path.read_text())


def _find_subject_wav(subj_dir: Path, subject: str) -> Path:
    wavs = sorted((subj_dir / "audio").glob("*.wav"))
    if not wavs:
        raise click.ClickException(f"No extracted audio for '{subject}' -- run `theodore ingest` first.")
    return wavs[0]


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def delivery(project: str, subject: str):
    """Extract delivery (prosody) profiles: pitch, energy, pauses, onset
    delay, jitter/shimmer, scored relative to this subject's own baseline.

    Needs `theodore analyze` to have run at least once already (it profiles
    the segments analyze produced). Re-run `theodore analyze` afterward to
    have Selects re-score with delivery as a second axis.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)
    wav_path = _find_subject_wav(subj_dir, subject)

    with Stopwatch(f"Extracting delivery profiles from {wav_path.name}"):
        result = analyze_delivery.analyze_delivery(wav_path, transcript, analysis)
    out = analyze_delivery.save_delivery(result, subj_dir)

    scored = sum(1 for s in result["segments"].values() if s.get("divergence") is not None)
    click.echo(f"   {len(result['segments'])} segments profiled, {scored} with a usable baseline")
    click.echo(f"\nWrote {out}\nRe-run `theodore analyze --project {project} --subject {subject}` "
               "to have Selects re-score with delivery as a second axis.")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--limit", default=10, type=int, help="How many segments to show.")
def peaks(project: str, subject: str, limit: int):
    """Segments ranked by how far their delivery diverges from this
    subject's own baseline -- per the spec, "almost always your best
    footage and the hardest to find by reading." Good first read on new
    footage.
    """
    project_dir, reg = _load_registry(project)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    analysis = _load_analysis(subj_dir, subject)
    delivery_data = analyze_delivery.load_delivery(subj_dir)
    if delivery_data is None:
        raise click.ClickException(f"No delivery data for '{subject}' -- run `theodore delivery` first.")

    ranked = analyze_delivery.rank_by_divergence(delivery_data, analysis)
    if not ranked:
        click.echo("No segments have enough of a baseline yet to rank.")
        return

    for row in ranked[:limit]:
        click.echo(f"\n[{row['divergence']:.2f}] {row['segment_id']} -- {row['label']}")
        for line in row["descriptor"].splitlines()[1:]:
            click.echo(f"  {line}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
def dupes(project: str, subject: str, model_tier: str):
    """Find segments that tell essentially the same story or give
    essentially the same answer more than once, so an editor only has to
    pick one take instead of re-watching every version of it.

    Needs `theodore analyze` to have run at least once already -- it pools
    candidate segments using the theme tags that pass produced, so this
    stays cheap instead of comparing every segment against every other.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    analysis = _load_analysis(subj_dir, subject)
    cost_tracker = CostTracker()

    with Stopwatch("Finding redundant segments"):
        result = analyze_redundancy.run_redundancy(
            analysis["segments"], analysis.get("theme_assignments", {}), analysis.get("selects", []),
            model_tier=model_tier, cost_tracker=cost_tracker,
        )
    out = analyze_redundancy.save_redundancy(result, subj_dir)

    if not result["groups"]:
        click.echo("No redundant segments found.")
    else:
        segments_by_id = {s["id"]: s for s in analysis["segments"]}
        for group in result["groups"]:
            click.echo(f"\n[{group['id']}] {len(group['segment_ids'])} segments cover the same ground:")
            for sid in group["segment_ids"]:
                mark = "-> keep" if sid == group["recommended_id"] else "  drop?"
                label = segments_by_id.get(sid, {}).get("question_text") or "(volunteered)"
                click.echo(f"  {mark}  {sid}  {label}")
            click.echo(f"  reason: {group['reason']}")

    click.echo(f"\nWrote {out}")
    click.echo(f"\nEstimated cost this run:\n{cost_tracker.summary()}")


@cli.command()
@click.argument("query")
@click.option("--project", required=True)
@click.option("--subject", default=None, help="Search only this subject; omit to search every registered subject in the project.")
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--limit", default=10, type=int, help="How many matches to show.")
def find(query: str, project: str, subject: Optional[str], model_tier: str, limit: int):
    """Search a project's segments in plain language, e.g.
    `theodore find "someone talking about losing their mother"`.

    Scoped to one subject with --subject, or every registered subject in
    the project by default -- segment ids already carry their subject
    prefix (haylee.q03), so cross-subject results stay unambiguous.
    Needs `theodore analyze` to have run for whichever subjects it searches.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)

    if subject:
        _require_subject_dir(project_dir, reg, subject)
        subjects = [subject]
    else:
        subjects = registry.list_subjects(reg)
    if not subjects:
        raise click.ClickException("No subjects registered yet -- run `theodore ingest --subject <name> ...` to add one.")

    segments: list[dict] = []
    selects: list[dict] = []
    segments_by_id: dict[str, dict] = {}
    searched = []
    for subj in subjects:
        subj_dir = registry.subject_dir(project_dir, subj)
        analysis_path = subj_dir / "analysis.json"
        if not analysis_path.exists():
            continue
        analysis = json.loads(analysis_path.read_text())
        segments.extend(analysis.get("segments", []))
        selects.extend(analysis.get("selects", []))
        segments_by_id.update({s["id"]: s for s in analysis.get("segments", [])})
        searched.append(subj)

    if not segments:
        raise click.ClickException("No analyzed segments found -- run `theodore analyze` for at least one subject first.")

    cost_tracker = CostTracker()
    with Stopwatch(f"Searching {len(segments)} segments across {len(searched)} subject(s)"):
        result = analyze_search.run_search(
            segments, query, model_tier=model_tier, cost_tracker=cost_tracker, selects=selects,
        )

    matches = result["matches"]
    if not matches:
        click.echo("No matches found.")
    else:
        for m in matches[:limit]:
            seg = segments_by_id.get(m["segment_id"], {})
            label = seg.get("question_text") or "(volunteered)"
            click.echo(f"\n[{m.get('relevance', 0):.2f}] {m['segment_id']} -- {label}")
            click.echo(f"  {m.get('reason', '')}")

    click.echo(f"\nEstimated cost this run:\n{cost_tracker.summary()}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--threshold", default=analyze_coverage.REJECT_STRENGTH_THRESHOLD, type=float,
              help=f"Strength below which a segment counts as a reject (default {analyze_coverage.REJECT_STRENGTH_THRESHOLD}).")
def rejects(project: str, subject: str, threshold: float):
    """Segments Selects scored too weak to use as-is -- what's NOT making
    the cut, and why, sorted weakest first. Pure re-read of `analysis.json`,
    no API calls."""
    project_dir, reg = _load_registry(project)
    subj_dir = _require_subject_dir(project_dir, reg, subject)
    analysis = _load_analysis(subj_dir, subject)

    rejected = analyze_coverage.find_rejects(analysis["segments"], analysis.get("selects", []), threshold=threshold)
    if not rejected:
        click.echo(f"No segments below strength {threshold:.2f}.")
        return

    click.echo(f"{len(rejected)} segment(s) below strength {threshold:.2f}:")
    for r in rejected:
        click.echo(f"\n[{r['strength']:.2f}] {r['segment_id']} -- {r['question_text'] or '(volunteered)'}")
        if r["issues"]:
            click.echo(f"  issues: {', '.join(r['issues'])}")
        if r["rationale"]:
            click.echo(f"  {r['rationale']}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def gaps(project: str, subject: str):
    """Canonical question-guide entries this subject never answered.
    Needs a `question_guide` in project.json and `--addressing canonical`
    to have run (the default once a guide exists)."""
    project_dir, reg = _load_registry(project)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    guide = reg.get("question_guide") or []
    if not guide:
        raise click.ClickException(
            "This project has no question_guide defined in project.json -- nothing to check gaps against."
        )
    analysis = _load_analysis(subj_dir, subject)

    missing = analyze_coverage.find_gaps(analysis["segments"], guide)
    if not missing:
        click.echo(f"'{subject}' covers every question in the guide ({len(guide)}/{len(guide)}).")
        return

    click.echo(f"'{subject}' is missing {len(missing)}/{len(guide)} guide question(s):")
    for g in missing:
        click.echo(f"  [{g['id']}] {g['canonical']}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def learn(project: str, subject: str):
    """Compares Selects' strength predictions against what the current
    edit list version actually keeps, surfacing where the model's read
    and the real editorial decision diverge. Observational only -- nothing
    here changes future scoring automatically."""
    project_dir, reg = _load_registry(project)
    subj_dir = _require_subject_dir(project_dir, reg, subject)
    analysis = _load_analysis(subj_dir, subject)

    summary = analyze_learning.summarize(project_dir, subject, analysis["segments"], analysis.get("selects", []))

    if summary["versions_checked"] == 0:
        click.echo(f"No edit list versions yet for '{subject}' -- run `theodore build --mode <mode>` at least once.")
        return

    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    click.echo(f"Checked {summary['versions_checked']} version(s) of '{subject}''s edit list.")
    kept_line = f"  kept (current):    {summary['kept_count']} segment(s)"
    if summary["avg_strength_kept"] is not None:
        kept_line += f", avg strength {summary['avg_strength_kept']:.2f}"
    click.echo(kept_line)
    dropped_line = f"  dropped (current): {summary['dropped_count']} segment(s)"
    if summary["avg_strength_dropped"] is not None:
        dropped_line += f", avg strength {summary['avg_strength_dropped']:.2f}"
    click.echo(dropped_line)

    if summary["dropped_despite_high_strength"]:
        click.echo("\nSelects rated these strong, but they're not in the current cut:")
        for sid in summary["dropped_despite_high_strength"]:
            seg = segments_by_id.get(sid, {})
            click.echo(f"  {sid}  {seg.get('question_text') or '(volunteered)'}")

    if summary["kept_despite_low_strength"]:
        click.echo("\nSelects rated these weak, but they're in the current cut anyway:")
        for sid in summary["kept_despite_low_strength"]:
            seg = segments_by_id.get(sid, {})
            click.echo(f"  {sid}  {seg.get('question_text') or '(volunteered)'}")

    if not summary["dropped_despite_high_strength"] and not summary["kept_despite_low_strength"]:
        click.echo("\nNo surprises -- your choices track Selects' strength closely.")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--dry-run", is_flag=True, help="Print markers that would be written; touch nothing.")
@click.option("--overwrite", is_flag=True, help="Update markers Theodore previously wrote, instead of offsetting.")
def markers(project: str, subject: str, dry_run: bool, overwrite: bool):
    """Write one subject's segment analysis onto the current Resolve timeline as markers."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    plans = resolve_markers.plan_markers(transcript, analysis)
    click.echo(f"Planned {len(plans)} markers from analysis.")

    if dry_run:
        click.echo(resolve_markers.format_dry_run(plans, transcript["fps"]))
        return

    try:
        handles = resolve_connection.connect()
    except resolve_connection.ResolveConnectionError as exc:
        click.echo(f"Could not connect to Resolve: {exc}\n")
        edl_path = subj_dir / "markers.edl"
        export_edl.write_edl(plans, transcript["fps"], f"{project}_{subject}", edl_path)
        click.echo(
            f"Wrote a marker EDL to {edl_path} instead -- your analysis isn't lost.\n"
            "Import it in Resolve via File > Import Timeline > Import AAF, EDL, XML... "
            "and enable 'timeline markers' in the import options to bring the markers in."
        )
        return

    click.echo(f"Connected to timeline: {resolve_connection.describe_timeline(handles)}")

    timeline_fps = handles.timeline.GetSetting("timelineFrameRate")
    if not resolve_markers.check_fps_match(timeline_fps, transcript["fps"]):
        click.echo(
            f"WARNING: timeline frame rate ({timeline_fps}) does not match source media "
            f"frame rate ({transcript['fps']}). Marker positions would be wrong -- Theodore "
            "does not silently convert between frame rates. Aborting."
        )
        raise click.exceptions.Exit(1)

    result = resolve_markers.write_markers(handles, plans, overwrite=overwrite)
    click.echo(
        f"Wrote {result['written']} markers, replaced {result['replaced']}, "
        f"offset {result['offset']} for collisions, skipped {result['skipped']}."
    )


def _print_summary(analysis: dict):
    selects = analysis.get("selects", [])
    scored = [s for s in selects if s.get("strength") is not None]
    click.echo(f"\n{len(analysis['segments'])} segments, {len(scored)} scored")
    if not scored:
        return
    avg = sum(s["strength"] for s in scored) / len(scored)
    click.echo(f"Average strength: {avg:.2f}")
    top = sorted(scored, key=lambda s: s["strength"], reverse=True)[:5]
    segments_by_id = {s["id"]: s for s in analysis["segments"]}
    click.echo("Top 5 segments:")
    for s in top:
        seg = segments_by_id.get(s["segment_id"], {})
        click.echo(f"  {s['strength']:.2f}  {seg.get('question_label', s['segment_id'])}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def notes(project: str, subject: str):
    """Export one subject's interview_notes.md and selects.csv."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    md_path = export_notes.write_markdown(transcript, analysis, subj_dir / "interview_notes.md")
    csv_path = export_notes.write_csv(transcript, analysis, subj_dir / "selects.csv")

    _print_summary(analysis)
    click.echo(f"\nWrote {md_path} and {csv_path}")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def ids(project: str, subject: str):
    """Print the immutable segment id -> question mapping for one subject."""
    project_dir, reg = _load_registry(project)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    segments_path = subj_dir / "segments.json"
    if not segments_path.exists():
        raise click.ClickException(f"No segments for '{subject}' yet -- run `theodore analyze` first.")
    segments = json.loads(segments_path.read_text())["segments"]

    addressing = reg.get("addressing", "sequential")
    click.echo(f"Subject: {subject}  (addressing: {addressing})\n")
    for seg in segments:
        canonical = seg.get("canonical_question_id")
        canonical_suffix = f"  [guide: {canonical}]" if canonical else ""
        label = seg.get("question_label") or seg.get("question_text") or "(volunteered)"
        click.echo(f"  {seg['id']:<16} {label}{canonical_suffix}")


def _parse_exclude(exclude: Optional[str], analysis: dict) -> set:
    """Parse a comma-separated --exclude list, validating every id against
    the analysis. An unknown id is an error, never silently ignored: the
    whole point of the review.html -> --exclude loop is that the ids you
    paste back are acted on exactly, and quietly dropping a typo'd id would
    put a segment you meant to cut back into the assembly."""
    if not exclude:
        return set()
    requested = {s.strip() for s in exclude.split(",") if s.strip()}
    known = {s["id"] for s in analysis["segments"]}
    unknown = requested - known
    if unknown:
        raise click.ClickException(
            f"--exclude names segment id(s) not in this subject's analysis: "
            f"{', '.join(sorted(unknown))}.\nKnown ids: {', '.join(sorted(known))}"
        )
    return requested


def _parse_duration_seconds(value: str) -> float:
    """Accepts plain seconds ("90") or clock-time ("1:30", "1:02:30") --
    intent-level duration, not a frame-accurate SMPTE timecode."""
    parts = value.split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise click.ClickException(f"--target must be seconds or MM:SS / HH:MM:SS, got {value!r}") from exc
    if len(numbers) == 1:
        seconds = numbers[0]
    elif len(numbers) == 2:
        seconds = numbers[0] * 60 + numbers[1]
    elif len(numbers) == 3:
        seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    else:
        raise click.ClickException(f"--target must be seconds or MM:SS / HH:MM:SS, got {value!r}")
    if seconds <= 0:
        raise click.ClickException(f"--target must be a positive duration, got {value!r}")
    return seconds


def _prepare_assembly(
    subj_dir: Path,
    subject: str,
    *,
    mode: str,
    handles: int,
    silence_threshold: float,
    aggressive_trim: bool,
    exclude: Optional[str],
    model_tier: str,
    cost_tracker: CostTracker,
    apply_trim: bool = True,
):
    """Shared front half of every assembly-facing command: load the
    subject's transcript/analysis, compute (and persist) trim proposals,
    resolve the assembly order, and turn all of that into the concrete
    per-clip frame plan. `apply_trim=False` is the `untrim` path -- an
    empty trims dict makes assembly.plan fall back to each segment's full
    clean range."""
    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    trims: dict = {}
    if apply_trim:
        trims = assembly_trim.compute_trims(
            analysis, transcript,
            silence_threshold=silence_threshold, aggressive=aggressive_trim,
        )
        assembly_trim.save_trims(trims, subj_dir)

    excluded = _parse_exclude(exclude, analysis)

    order, rationale = assembly_ordering.compute_order(
        analysis, mode, model_tier=model_tier, cost_tracker=cost_tracker,
    )
    plan = assembly_plan.build_plan(
        transcript, analysis, trims, order,
        handle_frames=handles, excluded=excluded,
    )
    return transcript, analysis, trims, order, rationale, plan, excluded


def _assembly_options(fn):
    """The flags every assembly-facing command shares."""
    fn = click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)(fn)
    fn = click.option("--exclude", default=None, help="Comma-separated segment ids to leave out (paste from review.html).")(fn)
    fn = click.option("--trim/--no-trim", "apply_trim", default=True, help="Apply trim proposals (default) or preview at full length.")(fn)
    fn = click.option("--aggressive-trim", is_flag=True, help="Also cut interior filler words, not just boundary filler.")(fn)
    fn = click.option("--silence-threshold", default=config.DEFAULT_SILENCE_THRESHOLD_SECONDS, type=float)(fn)
    fn = click.option("--handles", default=config.DEFAULT_HANDLE_FRAMES, type=int, help="Handle frames on each side of every clip.")(fn)
    fn = click.option("--mode", type=MODE_CHOICE, default="chronological")(fn)
    fn = click.option("--subject", required=True)(fn)
    fn = click.option("--project", required=True)(fn)
    return fn


def _report_runtime(plan, fps) -> None:
    total_frames = assembly_plan.total_runtime_frames(plan)
    click.echo(f"   {len(plan)} clips, runtime {timecode.frames_to_timecode(total_frames, fps)} ({total_frames} frames)")


@cli.command()
@_assembly_options
def review(project, subject, mode, handles, silence_threshold, aggressive_trim, apply_trim, exclude, model_tier):
    """Generate review.html for the proposed assembly -- read this before building."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    cost_tracker = CostTracker()
    transcript, analysis, trims, order, rationale, plan, excluded = _prepare_assembly(
        subj_dir, subject, mode=mode, handles=handles, silence_threshold=silence_threshold,
        aggressive_trim=aggressive_trim, exclude=exclude, model_tier=model_tier,
        cost_tracker=cost_tracker, apply_trim=apply_trim,
    )

    out = export_review.write_review_html(
        f"{project} / {subject}", transcript, analysis, trims, subj_dir / "review.html",
        segment_order=order, mode=mode, rationale=rationale, excluded=excluded,
    )
    _report_runtime(plan, transcript["fps"])
    if cost_tracker.calls:
        click.echo(f"\nEstimated cost this run:\n{cost_tracker.summary()}")
    click.echo(f"\nWrote {out}\nOpen it, uncheck what you don't want, then re-run with the --exclude string it gives you.")


@cli.command()
@_assembly_options
@click.option("--srt/--no-srt", default=True, help="Write captions.srt (default on).")
@click.option("--vtt/--no-vtt", default=True, help="Write captions.vtt (default on).")
@click.option("--import-to-resolve", is_flag=True, help="Also try to import the SRT onto the currently open Resolve timeline.")
def captions(project, subject, mode, handles, silence_threshold, aggressive_trim, apply_trim,
             exclude, model_tier, srt, vtt, import_to_resolve):
    """Export SRT/WebVTT captions timed against the assembly, not the source."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    cost_tracker = CostTracker()
    transcript, analysis, trims, order, rationale, plan, excluded = _prepare_assembly(
        subj_dir, subject, mode=mode, handles=handles, silence_threshold=silence_threshold,
        aggressive_trim=aggressive_trim, exclude=exclude, model_tier=model_tier,
        cost_tracker=cost_tracker, apply_trim=apply_trim,
    )

    cues = export_captions.build_cues(transcript, analysis, trims, plan)
    click.echo(f"Built {len(cues)} caption cues across {len(plan)} clips.")

    srt_path = None
    if srt:
        srt_path = export_captions.write_srt(cues, subj_dir / "captions.srt")
        click.echo(f"   {srt_path}")
    if vtt:
        click.echo(f"   {export_captions.write_vtt(cues, subj_dir / 'captions.vtt')}")

    if import_to_resolve:
        if srt_path is None:
            raise click.ClickException("--import-to-resolve needs the SRT; don't pass --no-srt with it.")
        try:
            handles_ = resolve_connection.connect()
        except resolve_connection.ResolveConnectionError as exc:
            click.echo(f"\nCould not connect to Resolve, so the SRT wasn't imported: {exc}")
        else:
            if export_captions.import_subtitles_to_timeline(handles_, srt_path):
                click.echo(f"\nImported the subtitle track onto {resolve_connection.describe_timeline(handles_)}")
            else:
                click.echo(
                    f"\nThis Resolve version wouldn't take the subtitle import via scripting.\n"
                    f"Import it by hand instead: File > Import > Subtitle...  ->  {srt_path}"
                )


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
def quotes(project: str, subject: str):
    """Pull-quote sheet: every best line with timecodes, sorted by strength."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    # Deliberately does NOT run the ordering pass -- quotes are keyed to
    # source timecode and sorted by strength, so there's nothing here that
    # depends on assembly order, and narrative mode would bill a Claude
    # call for nothing.
    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    out = export_captions.write_quotes(analysis, transcript, subj_dir / "quotes.txt")
    click.echo(f"Wrote {out}")


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--display-name", default=None)
@click.option("--interviewer", default=None)
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--addressing", type=ADDRESSING_CHOICE, default=None)
@click.option("--allow-cost-over", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--overwrite", is_flag=True)
@click.pass_context
def run(ctx, source, project, subject, display_name, interviewer, model_tier, addressing, allow_cost_over, dry_run, overwrite):
    """Full pipeline for one subject: ingest -> transcribe -> analyze -> markers -> notes."""
    start = time.monotonic()
    ctx.invoke(ingest, source=source, project=project, subject=subject, display_name=display_name)
    ctx.invoke(transcribe, project=project, subject=subject, interviewer=interviewer, force=False)
    ctx.invoke(analyze, project=project, subject=subject, model_tier=model_tier, addressing=addressing, allow_cost_over=allow_cost_over)
    ctx.invoke(markers, project=project, subject=subject, dry_run=dry_run, overwrite=overwrite)
    ctx.invoke(notes, project=project, subject=subject)
    click.echo(f"\nFull pipeline finished in {time.monotonic() - start:.1f}s")


def _connect_or_die() -> resolve_connection.ResolveHandles:
    try:
        return resolve_connection.connect()
    except resolve_connection.ResolveConnectionError as exc:
        raise click.ClickException(
            f"Could not connect to Resolve: {exc}\n\n"
            "Unlike `theodore markers`, there is no file-based fallback for building an "
            "actual timeline -- appending clips means talking to a live Resolve project. "
            "Your edit list and analysis are safe either way; open Resolve and re-run."
        ) from exc


def _referenced_subjects(segment_ids: list, home_subject: str) -> list:
    """The distinct subjects a sequence reaches into, in first-appearance
    order. An id with no ``subject.`` prefix (a pre-registry project's
    "s001") has no subject of its own and counts as the home subject -- the
    only one in play for such a project."""
    out: list = []
    for sid in segment_ids:
        subject = assembly_plan.subject_of_segment(sid) if "." in str(sid) else home_subject
        if subject not in out:
            out.append(subject)
    return out


def _require_build_subjects(
    project_dir: Path,
    reg: dict,
    segment_ids: list,
    home_subject: str,
    home_transcript: dict,
    home_analysis: dict,
) -> dict:
    """Load and validate every subject an edit list reaches into.

    This replaces v2's outright refusal of cross-subject edit lists. The
    registry was multi-subject and segment ids carried their subject's
    prefix from the start; what was missing was `theodore build` resolving
    each id against its OWN subject's transcript, analysis and media, which
    ``assembly.plan.build_multi_subject_plan`` and ``assembly.builder``'s
    per-subject media bindings now do.

    Called BEFORE a pending queue or a fresh ordering pass is confirmed into
    a real edit list version -- keeping the ordering the refusal it replaces
    was careful about, so a build that cannot work never leaves a version
    behind for a later plain `theodore build` to trip over.

    Returns ``{subject_id: (transcript, analysis)}`` in first-appearance
    order, reusing `home_subject`'s already-loaded pair rather than reading
    it twice. Every failure is a ClickException that names the fix -- an
    unregistered subject, or one registered but not yet transcribed or
    analyzed -- never a KeyError and never a quietly short assembly.
    """
    referenced = _referenced_subjects(segment_ids, home_subject)
    if not referenced:
        # An empty sequence still belongs to the subject that asked for it;
        # the empty-plan error downstream is the useful message here.
        return {home_subject: (home_transcript, home_analysis)}

    loaded: dict = {}
    for subject in referenced:
        if subject == home_subject:
            loaded[subject] = (home_transcript, home_analysis)
            continue
        subj_dir = _require_subject_dir(project_dir, reg, subject)
        loaded[subject] = (
            _load_transcript(subj_dir, subject),
            _load_analysis(subj_dir, subject),
        )

    if len(loaded) > 1:
        bare = sorted({str(sid) for sid in segment_ids if "." not in str(sid)})
        if bare:
            raise click.ClickException(
                f"This sequence mixes subjects but also contains unprefixed segment id(s): "
                f"{', '.join(bare[:5])}. An id without a 'subject.' prefix predates the "
                "multi-subject registry, so there is no way to tell which subject's footage "
                "it means -- and Theodore will not guess when the answer decides which "
                "camera original a cut comes from. Re-run `theodore analyze` for that "
                "subject to give its segments prefixed ids."
            )
    return loaded


def _plan_for_sequence(
    loaded: dict,
    segment_ids: list,
    *,
    subject: str,
    transcript: dict,
    analysis: dict,
    trims: dict,
    handle_frames: int,
) -> tuple:
    """Plan `segment_ids`, single-subject or across subjects, and return
    ``(plan, source_kwargs)`` -- the kwargs naming the sources for whichever
    of ``describe_build``/``build_timeline`` runs next.

    One subject (the overwhelmingly common case) keeps the original
    single-transcript path exactly as it was. Anything reaching another
    subject's segments plans per-subject instead, after refusing a
    mixed-frame-rate assembly here, before Resolve is even connected to --
    ``build_timeline`` re-checks it regardless, so this is the friendly
    early message rather than the guard.

    `loaded` comes from :func:`_require_build_subjects`, which every caller
    runs before persisting anything.
    """
    if list(loaded) == [subject]:
        plan = assembly_plan.build_plan(
            transcript, analysis, trims, segment_ids, handle_frames=handle_frames,
        )
        return plan, {"transcript": transcript, "analysis": analysis}

    sources = {
        sid: assembly_plan.SubjectSources(subject_transcript, subject_analysis, trims)
        for sid, (subject_transcript, subject_analysis) in loaded.items()
    }
    try:
        assembly_builder.common_source_fps(sources)
    except assembly_builder.BuilderError as exc:
        raise click.ClickException(str(exc)) from exc
    plan = assembly_plan.build_multi_subject_plan(
        sources, segment_ids, handle_frames=handle_frames,
    )
    click.echo(f"   cross-subject build: {', '.join(loaded)}")
    return plan, {"sources": sources}


def _merged_base_trims(project_dir: Path, subjects) -> dict:
    """Every involved subject's saved trim proposals, in one dict.

    Segment ids carry their subject's prefix and so are unique across the
    whole project, which makes one flat dict unambiguous -- and it is
    already the shape ``edits.apply_overrides_to_trims`` and
    ``assembly.plan`` both expect. For a single-subject build this is
    exactly ``assembly_trim.load_trims()`` on that one subject, unchanged.
    """
    merged: dict = {}
    for subject in subjects:
        merged.update(assembly_trim.load_trims(registry.subject_dir(project_dir, subject)))
    return merged


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--check-resolve/--no-check-resolve", default=True,
              help="Also ask the open Resolve project which of the proposed multicam clips "
                   "already exist. Skipped silently when Resolve isn't reachable.")
def multicam(project: str, subject: str, check_resolve: bool):
    """Find which of a subject's camera files are angles of the same moment,
    name the multicam clip(s) to create in Resolve, and -- if the subject
    also has a lav or boom recorder's own audio file -- which camera file(s)
    to sync it to.

    Camera grouping works off the embedded timecode `theodore ingest` already
    probed, so it needs no analysis pass and no Resolve connection. Files
    with no embedded timecode (ffprobe reports 00:00:00:00) are never
    grouped -- that is the absence of a sync signal, not a start at frame
    zero. External audio doesn't need embedded timecode at all (see below).

    Theodore does NOT create the multicam clip or touch any audio itself:
    both are manual steps in Resolve. For camera angles: select them ->
    right-click -> New Multicam Clip Using..., name it EXACTLY what this
    command prints, and `theodore build` will pick it up automatically (if
    it doesn't exist yet, the build just uses the plain source clip). For
    external audio: select it with the camera file(s) this command names ->
    right-click -> Auto Sync Audio -> Based on Waveform and Append Tracks
    (Resolve's own audio-waveform sync, not something Theodore recomputes).
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    media = ingest_media.load_media_info(subj_dir)
    if not media:
        raise click.ClickException(
            f"No media.json for '{subject}' -- run `theodore ingest` for this subject first."
        )

    with Stopwatch(f"Checking {len(media)} ingested file(s) for synchronized camera angles"):
        plan = resolve_multicam.plan_multicam(media, subject)
    out = resolve_multicam.save_multicam(plan, subj_dir)

    click.echo(resolve_multicam.format_plan(plan))
    click.echo(f"\nWrote {out}")

    if not plan.groups:
        if not plan.external_audio:
            click.echo(
                "\nNothing to create in Resolve. `theodore build` will use the plain source "
                "clips, which is the normal path."
            )
        return

    existing: list[str] = []
    missing = [g.multicam_clip_name for g in plan.groups]
    if check_resolve:
        try:
            handles = resolve_connection.connect()
        except resolve_connection.ResolveConnectionError as exc:
            click.echo(f"\n(Didn't check the Resolve media pool: {exc.args[0].splitlines()[0]})")
        else:
            status = resolve_multicam.check_multicam_clips(handles, plan)
            existing, missing = status["existing"], status["missing"]
            for name in existing:
                click.echo(f"\nAlready in the media pool: {name}  -- nothing to do.")

    if missing:
        click.echo("\nNEXT STEP -- create these in Resolve, named EXACTLY:")
        for name in missing:
            group = next(g for g in plan.groups if g.multicam_clip_name == name)
            click.echo(f"\n  {name}")
            for angle in group.angles:
                click.echo(f"      {angle.name}")
        click.echo(
            "\n  In Resolve: select those angle clips in the Media Pool, right-click ->\n"
            "  'New Multicam Clip Using...', set Angle Sync to Timecode, and set the\n"
            "  clip name to the exact name above. Then re-run `theodore build`."
        )


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True,
              help="The home subject: whose analysis a --mode ordering pass runs on, and what "
                   "the new timeline is named after. An edit list may reference other "
                   "subjects' segments too; each clip is cut from its own subject's media, "
                   "provided every subject involved shares one frame rate.")
@click.option("--mode", type=MODE_CHOICE, default=None,
              help="Run a fresh ordering pass and seed/derive a new edit version from it. Omit to rebuild the CURRENT version as-is.")
@click.option("--handles", default=config.DEFAULT_HANDLE_FRAMES, type=int)
@click.option("--silence-threshold", default=config.DEFAULT_SILENCE_THRESHOLD_SECONDS, type=float)
@click.option("--aggressive-trim", is_flag=True)
@click.option("--trim/--no-trim", "apply_trim", default=True)
@click.option("--exclude", default=None, help="Comma-separated segment ids to leave out (only meaningful with --mode).")
@click.option("--target", default=None,
              help="Target runtime (seconds or MM:SS / HH:MM:SS). Drops the weakest-scoring segments "
                   "to fit. Only meaningful with --mode -- a fresh ordering pass, never a saved version.")
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--dry-run", is_flag=True)
def build(project, subject, mode, handles, silence_threshold, aggressive_trim, apply_trim,
          exclude, target, model_tier, dry_run):
    """Build (or rebuild) a real Resolve timeline from the project's edit list.

    Never touches an existing timeline -- every build creates a new one. With
    --mode, runs a fresh ordering pass and records it as a new edit list
    version (v001 if none exists yet, otherwise a derived version on top of
    the current one). Without --mode, rebuilds the CURRENT version exactly
    as it stands -- this is what `theodore say` leads to after queued
    commands are confirmed.

    An edit list may span subjects ("move haylee.q03 after marcus.q04"):
    each clip is cut from its own subject's source media at its own frame
    offsets and annotated from its own analysis. Every subject involved must
    share one frame rate, since a Resolve timeline has only one -- a mixed
    rate assembly is refused before anything is created rather than built
    with half its cuts in the wrong place.
    """
    if target is not None and mode is None:
        raise click.ClickException("--target only makes sense with --mode -- it drops segments from a fresh "
                                    "ordering pass, not from a saved edit list version.")

    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)
    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    cost_tracker = CostTracker()
    rationale = None

    if mode is not None:
        # Fresh assembly from an ordering pass. Nothing here is persisted
        # under --dry-run -- computing a preview must not seed a real edit
        # list version or move current_edit_version out from under you.
        trims = {}
        if apply_trim:
            trims = assembly_trim.compute_trims(
                analysis, transcript, silence_threshold=silence_threshold, aggressive=aggressive_trim,
            )
        excluded = _parse_exclude(exclude, analysis)
        order, rationale = assembly_ordering.compute_order(
            analysis, mode, model_tier=model_tier, cost_tracker=cost_tracker,
        )
        segment_ids = [sid for sid in order if sid not in excluded]
        version_label = mode
        # An ordering pass runs on one subject's analysis, so its ids are
        # that subject's by construction -- this is the invariant check, and
        # the place the other two branches genuinely resolve foreign ids.
        loaded = _require_build_subjects(project_dir, reg, segment_ids, subject, transcript, analysis)

        if target is not None:
            target_seconds = _parse_duration_seconds(target)
            target_frames = timecode.seconds_to_frames(target_seconds, transcript["fps"])
            prelim_plan = assembly_plan.build_plan(transcript, analysis, trims, segment_ids, handle_frames=handles)
            selects_by_id = {s["segment_id"]: s for s in analysis.get("selects", [])}
            segment_ids, dropped_for_target = assembly_plan.target_duration_order(
                segment_ids, prelim_plan, selects_by_id, target_frames,
            )
            if dropped_for_target:
                click.echo(f"   --target dropped {len(dropped_for_target)} weakest segment(s) to fit "
                           f"{target}: {', '.join(dropped_for_target)}")

        if not dry_run:
            if apply_trim:
                assembly_trim.save_trims(trims, subj_dir)
            current = edits.load_current(project_dir, reg)
            entries = [edits.EditEntry(sid) for sid in segment_ids]
            if current is None:
                edit_list = edits.create_initial(project_dir, segment_ids, command_log=[f"assemble --mode {mode}"])
            else:
                edit_list = edits.derive(project_dir, current, entries, [f"rebuild --mode {mode}"])
            edits.set_current_version(reg, edit_list.version)
            registry.save_project(project_dir, reg)
            version_label = edit_list.version
    else:
        # A queued `theodore say` session takes priority: confirm it (fork a
        # new version, clear the queue) and build THAT. Under --dry-run,
        # preview what confirming would produce without actually confirming
        # -- same "touches nothing" rule as everywhere else in this command.
        # With nothing queued, just rebuild the CURRENT persisted version
        # exactly as it stands (e.g. after Resolve was closed and reopened).
        pending = edits.load_pending(project_dir)
        if pending is not None:
            segment_ids = pending.segment_ids
            version_label = "(pending)"
            loaded = _require_build_subjects(project_dir, reg, segment_ids, subject, transcript, analysis)
            base_trims = _merged_base_trims(project_dir, loaded)
            trims = edits.apply_overrides_to_trims(pending, base_trims)
            if not dry_run:
                current = edits.load_current(project_dir, reg)
                if current is None:
                    edit_list = edits.create_initial(project_dir, pending.segment_ids, command_log=list(pending.command_log))
                else:
                    edit_list = edits.derive(project_dir, current, list(pending.sequence), list(pending.command_log))
                edits.set_current_version(reg, edit_list.version)
                registry.save_project(project_dir, reg)
                edits.clear_pending(project_dir)
                version_label = edit_list.version
        else:
            edit_list = edits.load_current(project_dir, reg)
            if edit_list is None:
                raise click.ClickException(
                    "No edit list yet for this project -- run `theodore build --mode <mode>` "
                    "once to seed one, or `theodore say` to start one conversationally."
                )
            segment_ids = edit_list.segment_ids
            version_label = edit_list.version
            loaded = _require_build_subjects(project_dir, reg, segment_ids, subject, transcript, analysis)
            base_trims = _merged_base_trims(project_dir, loaded)
            trims = edits.apply_overrides_to_trims(edit_list, base_trims)

    plan, source_kwargs = _plan_for_sequence(
        loaded, segment_ids, subject=subject, transcript=transcript,
        analysis=analysis, trims=trims, handle_frames=handles,
    )

    if dry_run:
        click.echo(assembly_builder.describe_build(
            plan, project=project, subject=subject, mode=version_label,
            rationale=rationale, handle_frames=handles, **source_kwargs,
        ))
        return

    handles_ = _connect_or_die()
    multicam = assembly_builder.merge_multicam(
        assembly_builder.load_multicam(registry.subject_dir(project_dir, sid) / "multicam.json")
        for sid in loaded
    )
    result = assembly_builder.build_timeline(
        handles_, plan,
        project=project, subject=subject, mode=version_label,
        trims=trims, rationale=rationale, multicam=multicam, **source_kwargs,
    )
    if cost_tracker.calls:
        click.echo(f"Estimated cost this run:\n{cost_tracker.summary()}\n")
    click.echo(assembly_builder.format_result(result))


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True)
@click.option("--handles", default=config.DEFAULT_HANDLE_FRAMES, type=int)
@click.option("--dry-run", is_flag=True)
def untrim(project: str, subject: str, handles: int, dry_run: bool):
    """Rebuild the current edit list's assembly at full (untrimmed) length.

    Follows the edit list wherever it goes: a version spanning subjects is
    rebuilt across them, at full length, exactly as `theodore build` would.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)
    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    edit_list = edits.load_current(project_dir, reg)
    order = edit_list.segment_ids if edit_list else [s["id"] for s in analysis["segments"]]

    # An empty trims dict IS the un-trimming: plan.py falls back to each
    # segment's full clean range whenever it has no trims entry, which is
    # what build_untrimmed_plan does for one subject and what passing {}
    # through here does for any number of them.
    loaded = _require_build_subjects(project_dir, reg, order, subject, transcript, analysis)
    plan, source_kwargs = _plan_for_sequence(
        loaded, order, subject=subject, transcript=transcript,
        analysis=analysis, trims={}, handle_frames=handles,
    )

    if dry_run:
        click.echo(assembly_builder.describe_build(
            plan, project=project, subject=subject, mode="untrimmed",
            handle_frames=handles, **source_kwargs,
        ))
        return

    handles_ = _connect_or_die()
    result = assembly_builder.build_timeline(
        handles_, plan, project=project, subject=subject, mode="untrimmed",
        **source_kwargs,
    )
    click.echo(assembly_builder.format_result(result))


def _format_versions(project_dir: Path, reg: dict) -> str:
    current = reg.get("current_edit_version")
    version_names = edits.list_versions(project_dir)
    if not version_names:
        return "No edit list versions yet -- run `theodore build --mode <mode>` to seed one."
    lines = []
    for name in version_names:
        el = edits.load(project_dir, name)
        marker = "  <- current" if name == current else ""
        lines.append(f"{name}  (parent: {el.parent or '-'}, {len(el.sequence)} clips){marker}")
        for c in el.command_log:
            lines.append(f"    - {c}")
    return "\n".join(lines)


@cli.command()
@click.option("--project", required=True)
def versions(project: str):
    """List every edit list version, its parent, and its command log."""
    project_dir, reg = _load_registry(project)
    click.echo(_format_versions(project_dir, reg))


@cli.command()
@click.argument("version")
@click.option("--project", required=True)
def revert(version: str, project: str):
    """Fork a NEW edit list version carrying VERSION's sequence -- never rewinds history."""
    project_dir, reg = _load_registry(project)
    try:
        reverted = edits.revert(project_dir, version)
    except edits.EditListError as exc:
        raise click.ClickException(str(exc)) from exc
    edits.set_current_version(reg, reverted.version)
    registry.save_project(project_dir, reg)
    click.echo(f"Reverted to {version} as new version {reverted.version} (now current). Run `theodore build` to rebuild it.")


@cli.command(name="diff")
@click.argument("version_a")
@click.argument("version_b")
@click.option("--project", required=True)
def diff_versions(version_a: str, version_b: str, project: str):
    """Show what changed between two edit list versions."""
    project_dir, reg = _load_registry(project)
    try:
        a = edits.load(project_dir, version_a)
        b = edits.load(project_dir, version_b)
    except edits.EditListError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(edits.format_diff(edits.diff(a, b)))


def _subject_of_segment(segment_id: str) -> str:
    # One definition of "which subject owns this id", shared with the
    # cross-subject planner that reads the same prefix.
    return assembly_plan.subject_of_segment(segment_id)


def _build_command_context(project_dir: Path, reg: dict):
    """Everything theodore.commands.say() needs that isn't the instruction
    text itself: every registered subject's segments (annotated with
    strength/themes), and three lazy, cross-subject lookups -- a segment id
    like "haylee.q03" is resolved to its subject by prefix, and each
    subject's transcript/analysis/trims are read at most once per call
    regardless of how many segments from that subject a command touches.
    """
    subjects_segments: dict = {}
    for subject_id in registry.list_subjects(reg):
        analysis_path = registry.subject_dir(project_dir, subject_id) / "analysis.json"
        if analysis_path.exists():
            analysis = json.loads(analysis_path.read_text())
            subjects_segments[subject_id] = commands.index_segments_with_metadata(analysis)

    cache: dict = {}

    def subject_data(subject_id: str):
        if subject_id not in cache:
            subj_dir = registry.subject_dir(project_dir, subject_id)
            transcript_path = subj_dir / "transcript.json"
            analysis_path = subj_dir / "analysis.json"
            cache[subject_id] = (
                json.loads(transcript_path.read_text()) if transcript_path.exists() else None,
                json.loads(analysis_path.read_text()) if analysis_path.exists() else None,
                assembly_trim.load_trims(subj_dir),
            )
        return cache[subject_id]

    def strength_of(segment_id: str) -> Optional[float]:
        _, analysis, _ = subject_data(_subject_of_segment(segment_id))
        if analysis is None:
            return None
        sel = next((s for s in analysis.get("selects", []) if s["segment_id"] == segment_id), None)
        return sel.get("strength") if sel else None

    def words_of(segment_id: str) -> list:
        transcript, analysis, trims = subject_data(_subject_of_segment(segment_id))
        if transcript is None or analysis is None:
            return []
        seg = next((s for s in analysis["segments"] if s["id"] == segment_id), None)
        if seg is None:
            return []
        sel = next((s for s in analysis.get("selects", []) if s["segment_id"] == segment_id), None)
        return assembly_trim.kept_words(seg, sel, transcript, trims.get(segment_id))

    def bounds_of(segment_id: str) -> tuple:
        transcript, analysis, trims = subject_data(_subject_of_segment(segment_id))
        trim = trims.get(segment_id)
        if trim:
            return trim["trimmed_start"], trim["trimmed_end"]
        seg = next((s for s in (analysis or {}).get("segments", []) if s["id"] == segment_id), None)
        sel = next((s for s in (analysis or {}).get("selects", []) if s["segment_id"] == segment_id), None) if analysis else None
        if seg is None or transcript is None:
            return 0.0, 0.0
        utterances_by_id = {u["id"]: u for u in transcript["utterances"]}
        start_u = utterances_by_id.get((sel or {}).get("clean_start_utterance") or seg["answer_start_utterance"])
        end_u = utterances_by_id.get((sel or {}).get("clean_end_utterance") or seg["answer_end_utterance"])
        return (start_u["start"] if start_u else 0.0, end_u["end"] if end_u else 0.0)

    return subjects_segments, strength_of, words_of, bounds_of


def _print_command_result(result) -> None:
    if result.status == "answered":
        click.echo(result.message)
    elif result.status == "ambiguous":
        click.echo(f"Ambiguous -- {result.message}")
        for candidate in result.candidates:
            click.echo(f"  - {candidate}")
    elif result.status == "error":
        click.echo(f"Could not apply that: {result.message}")
    else:
        click.echo(f'Queued: "{result.raw_text}"\n')
        click.echo("Resulting order:")
        for i, sid in enumerate(result.sequence, start=1):
            click.echo(f"  {i}. {sid}")
        click.echo("\nRun `theodore pending` to review, or `theodore build --subject <id>` to confirm and rebuild.")


@cli.command()
@click.argument("text")
@click.option("--project", required=True)
def say(text: str, project: str):
    """Turn one plain-language instruction into a queued edit-list change.

    Nothing is built or committed to a real edit list version until
    `theodore build` (without --mode) confirms the queue -- six commands
    are one rebuild, not six.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)

    subjects_segments, strength_of, words_of, bounds_of = _build_command_context(project_dir, reg)
    if not subjects_segments:
        raise click.ClickException(
            "No subject in this project has analysis yet -- run `theodore analyze` for at "
            "least one subject before `theodore say` has anything to work with."
        )

    cost_tracker = CostTracker()
    result = commands.say(
        project_dir, reg, text, subjects_segments,
        strength_of=strength_of, words_of=words_of, bounds_of=bounds_of,
        cost_tracker=cost_tracker,
    )
    _print_command_result(result)
    if cost_tracker.calls:
        click.echo(f"\n(cost: ${cost_tracker.total_cost_usd:.4f})")


def _format_pending(project_dir: Path) -> str:
    queue = edits.load_pending(project_dir)
    if queue is None:
        return 'Nothing queued. Use `say "..."` to start.'
    lines = [f"Based on: {queue.parent or '(no edit list yet)'}", "", "Commands:"]
    lines.extend(f"  - {c}" for c in queue.command_log)
    lines.append("\nResulting order:")
    lines.extend(f"  {i}. {sid}" for i, sid in enumerate(queue.segment_ids, start=1))
    lines.append("\nRun `theodore build --subject <id>` to confirm and rebuild, or keep `say`ing to add more.")
    return "\n".join(lines)


@cli.command()
@click.option("--project", required=True)
def pending(project: str):
    """Show the queued commands and the order they would produce."""
    project_dir, _ = _load_registry(project)
    click.echo(_format_pending(project_dir))


_CHAT_HELP = """\
Type a plain-language instruction to queue it, same as `theodore say`.

Meta-commands:
  pending                        show the queued commands + resulting order
  versions                       list edit list versions
  build <subject> [--mode M]     confirm the queue (or seed one with --mode)
                                  and build a real Resolve timeline
  help                           this message
  exit / quit / Ctrl-D           leave
"""


@cli.command()
@click.option("--project", required=True)
def chat(project: str):
    """Interactive REPL: hold the registry in context and issue instructions
    one after another without re-invoking the CLI (and re-reading every
    subject's transcript/analysis off disk) for each one.
    """
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)

    subjects_segments, strength_of, words_of, bounds_of = _build_command_context(project_dir, reg)
    if not subjects_segments:
        raise click.ClickException(
            "No subject in this project has analysis yet -- run `theodore analyze` for at "
            "least one subject before `theodore chat` has anything to work with."
        )

    # Lazy, not created up front: `pending`/`versions`/`build`/`help` never
    # touch Claude, and a REPL that hard-fails at startup over a missing key
    # would block them along with everything else. Deferred to the first
    # actual instruction, and caught there rather than left to crash the
    # whole process with a raw traceback.
    client = None
    cost_tracker = CostTracker()

    click.echo(f"Theodore chat -- project '{project}', {len(subjects_segments)} subject(s) loaded.")
    click.echo("Type an instruction, or `help` for meta-commands. Ctrl-D to exit.\n")

    while True:
        try:
            line = click.prompt("theodore", prompt_suffix="> ")
        except (EOFError, click.exceptions.Abort):
            click.echo()
            break

        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            click.echo(_CHAT_HELP)
            continue
        if line == "pending":
            click.echo(_format_pending(project_dir))
            continue
        if line == "versions":
            click.echo(_format_versions(project_dir, reg))
            continue

        if line.startswith("build"):
            parts = line.split()
            if len(parts) < 2:
                click.echo("Usage: build <subject> [--mode chronological|strength|thematic|narrative]")
                continue
            build_subject = parts[1]
            build_mode = parts[parts.index("--mode") + 1] if "--mode" in parts else None
            ctx = click.Context(build)
            try:
                ctx.invoke(
                    build, project=project, subject=build_subject, mode=build_mode,
                    handles=config.DEFAULT_HANDLE_FRAMES, silence_threshold=config.DEFAULT_SILENCE_THRESHOLD_SECONDS,
                    aggressive_trim=False, apply_trim=True, exclude=None,
                    model_tier=config.DEFAULT_MODEL_TIER, dry_run=False,
                )
            except click.ClickException as exc:
                click.echo(f"Error: {exc.message}")
            reg = registry.load_project(project_dir, project_name=project)  # build() may have advanced current_edit_version
            continue

        if client is None:
            try:
                client = anthropic.Anthropic(api_key=config.require_anthropic_key())
            except RuntimeError as exc:
                click.echo(f"Error: {exc}")
                continue

        result = commands.say(
            project_dir, reg, line, subjects_segments,
            strength_of=strength_of, words_of=words_of, bounds_of=bounds_of,
            cost_tracker=cost_tracker, client=client,
        )
        _print_command_result(result)

    if cost_tracker.calls:
        click.echo(f"\nSession cost: ${cost_tracker.total_cost_usd:.4f}")
    click.echo("Goodbye.")


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", default=None, help="Show detail for one subject; omit to list every registered subject.")
def status(project: str, subject: Optional[str]):
    """Show which stages are complete/cached, per subject."""
    project_dir, reg = _load_registry(project)
    click.echo(f"Project: {project} ({project_dir})")
    click.echo(f"Addressing: {reg.get('addressing', 'sequential')}  ·  {len(reg.get('question_guide') or [])} guide questions\n")

    subjects = [subject] if subject else registry.list_subjects(reg)
    if not subjects:
        click.echo("No subjects registered yet -- run `theodore ingest --subject <name> ...` to add one.")
        return

    checks = [
        ("ingest", "media.json"),
        ("transcribe", "transcript.json"),
        ("analyze", "analysis.json"),
        ("notes", "interview_notes.md"),
    ]
    for subj in subjects:
        subj_dir = registry.subject_dir(project_dir, subj)
        display = reg["subjects"].get(subj, {}).get("display_name", subj)
        click.echo(f"{subj}  ({display})")
        for name, filename in checks:
            done = (subj_dir / filename).exists()
            click.echo(f"  {name:<12} {'done' if done else 'not started'}")
        click.echo()


def main():
    cli()


if __name__ == "__main__":
    main()
