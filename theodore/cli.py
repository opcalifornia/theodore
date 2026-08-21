"""Theodore CLI entry point."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import click

from theodore import config
from theodore.analyze.claude_client import CostTracker
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


@click.group()
def cli():
    """Theodore -- AI post-production assistant for interview/documentary editing."""


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--project", required=True)
def ingest(source: Path, project: str):
    """Ingest a video file, audio file, or directory of files."""
    project_dir = config.project_dir(project)
    _setup_logging(project_dir)

    files = _media_files(source)
    if not files:
        raise click.ClickException(f"No media files found at {source}")

    for f in files:
        with Stopwatch(f"Probing {f.name}"):
            info = ingest_media.probe(f)
        click.echo(f"   fps={info.fps}  duration={info.duration_seconds:.1f}s  start_tc={info.start_timecode}")

        with Stopwatch(f"Extracting audio from {f.name}"):
            wav_path = ingest_audio.extract_wav(f, project_dir / "audio")
        click.echo(f"   -> {wav_path}")

        info.audio_hash = wav_path.stem
        ingest_media.save_media_info(info, project_dir)

    click.echo(f"\nIngested {len(files)} file(s) into project '{project}'.")


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
@click.option("--interviewer", default=None, help="Speaker id to hint as the interviewer (e.g. '1').")
@click.option("--force", is_flag=True, help="Re-transcribe even if a cached transcript exists.")
def transcribe(project: str, interviewer: Optional[str], force: bool):
    """Transcribe ingested audio with Deepgram Nova-3 and name speakers."""
    project_dir = config.project_dir(project)
    _setup_logging(project_dir)

    media_list = ingest_media.load_media_info(project_dir)
    media_by_hash = {m["audio_hash"]: m for m in media_list if m.get("audio_hash")}
    wavs = sorted((project_dir / "audio").glob("*.wav"))
    if not wavs:
        raise click.ClickException("No extracted audio found -- run `theodore ingest` first.")
    if len(wavs) > 1:
        click.echo(
            f"NOTE: {len(wavs)} audio files ingested; v1 transcribes and analyzes the "
            "first one as the primary interview. Use a separate project per subject."
        )

    wav_path = wavs[0]
    media = media_by_hash.get(wav_path.stem) or (media_list[0] if media_list else None)
    if media is None:
        raise click.ClickException("No media metadata found -- run `theodore ingest` first.")

    with Stopwatch(f"Transcribing {wav_path.name} with Deepgram Nova-3"):
        raw = dg.transcribe_audio(wav_path, project_dir, force=force)
    transcript = dg.normalize(raw, media["path"], media["fps"], media["start_timecode"])

    speakers_path = project_dir / "speakers.json"
    if speakers_path.exists():
        names = json.loads(speakers_path.read_text())
    else:
        names = _prompt_speaker_names(transcript)
        speakers_path.write_text(json.dumps(names, indent=2))
    transcript = dg.apply_speaker_names(transcript, names)

    if interviewer:
        transcript["interviewer"] = interviewer

    dg.save_normalized(transcript, project_dir)
    click.echo(f"   {len(transcript['utterances'])} utterances, {len(transcript['speakers'])} speakers")


@cli.command()
@click.option("--project", required=True)
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--allow-cost-over", is_flag=True, help="Proceed even if the pre-flight estimate exceeds THEODORE_MAX_COST_USD.")
def analyze(project: str, model_tier: str, allow_cost_over: bool):
    """Run the segmenter, selects, and themes Claude passes."""
    project_dir = config.project_dir(project)
    _setup_logging(project_dir)

    transcript_path = project_dir / "transcript.json"
    if not transcript_path.exists():
        raise click.ClickException("No transcript found -- run `theodore transcribe` first.")
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

    with Stopwatch("Segmenting Q/A structure"):
        seg_result = run_segmenter(transcript, interviewer=interviewer, model_tier=model_tier, cost_tracker=cost_tracker)
    click.echo(f"   {len(seg_result['segments'])} segments")
    (project_dir / "segments.json").write_text(json.dumps(seg_result, indent=2))

    with Stopwatch("Scoring selects"):
        selects_result = run_selects(seg_result["segments"], transcript, model_tier=model_tier, cost_tracker=cost_tracker)
    (project_dir / "selects.json").write_text(json.dumps(selects_result, indent=2))

    with Stopwatch("Tagging themes"):
        themes_result = run_themes(seg_result["segments"], model_tier=model_tier, cost_tracker=cost_tracker)
    (project_dir / "themes.json").write_text(json.dumps(themes_result, indent=2))

    analysis = {
        "segments": seg_result["segments"],
        "utterances": transcript["utterances"],
        "selects": selects_result["selects"],
        "themes": themes_result["themes"],
        "theme_assignments": themes_result["assignments"],
    }
    (project_dir / "analysis.json").write_text(json.dumps(analysis, indent=2))

    click.echo(f"\nEstimated cost this run:\n{cost_tracker.summary()}")


def _load_transcript(project_dir: Path) -> dict:
    path = project_dir / "transcript.json"
    if not path.exists():
        raise click.ClickException("No transcript found -- run `theodore transcribe` first.")
    return json.loads(path.read_text())


def _load_analysis(project_dir: Path) -> dict:
    path = project_dir / "analysis.json"
    if not path.exists():
        raise click.ClickException("No analysis found -- run `theodore analyze` first.")
    return json.loads(path.read_text())


@cli.command()
@click.option("--project", required=True)
@click.option("--dry-run", is_flag=True, help="Print markers that would be written; touch nothing.")
@click.option("--overwrite", is_flag=True, help="Update markers Theodore previously wrote, instead of offsetting.")
def markers(project: str, dry_run: bool, overwrite: bool):
    """Write segment analysis onto the current Resolve timeline as markers."""
    project_dir = config.project_dir(project)
    _setup_logging(project_dir)

    transcript = _load_transcript(project_dir)
    analysis = _load_analysis(project_dir)

    plans = resolve_markers.plan_markers(transcript, analysis)
    click.echo(f"Planned {len(plans)} markers from analysis.")

    if dry_run:
        click.echo(resolve_markers.format_dry_run(plans, transcript["fps"]))
        return

    try:
        handles = resolve_connection.connect()
    except resolve_connection.ResolveConnectionError as exc:
        click.echo(f"Could not connect to Resolve: {exc}\n")
        edl_path = project_dir / "markers.edl"
        export_edl.write_edl(plans, transcript["fps"], project, edl_path)
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
def notes(project: str):
    """Export interview_notes.md and selects.csv."""
    project_dir = config.project_dir(project)
    _setup_logging(project_dir)

    transcript = _load_transcript(project_dir)
    analysis = _load_analysis(project_dir)

    md_path = export_notes.write_markdown(transcript, analysis, project_dir / "interview_notes.md")
    csv_path = export_notes.write_csv(transcript, analysis, project_dir / "selects.csv")

    _print_summary(analysis)
    click.echo(f"\nWrote {md_path} and {csv_path}")


@cli.command()
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--project", required=True)
@click.option("--interviewer", default=None)
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--allow-cost-over", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.option("--overwrite", is_flag=True)
@click.pass_context
def run(ctx, source, project, interviewer, model_tier, allow_cost_over, dry_run, overwrite):
    """Full pipeline: ingest -> transcribe -> analyze -> markers -> notes."""
    start = time.monotonic()
    ctx.invoke(ingest, source=source, project=project)
    ctx.invoke(transcribe, project=project, interviewer=interviewer, force=False)
    ctx.invoke(analyze, project=project, model_tier=model_tier, allow_cost_over=allow_cost_over)
    ctx.invoke(markers, project=project, dry_run=dry_run, overwrite=overwrite)
    ctx.invoke(notes, project=project)
    click.echo(f"\nFull pipeline finished in {time.monotonic() - start:.1f}s")


@cli.command()
@click.option("--project", required=True)
def status(project: str):
    """Show which stages are complete/cached for a project."""
    project_dir = config.project_dir(project)
    checks = [
        ("ingest", project_dir / "media.json"),
        ("transcribe", project_dir / "transcript.json"),
        ("analyze", project_dir / "analysis.json"),
        ("notes", project_dir / "interview_notes.md"),
    ]
    click.echo(f"Project: {project} ({project_dir})\n")
    for name, path in checks:
        click.echo(f"  {name:<12} {'done' if path.exists() else 'not started'}")


def main():
    cli()


if __name__ == "__main__":
    main()
