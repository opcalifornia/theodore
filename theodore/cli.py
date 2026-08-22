"""Theodore CLI entry point."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import click

from theodore import config, edits, registry
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


@cli.command()
@click.option("--project", required=True)
@click.option("--subject", required=True, help="Whose segments this build covers. Cross-subject edit lists aren't supported yet.")
@click.option("--mode", type=MODE_CHOICE, default=None,
              help="Run a fresh ordering pass and seed/derive a new edit version from it. Omit to rebuild the CURRENT version as-is.")
@click.option("--handles", default=config.DEFAULT_HANDLE_FRAMES, type=int)
@click.option("--silence-threshold", default=config.DEFAULT_SILENCE_THRESHOLD_SECONDS, type=float)
@click.option("--aggressive-trim", is_flag=True)
@click.option("--trim/--no-trim", "apply_trim", default=True)
@click.option("--exclude", default=None, help="Comma-separated segment ids to leave out (only meaningful with --mode).")
@click.option("--model-tier", type=MODEL_TIER_CHOICE, default=config.DEFAULT_MODEL_TIER)
@click.option("--dry-run", is_flag=True)
def build(project, subject, mode, handles, silence_threshold, aggressive_trim, apply_trim,
          exclude, model_tier, dry_run):
    """Build (or rebuild) a real Resolve timeline from the project's edit list.

    Never touches an existing timeline -- every build creates a new one. With
    --mode, runs a fresh ordering pass and records it as a new edit list
    version (v001 if none exists yet, otherwise a derived version on top of
    the current one). Without --mode, rebuilds the CURRENT version exactly
    as it stands -- this is what `theodore say` leads to after queued
    commands are confirmed.
    """
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
        # Rebuild the CURRENT persisted edit list exactly as it stands --
        # this is what `theodore say` leads to after commands are confirmed.
        edit_list = edits.load_current(project_dir, reg)
        if edit_list is None:
            raise click.ClickException(
                "No edit list yet for this project -- run `theodore build --mode <mode>` "
                "once to seed one, or `theodore say` to start one conversationally."
            )
        segment_ids = edit_list.segment_ids
        version_label = edit_list.version
        base_trims = assembly_trim.load_trims(subj_dir)
        trims = edits.apply_overrides_to_trims(edit_list, base_trims)

    foreign = [sid for sid in segment_ids if not sid.startswith(f"{subject}.")]
    if foreign:
        raise click.ClickException(
            f"This edit list references segment(s) belonging to another subject: "
            f"{', '.join(foreign[:5])}. Cross-subject builds aren't supported by "
            "`theodore build` yet -- for now, an edit list built with `theodore build` must "
            "stay within one subject's own segments."
        )

    plan = assembly_plan.build_plan(transcript, analysis, trims, segment_ids, handle_frames=handles)

    if dry_run:
        click.echo(assembly_builder.describe_build(
            plan, transcript=transcript, project=project, subject=subject, mode=version_label,
            analysis=analysis, rationale=rationale, handle_frames=handles,
        ))
        return

    handles_ = _connect_or_die()
    multicam = assembly_builder.load_multicam(subj_dir / "multicam.json")
    result = assembly_builder.build_timeline(
        handles_, plan, transcript=transcript, analysis=analysis,
        project=project, subject=subject, mode=version_label,
        trims=trims, rationale=rationale, multicam=multicam,
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
    """Rebuild the current edit list's assembly at full (untrimmed) length."""
    project_dir, reg = _load_registry(project)
    _setup_logging(project_dir)
    subj_dir = _require_subject_dir(project_dir, reg, subject)
    transcript = _load_transcript(subj_dir, subject)
    analysis = _load_analysis(subj_dir, subject)

    edit_list = edits.load_current(project_dir, reg)
    order = edit_list.segment_ids if edit_list else [s["id"] for s in analysis["segments"]]

    plan = assembly_builder.build_untrimmed_plan(transcript, analysis, order, handle_frames=handles)

    if dry_run:
        click.echo(assembly_builder.describe_build(
            plan, transcript=transcript, project=project, subject=subject, mode="untrimmed",
            analysis=analysis, handle_frames=handles,
        ))
        return

    handles_ = _connect_or_die()
    result = assembly_builder.build_timeline(
        handles_, plan, transcript=transcript, analysis=analysis,
        project=project, subject=subject, mode="untrimmed",
    )
    click.echo(assembly_builder.format_result(result))


@cli.command()
@click.option("--project", required=True)
def versions(project: str):
    """List every edit list version, its parent, and its command log."""
    project_dir, reg = _load_registry(project)
    current = reg.get("current_edit_version")
    version_names = edits.list_versions(project_dir)
    if not version_names:
        click.echo("No edit list versions yet -- run `theodore build --mode <mode>` to seed one.")
        return
    for name in version_names:
        el = edits.load(project_dir, name)
        marker = "  <- current" if name == current else ""
        click.echo(f"{name}  (parent: {el.parent or '-'}, {len(el.sequence)} clips){marker}")
        for c in el.command_log:
            click.echo(f"    - {c}")


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
