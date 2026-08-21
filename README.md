# Theodore

Theodore is an AI post-production assistant for interview and documentary
editing. It ingests interview footage, transcribes and diarizes it, uses
Claude to identify question/answer structure and story-worthy content, and
writes that intelligence directly into a DaVinci Resolve Studio timeline as
markers — plus exports human-readable notes.

This is v1. It's useful standalone (ingest → transcript → analysis →
markers → notes), and its module boundaries are built to grow into a full
rough-assembly and multicam-aware editor without a rewrite (see
[Future phases](#future-phases)).

## Requirements

- **DaVinci Resolve Studio.** The free edition of Resolve does not expose
  the external scripting API — Theodore cannot connect to it, and there is
  no free-edition fallback. If Resolve isn't running (or isn't reachable),
  Theodore falls back to writing an EDL marker file to disk instead of
  crashing or losing your analysis.
- Resolve must be **running with a project and timeline open** before you
  run `theodore markers`.
- Resolve's **Preferences → System → General → External scripting using**
  must be set to `Local` (or `Network`).
- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (ffmpeg + ffprobe on your `PATH`)
- An [Anthropic API key](https://console.anthropic.com/) and a
  [Deepgram API key](https://console.deepgram.com/)

## Setup

```bash
git clone <this repo>
cd theodore
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env and add ANTHROPIC_API_KEY / DEEPGRAM_API_KEY

./setup.sh   # configures RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH
             # for DaVinci Resolve Studio's scripting bridge, and appends
             # them to your shell profile. Open a NEW terminal afterward.
```

`setup.sh` detects macOS or Linux, verifies the Resolve scripting paths
actually exist (which also catches "you installed the free edition, not
Studio"), and appends `export` lines to your shell profile. On Windows, it
prints the equivalent PowerShell/environment-variable setup instead — set
those **without quotes**, or Resolve's scripting bridge fails to load.

## Worked example

```bash
# One command, full pipeline:
theodore run /path/to/interview.mov --project acme_doc

# Or stage by stage, so you can check each result before moving on:
theodore ingest /path/to/interview.mov --project acme_doc
theodore transcribe --project acme_doc --interviewer 1
theodore analyze --project acme_doc --model-tier standard
theodore markers --project acme_doc --dry-run   # preview first
theodore markers --project acme_doc             # write to the open Resolve timeline
theodore notes --project acme_doc

theodore status --project acme_doc
```

`transcribe` will interactively ask you to name each speaker Deepgram
detected, showing you their first ~30 words as a sample (e.g. `Speaker 0` →
`Marcus (subject)`). That mapping is saved to
`data/acme_doc/speakers.json` and reused on future runs. `--interviewer 1`
hints which speaker is asking questions, which meaningfully improves the
segmenter's accuracy.

Every stage caches its expensive work to `data/<project>/` — re-running
`theodore transcribe` on the same audio never re-bills Deepgram, and each
`analyze` sub-pass (segmenter/selects/themes) is independently re-runnable.

Output, per project:

```
data/<project>/
├── media.json              # ffprobe metadata (exact fps, duration, start TC)
├── audio/<hash>.wav         # extracted mono 16kHz audio
├── transcript_raw.json      # full raw Deepgram response
├── transcript_cache/         # cached raw responses, keyed by audio content hash
├── transcript.json          # normalized transcript (Theodore's schema)
├── speakers.json            # speaker id -> display name
├── segments.json            # Pass 3A: Q/A boundaries
├── selects.json             # Pass 3B: usability scoring + clean in/out
├── themes.json              # Pass 3C: controlled theme vocabulary + tags
├── analysis.json            # segments + selects + themes, combined
├── markers.edl               # EDL fallback, written only if Resolve wasn't reachable
├── interview_notes.md        # human-readable interview log
├── selects.csv               # machine-readable, sorted by strength
└── theodore.log              # structured log for the whole run
```

## CLI reference

```
theodore ingest <file|dir> --project <name>
theodore transcribe --project <name> [--interviewer <speaker_id>] [--force]
theodore analyze --project <name> [--model-tier economy|standard|premium] [--allow-cost-over]
theodore markers --project <name> [--dry-run] [--overwrite]
theodore notes --project <name>
theodore run <file> --project <name>      # full pipeline, one command
theodore status --project <name>          # which stages are complete/cached
```

`--model-tier` swaps the whole Claude model-routing table at once (see
`theodore/config.py`): `economy` uses Haiku everywhere, `standard` (default)
uses Haiku for the structural segmenter pass and Sonnet for the editorial
selects/themes passes, `premium` upgrades those to Opus. Every `analyze` run
prints a pre-flight cost estimate and a per-pass cost breakdown at the end;
`THEODORE_MAX_COST_USD` (default `20.0`) is a hard stop you can bypass with
`--allow-cost-over`.

## Architecture

```
theodore/
├── cli.py                  # entry point, command routing, progress output
├── config.py                # API keys, paths, model routing, cost guardrails
├── ingest/                  # ffprobe metadata + ffmpeg audio extraction
├── transcribe/               # Deepgram Nova-3 client + content-hash caching
├── analyze/                  # Claude passes: segmenter, selects, themes
│   └── prompts/               # every prompt as a versioned .md file
├── resolve/                  # timecode math, Resolve connection, marker writing
└── export/                   # notes.md / selects.csv / EDL fallback
```

**`analyze/` never imports from `resolve/`.** Analysis produces
platform-agnostic JSON; `resolve/` and `export/` consume it. That boundary
is what lets later phases target other NLEs, or build an actual cut
timeline from the same analysis, without touching the AI logic.

**`resolve/timecode.py` is the module everything else trusts.** It's the
single most likely source of silent bugs in a project like this — drop-frame
vs non-drop-frame, and 23.976 vs 24, will misplace every marker downstream
if handled sloppily. It uses `fractions.Fraction` throughout (never a
rounded float) and has unit tests covering 23.976, 24, 25, 29.97
drop-frame/non-drop-frame, 30, 59.94 drop-frame/non-drop-frame, and 60,
including the canonical "one hour of 29.97 drop-frame is frame 107892"
sanity check.

**Marker placement is deliberately split into two steps**
(`resolve/markers.py`): `plan_markers()` is pure and Resolve-independent —
it computes each marker's *absolute* frame position assuming the source
clip's embedded timecode is preserved on the timeline (the normal
documentary workflow). `write_markers()` is the only place that talks to
Resolve, and its entire job is subtracting `timeline.GetStartFrame()` to
get the relative frame id `AddMarker()` expects. Getting this backwards
silently offsets every marker by an hour on any timeline starting at
`01:00:00:00`; `tests/test_markers.py` verifies it against a fake timeline
object standing in for the Resolve API.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests cover `resolve/timecode.py` exhaustively (round-trips for every frame
rate above, plus the drop-frame "skipped label" edge cases), the Deepgram
response → `transcript.json` normalization layer, the Claude call wrapper
(JSON fence-stripping, repair-retry, chunk de-duplication) against a fake
Anthropic client, and marker writing against a fake Resolve timeline. None
of the tests require a Deepgram/Anthropic API key or a running Resolve
instance.

## Future phases

Theodore's architecture is built to grow into these without a rewrite —
`analyze/` stays platform-agnostic, and every marker's `customData` blob
carries enough of Theodore's own analysis (segment id, strength, clean
in/out frames, theme ids) for a later phase to read back off the timeline
instead of recomputing it:

- **Rough assembly** — build an actual sequence in Resolve from the clean
  in/out selects, ordered chronologically, thematically, or strongest-first.
- **Multicam angle intelligence** — frame-sampled vision scoring across
  multiple camera angles, auto-selecting the stronger angle per beat.
- **Cross-interview theme clustering** — multi-subject documentary
  structuring against a shared theme vocabulary.
- **Auto b-roll placement** — keyword/imagery matching from dialogue
  against the media pool.
- **Pacing pass** — filler-word and dead-air trimming proposals (this is
  why `filler_words: true` is already turned on in the Deepgram request).
- **Multi-length outputs** — 30s social / 3min short / full assembly from
  one source.
- **Style matching** — ingest finished prior edits as reference to align
  Theodore's pacing and selection with the editor's voice.
