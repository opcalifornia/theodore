"""Theodore CLI entry point."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import click

from theodore import config, registry
from theodore.analyze.claude_client import CostTracker
from theodore.analyze.question_guide import apply_canonical_ids, match_to_guide
from theodore.analyze.segmenter import run_segmenter
from theodore.analyze.selects import run_selects
from theodore.analyze.themes import run_themes
from theodore.export import edl as export_edl
from theodore.export import notes as export_notes
from theodore.ingest import audio as ingest_audio
from theodore.ingest import media as ingest_media
from theodore.resolve import connection as resolve_connection
from theodore.resolve import markers as resolve_markers
from theodore.transcribe import deepgram as dg

logger = logging.getLogger("theodore.cli")

MODEL_TIER_CHOICE = click.Choice(["economy", "standard", "premium"])
ADDRESSING_CHOICE = click.Choice(["sequential", "canonical"])
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
    if source.is_file():
        return [source]
    return sorted(p for p in source.iterdir() if p.suffix.lower() in MEDIA_EXTS)


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

    for f in files:
        with Stopwatch(f"Probing {f.name}"):
            info = ingest_media.probe(f)
        click.echo(f"   fps={info.fps}  duration={info.duration_seconds:.1f}s  start_tc={info.start_timecode}")

        with Stopwatch(f"Extracting audio from {f.name}"):
            wav_path = ingest_audio.extract_wav(f, subj_dir / "audio")
        click.echo(f"   -> {wav_path}")

        info.audio_hash = wav_path.stem
        ingest_media.save_media_info(info, subj_dir)

    source_files = set(reg["subjects"][subject].get("source_files", []))
    source_files.update(f.name for f in files)
    reg["subjects"][subject]["source_files"] = sorted(source_files)
    registry.save_project(project_dir, reg)

    click.echo(f"\nIngested {len(files)} file(s) for subject '{subject}' in project '{project}'.")


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
    """Transcribe one subject's ingested audio with Deepgram Nova-3 and name speakers."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)

    media_list = ingest_media.load_media_info(subj_dir)
    media_by_hash = {m["audio_hash"]: m for m in media_list if m.get("audio_hash")}
    wavs = sorted((subj_dir / "audio").glob("*.wav"))
    if not wavs:
        raise click.ClickException(f"No extracted audio found for subject '{subject}' -- run `theodore ingest` first.")
    if len(wavs) > 1:
        click.echo(
            f"NOTE: {len(wavs)} audio files ingested for '{subject}'; v1 transcribes and "
            "analyzes the first one as the primary interview."
        )

    wav_path = wavs[0]
    media = media_by_hash.get(wav_path.stem) or (media_list[0] if media_list else None)
    if media is None:
        raise click.ClickException("No media metadata found -- run `theodore ingest` first.")

    with Stopwatch(f"Transcribing {wav_path.name} with Deepgram Nova-3"):
        raw = dg.transcribe_audio(wav_path, subj_dir, force=force)
    transcript = dg.normalize(raw, media["path"], media["fps"], media["start_timecode"])

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

    with Stopwatch("Scoring selects"):
        selects_result = run_selects(seg_result["segments"], transcript, model_tier=model_tier, cost_tracker=cost_tracker)
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
