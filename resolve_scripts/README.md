# Theodore -- DaVinci Resolve Scripts-menu entry points

A **Workspace -> Scripts** entry, not a Workflow Integration Plugin --
[`Remove Silence.py`](Remove%20Silence.py) runs as a plain Python script
Resolve itself invokes (`resolve` is provided as a pre-existing global; no
manual connection step, no separate Electron window, no click to open a
panel first). This is the same mechanism third-party tools like FireCut
use, and it's the closest any script gets to being a real, native Resolve
menu item.

It adds no new editing logic of its own -- it figures out the current
project, works out which Theodore subject is on the open timeline, and
runs `theodore remove-silence`, the exact same tested code path the CLI
and the Electron panel's own **Remove Silence** button use. The risky part
(actually cutting the timeline) already has full test coverage; this
script is thin glue on top of it, and has its own test coverage too
(`tests/test_resolve_scripts_remove_silence.py`, exercised via a fake
`resolve` global shaped the way Resolve's own scripting docs describe).

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
only ever want it on the Edit page. Restart Resolve, then run it from
**Workspace -> Scripts -> Theodore -> Remove Silence** with a project and
a timeline open.

## How subject discovery works

Theodore's own "project" is assumed to share its name with the currently
open Resolve project (override with `theodoreProject` in the config if
that's ever not true). There's no Resolve-side equivalent of "subject" to
read, so instead the script leans on `theodore remove-silence`'s own
`--subject`-optional behavior: it checks every registered subject's known
audio against what's actually placed on the open timeline, and uses
whichever one matches -- the same "run `theodore timeline-status` to
check the match" fallback exists if that's ever ambiguous.

## What's still unverified

Real DaVinci Resolve was not available in this sandbox. What's tested:
the dead-air detection, the timeline-frame mapping, the trim engine, and
this script's own glue code (config loading, project/subject discovery,
building the subprocess call, surfacing a failure cleanly) -- all against
fakes standing in for the real API. What's NOT verified: that Resolve
really does inject `resolve`/`fusion`/`bmd` the way its own docs describe,
and that it really does list a script placed here under Workspace ->
Scripts. Report back what happens the first time you run it for real.
