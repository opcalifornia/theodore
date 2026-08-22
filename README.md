# Theodore

Theodore is an AI post-production assistant for interview and documentary
editing. It ingests interview footage, transcribes and diarizes it, uses
Claude to identify question/answer structure and story-worthy content, and
writes that intelligence directly into a DaVinci Resolve Studio timeline as
markers — plus exports human-readable notes.

This is v1, the v1.5 rough-assembly layer, and v2.0's conversational editor
through delivery analysis. **Currently built and tested:** ingest →
transcribe → analyze → markers → notes (v1); trim proposals, `review.html`,
assembly ordering strategies, real Resolve timeline construction, and
multicam angle detection (`theodore multicam`) (v1.5 — complete); the
multi-subject registry with immutable ids and canonical question-guide
matching, versioned
edit lists (`theodore build`/`versions`/`revert`/`diff`), delivery/prosody
analysis feeding a second axis into the Selects pass
(`theodore delivery`/`peaks`), redundancy detection across near-duplicate
answers (`theodore dupes`), cross-subject plain-language search
(`theodore find`), the rejects/gap views (`theodore rejects`/`gaps`),
duration targeting (`theodore build --target`), and the learning loop
comparing Selects' predictions against real editorial decisions
(`theodore learn`) (v2.0 Parts 1-4 steps 7-11). **Known limitation:**
`theodore build` can't yet assemble a sequence spanning more than one
subject (rejected with a clear error, not silently mis-built) — the "move
haylee.q03 after marcus.q04" cross-subject case needs `assembly/plan.py`
to work across multiple transcripts, which is follow-up work. **Not yet
built:** client export and voice control. Module boundaries are
deliberately kept clean so all of
that is additive, not a rewrite.

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
- `theodore delivery`/`peaks` need `numpy` and `praat-parselmouth` (installed
  with everything else via `pip install -e .`) — no separate Praat install
  or API key required, it's a pure-Python acoustic analysis library.

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

Every project is a **registry**: one `project.json`, and one or more
**subjects** (people interviewed), each addressed by an immutable id you
choose (e.g. `haylee`, `marcus`). Every command below is scoped to one
subject at a time — that's what makes multi-subject documentary projects
(interview five people, cut across all of them) work without the data from
one subject colliding with another's.

```bash
# One command, full pipeline, for one subject:
theodore run /path/to/haylee_interview.mov --project veterans_doc --subject haylee --display-name "Haylee Reyes"

# Or stage by stage, so you can check each result before moving on:
theodore ingest /path/to/haylee_interview.mov --project veterans_doc --subject haylee
theodore transcribe --project veterans_doc --subject haylee --interviewer 1
theodore analyze --project veterans_doc --subject haylee --model-tier standard
theodore markers --project veterans_doc --subject haylee --dry-run   # preview first
theodore markers --project veterans_doc --subject haylee             # write to the open Resolve timeline
theodore notes --project veterans_doc --subject haylee

theodore status --project veterans_doc             # every subject's stage status
theodore ids --project veterans_doc --subject haylee  # the id -> question mapping
```

Repeat `ingest`/`transcribe`/`analyze`/... with `--subject marcus` (etc.) to
add more people to the same project.

`transcribe` will interactively ask you to name each speaker Deepgram
detected, showing you their first ~30 words as a sample (e.g. `Speaker 0` →
`Haylee Reyes`). That mapping is saved to the subject's `speakers.json` and
`project.json`'s `speaker_map`, and reused on future runs. `--interviewer 1`
hints which speaker is asking questions, which meaningfully improves the
segmenter's accuracy.

Every stage caches its expensive work — re-running `theodore transcribe` on
the same audio never re-bills Deepgram, and each `analyze` sub-pass
(segmenter/selects/themes) is independently re-runnable. Segment ids are
**immutable**: re-running the segmenter matches new output against a
subject's existing `segments.json` (by exact utterance overlap, then a
Haiku similarity pass for anything that shifted) rather than renumbering
everything, so a saved reference to `haylee.q03` never silently starts
pointing at a different answer.

If a project defines a `question_guide` in `project.json` (a shared list of
canonical questions), `analyze` also runs a Haiku pass mapping each
subject's actual questions to guide ids (`--addressing canonical`, the
default once a guide exists) — so `q03` means the same *question* for every
subject, letting you eventually pull every subject's answer to the same
prompt and rank by strength. This is layered on top of each segment's
immutable sequential id, never a replacement for it.

`theodore dupes` finds segments that tell essentially the same story or
give essentially the same answer more than once — the subject circling
back unprompted, or being asked the same thing two different ways — so an
editor picks once instead of re-watching every take. It pools candidates
by shared theme tag (from `analyze`'s themes pass) rather than comparing
every segment against every other, so it stays cheap; the recommended take
within a group is just whichever member Selects already scored highest, no
second judgment call needed.

`theodore find "<query>"` searches segments in plain language across every
registered subject by default (or one with `--subject`) — segment ids
already carry their subject prefix, so a cross-subject result set (e.g.
"someone talking about losing a parent" matching both `haylee.q01` and
`marcus.q01`) stays unambiguous. It's a single Claude relevance pass over
question/answer summaries, not an embeddings index — per-project segment
counts are small enough that this is simpler and no less accurate.

`theodore rejects` and `theodore gaps` are pure re-reads of data `analyze`
already produced — no API calls, no new files. `rejects` surfaces
segments Selects scored too weak to use as-is (sorted weakest first, with
their issues and rationale), and `gaps` compares a subject's answered
`canonical_question_id`s against the project's `question_guide` to show
which guide questions that subject never actually answered — the
complement to Selects telling you what's good: what's weak, and what's
missing.

`theodore build --mode <mode> --target <MM:SS|seconds>` fits a fresh
assembly to a runtime budget by dropping the weakest-scoring segments
(Selects strength, unscored counting as 0.0) until it's under target,
keeping the relative order of everything that's kept, and reporting
exactly what it dropped and why. `--target` only applies to a fresh
`--mode` ordering pass, never to rebuilding a saved or pending edit list
version — a saved version is a recorded decision, and duration targeting
silently reshaping it would violate the same "never surgically mutate,
always derive a new version" rule the rest of the edit-list layer holds to.

`theodore multicam` reads the embedded timecode `ingest` already probed and
works out which of a subject's camera files were rolling on the same moment,
writing `multicam.json` and printing the exact clip name to use. Theodore
deliberately does not create the Multicam Clip itself — that stays a manual
step in Resolve (select the angles → right-click → "New Multicam Clip
Using…", Angle Sync: Timecode), named exactly what the command printed.
`theodore build` then finds it by that name and builds from it instead of the
plain source clip; if it isn't there, the build warns and falls back, so a
forgotten step is never a failed build. Grouping is conservative on purpose:
files with no embedded timecode (ffprobe reports `00:00:00:00`) are never
grouped, audio-only files are excluded, and a chain of partial overlaps is
reported as ambiguous rather than resolved by a guess.

`theodore learn` compares Selects' strength predictions against the
current edit list version's actual kept/dropped state, surfacing where
they diverge — a strong segment that isn't in the current cut, or a weak
one that is. It's deliberately observational: nothing here rewrites a
prompt or a score automatically. It's the safer, reversible half of a
feedback loop, and the half worth building first.

Output, per project:

```
data/<project>/
├── project.json               # registry: subjects, question guide, addressing mode
└── subjects/<subject_id>/
    ├── media.json              # ffprobe metadata (exact fps, duration, start TC)
    ├── audio/<hash>.wav         # extracted mono 16kHz audio
    ├── transcript_raw.json      # full raw Deepgram response
    ├── transcript_cache/         # cached raw responses, keyed by audio content hash
    ├── transcript.json          # normalized transcript (Theodore's schema)
    ├── speakers.json            # speaker id -> display name
    ├── segments.json            # Pass 3A: Q/A boundaries, immutable "<subject>.q<NN>" ids
    ├── selects.json             # Pass 3B: usability scoring + clean in/out
    ├── themes.json              # Pass 3C: controlled theme vocabulary + tags
    ├── analysis.json            # segments + selects + themes, combined
    ├── delivery.json            # v2.0 Part 3: pitch/energy/pause/onset-delay profiles (optional)
    ├── redundancy.json          # v2.0 Part 4: near-duplicate answer groups (optional)
    ├── multicam.json             # v1.5: detected camera-angle groups + proposed clip names (optional)
    ├── markers.edl               # EDL fallback, written only if Resolve wasn't reachable
    ├── interview_notes.md        # human-readable interview log
    └── selects.csv               # machine-readable, sorted by strength
```

A legacy (pre-registry) project — a flat `data/<project>/transcript.json`
et al. with no `project.json` — is migrated automatically and
**non-destructively** the first time any command touches it: the original
flat files are copied (never moved) into `subjects/<project-name>/`, so
nothing is lost if the migration needs to be redone.

`theodore.log` is written once per project (`data/<project>/theodore.log`),
interleaving every subject's activity chronologically.

## CLI reference

```
# v1 -- ingest through markers/notes
theodore ingest <file|dir> --project <name> --subject <id> [--display-name <name>]
theodore transcribe --project <name> --subject <id> [--interviewer <speaker_id>] [--force]
theodore analyze --project <name> --subject <id> [--model-tier economy|standard|premium] [--addressing sequential|canonical] [--allow-cost-over]
theodore markers --project <name> --subject <id> [--dry-run] [--overwrite]
theodore notes --project <name> --subject <id>
theodore ids --project <name> --subject <id>          # immutable id -> question mapping
theodore run <file> --project <name> --subject <id>    # full pipeline, one command
theodore status --project <name> [--subject <id>]      # which stages are complete/cached

# v1.5 -- rough assembly
theodore review --project <name> --subject <id> [--mode ...] [--exclude ids]   # review.html before building anything
theodore captions --project <name> --subject <id> [--srt] [--vtt] [--import-to-resolve]
theodore quotes --project <name> --subject <id>        # pull-quote sheet, sorted by strength
theodore multicam --project <name> --subject <id>      # group synced camera angles; names the multicam clip to create in Resolve

# v2.0 -- versioned edit lists + the conversational editor
theodore build --project <name> --subject <id> [--mode ...] [--target MM:SS] [--dry-run]   # seed/rebuild a real timeline
theodore untrim --project <name> --subject <id>        # rebuild the current edit list at full length
theodore versions / revert <version> / diff <v1> <v2> --project <name>
theodore say "move haylee.q03 after marcus.q04" --project <name>
theodore pending --project <name>                       # what's queued + resulting order
theodore chat --project <name>                          # interactive REPL over `say`

# v2.0 Part 3 -- delivery (prosody) analysis
theodore delivery --project <name> --subject <id>       # pitch/energy/pause/onset-delay profiles
theodore peaks --project <name> --subject <id>          # segments ranked by divergence from baseline

# v2.0 Part 4 -- redundancy detection, semantic search, rejects/gaps
theodore dupes --project <name> --subject <id> [--model-tier ...]      # near-duplicate answers, pick-one groups
theodore find "<query>" --project <name> [--subject <id>] [--limit N]  # plain-language search, one or all subjects
theodore rejects --project <name> --subject <id> [--threshold N]       # weak segments, sorted weakest first
theodore gaps --project <name> --subject <id>                          # question-guide entries never answered
theodore learn --project <name> --subject <id>                         # Selects' predictions vs. the current edit list
```

`--model-tier` swaps the whole Claude model-routing table at once (see
`theodore/config.py`): `economy` uses Haiku everywhere, `standard` (default)
uses Haiku for the structural segmenter pass and Sonnet for the editorial
selects/themes passes, `premium` upgrades those to Opus. Every `analyze` run
prints a pre-flight cost estimate and a per-pass cost breakdown at the end;
`THEODORE_MAX_COST_USD` (default `20.0`) is a hard stop you can bypass with
`--allow-cost-over`. Id-matching and question-guide-matching are always
Haiku, regardless of tier — they're similarity tasks, not editorial judgment.

## Architecture

```
theodore/
├── cli.py                  # entry point, command routing, progress output
├── config.py                # API keys, paths, model routing, cost guardrails
├── registry.py               # project.json: subjects, immutable-id counters, migration
├── ingest/                  # ffprobe metadata + ffmpeg audio extraction
├── transcribe/               # Deepgram Nova-3 client + content-hash caching
├── analyze/                  # Claude passes: segmenter, selects, themes, id-matching, question-guide
│   └── prompts/               # every prompt as a versioned .md file
├── resolve/                  # timecode math, Resolve connection, marker writing
├── assembly/                 # trim proposals, ordering strategies, cut-list frame math
└── export/                   # notes.md / selects.csv / review.html / EDL fallback
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
