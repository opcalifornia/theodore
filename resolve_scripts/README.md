# Theodore -- DaVinci Resolve Scripts-menu entry points

A **Workspace -> Scripts** entry, not a Workflow Integration Plugin --
[`Remove Silence.py`](Remove%20Silence.py),
[`Caption Timeline.py`](Caption%20Timeline.py), and
[`Cut Timeline.py`](Cut%20Timeline.py) run as plain Python scripts Resolve
itself invokes (`resolve` is provided as a pre-existing global; no manual
connection step, no separate Electron window, no click to open a panel
first). This is the same mechanism third-party tools like FireCut use,
and it's the closest any script gets to being a real, native Resolve menu
item.

None of the three add any new editing logic of their own -- each figures
out the current project, works out which Theodore subject is on the open
timeline, and runs `theodore remove-silence` / `caption-timeline` /
`cut-timeline`, the exact same tested code paths the CLI and the
Electron panel's own buttons use. The risky part (actually cutting the
timeline, placing captions) already has full test coverage; these
scripts are thin glue on top of it, and have their own test coverage too
(`tests/test_resolve_scripts_remove_silence.py`,
`tests/test_resolve_scripts_caption_timeline.py`,
`tests/test_resolve_scripts_cut_timeline.py`, each exercised via a fake
`resolve` global shaped the way Resolve's own scripting docs describe).

`Cut Timeline.py` is the one genuine two-step flow of the three: since it
removes far more than dead air alone (interviewer questions, gaps,
excluded segments), it always runs a preview pass first and shows the
real numbers in a native macOS confirm dialog (Cancel / Apply Cut) --
only clicking Apply Cut runs the pass again with `--apply`, which is what
actually builds the trimmed duplicate. Cancel, or the dialog failing for
any reason, never applies anything.

## Setup

```bash
cd resolve_scripts
cp theodore-scripts-config.example.json theodore-scripts-config.json
# edit theodore-scripts-config.json: point theodoreExecutable at your
# theodore install's venv (e.g. /path/to/theodore/.venv/bin/theodore) and
# theodoreDataDir at the same data/ directory your CLI runs against.
```

`theodore-scripts-config.json` is gitignored, same reason as the panel's
own config: local paths, not something to commit.

Copy this whole `resolve_scripts/` folder (config included) into DaVinci
Resolve's Scripts directory, under a `Theodore` subfolder so it gets its
own menu group:

- Mac (all users): `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/`
- Mac (this user): `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Theodore/`
- Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\Theodore\`
- Linux: `/opt/resolve/Fusion/Scripts/Utility/Theodore/` (or the per-user equivalent)

`Utility` puts it on every page's Scripts menu; use `Edit` instead if you
only ever want it on the Edit page. Restart Resolve, then run any of them
from **Workspace -> Scripts -> Theodore -> Remove Silence** /
**Caption Timeline** / **Cut Timeline**, with a project and a timeline
open.

## If nothing shows up under Workspace -> Scripts

1. **Fully quit Resolve (Cmd+Q / Alt+F4), not just close the project.**
   Resolve only scans the Scripts folder at launch -- a project reload
   isn't enough, and this is the single most common cause.
2. Confirm the files actually landed where they should have:
   `ls -la` the `Theodore/` folder at whichever path above matches your
   OS, and check the timestamps are recent.
3. If a full restart with the files confirmed present still doesn't show
   them, try without the `Theodore` subfolder -- copy all three `.py`
   files (and the config) directly into `Utility/` itself, restart again,
   and look for them at **Workspace -> Scripts -> Remove Silence** (no
   submenu this time). Nested-folder submenus are generally supported,
   but this isolates whether your Resolve version is being fussy about it
   specifically.

## How subject discovery works

Theodore's own "project" is assumed to share its name with the currently
open Resolve project (override with `theodoreProject` in the config if
that's ever not true). There's no Resolve-side equivalent of "subject" to
read, so instead each script leans on the CLI's own `--subject`-optional
behavior: it checks every registered subject's known audio against
what's actually placed on the open timeline, and uses whichever one
matches -- the same "run `theodore timeline-status` to check the match"
fallback exists if that's ever ambiguous.

`Cut Timeline.py` deliberately does NOT expose `--exclude` (cutting one
specific extra segment, like a weak retake) -- that changes every run and
doesn't belong baked into a static config file. Use the Electron panel's
Cut Timeline row, or the CLI directly, when you need that; this script's
job is the automatic default (keep every detected answer).

## What's still unverified

Real DaVinci Resolve was not available in this sandbox. What's tested:
dead-air detection, the timeline-frame mapping, the trim engine, caption
placement, the clean-cut plan (keep vs. cut, dead air vs. gaps), and each
script's own glue code (config loading, project/subject discovery,
building the subprocess call, the preview-then-confirm flow, surfacing a
failure cleanly) -- all against fakes standing in for the real API, and
`Cut Timeline.py`'s confirm dialog against a stubbed `osascript`. What's
NOT verified: that Resolve really does inject `resolve`/`fusion`/`bmd`
the way its own docs describe, that it really does list a script placed
here under Workspace -> Scripts (see the troubleshooting section above --
this is the exact thing being tracked down as of this writing), and that
a real `osascript display dialog` behaves the way the stub assumes.
