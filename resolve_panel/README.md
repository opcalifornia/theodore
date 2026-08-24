# Theodore -- DaVinci Resolve panel

A DaVinci Resolve Studio **Workflow Integration Plugin**: an Electron app
that Resolve loads (`Workspace -> Workflow Integrations -> Theodore`),
covering the entire loop -- picking raw footage and running the full
ingest/transcribe/analyze pipeline, and holding a live Claude-backed
conversation about the edit -- with no terminal involved at any point.

One screen, not a tab-per-command dashboard: a footage picker (a native
file dialog, or **Ingest from Timeline** to skip picking altogether and
ingest straight off the currently-open Resolve timeline's audio tracks),
a row of one-click actions (Timeline Status, Remove Silence, Caption
Timeline, Segments, Delivery, Dupes, Gaps, Learn, Find), a Trim Timeline row
(removes frame range(s) from the open timeline and ripples everything
else together to close the gap -- the frame numbers come from Timeline
Status), a Cut Timeline row (keep only the detected answers, cut
interviewer questions/gaps/dead air and any segment id you name -- a
Preview button and a separate Apply button that only enables after a
successful preview, so building the cut can never happen unseen), and a
single scrolling console/chat below them that every action's output lands
in -- closer to talking to Claude directly than hunting
through a UI. Building a new timeline from scratch (`theodore build`,
versions/diff/revert) isn't wired to a button right now, since that isn't
the current workflow this panel is built around; the CLI commands
themselves are untouched underneath and easy to re-surface later.

**On "embedded panel" vs. a separate window:** Resolve's Workflow
Integration SDK gives a third-party plugin its own top-level Electron
window; there is no supported way for a plugin to render inline inside
Resolve's own single-window frame (Blackmagic's own bundled panels are
native, not part of this SDK). This window still lives entirely inside the
`Workspace -> Workflow Integrations` menu -- launched from Resolve, closed
with Resolve, no separately-installed app icon or Dock entry -- which is
as close to "inside DaVinci Resolve" as this SDK allows any plugin to get.

## What's real here, and what isn't yet

This session had no DaVinci Resolve installed, so the Resolve-specific
half is unverified against the real app. But everything that COULD be run
in a sandboxed Linux container without Resolve was actually run, not just
written and hoped about:

**Actually verified, not just written:**
- `npm install` succeeds and pulls a real, working Electron binary.
- The app **actually launches** (verified under Xvfb with
  `--no-sandbox`) and **actually renders** -- real screenshots of the
  live window show the single-pane layout (footage picker, quick-action
  row, shared console) working end to end: a Segments quick action
  correctly formats real `analysis.json` fixture data (read through the
  real `contextBridge` -> `ipcMain` -> `theodore_bridge.js` path, no
  mocking), a Dupes click runs the real `theodore` CLI and prints its
  real output (including a real, uncaught error when no Anthropic key is
  configured -- confirming failures surface honestly, not silently), and
  a chat line lands in the exact same scrolling console right below it.
- The missing-config failure mode (the single most likely first-run
  mistake: forgetting to copy/edit `theodore-panel-config.json`) was
  deliberately triggered and produces a clear red banner naming the exact
  fix, instead of the silent blank screen an earlier version of this
  panel actually had -- caught and fixed by running it for real, not by
  inspection.
- The Ingest tab's whole flow -- native file/folder picker, then
  `theodore run` streamed live into the console instead of buffered until
  exit -- was driven end-to-end against a stub `theodore` executable that
  prints on a delay: real screenshots taken mid-run show a partial console
  (proving output streams as it arrives, not just at the end), and a final
  screenshot shows the full output with the Run button re-enabled.
- The Chat tab was driven end-to-end against the **real** `theodore chat`
  process (not a stub) over a hand-built project fixture: the greeting
  banner, `pending`, `help`, and `versions` all round-tripped correctly
  through `startChat()`'s stdin/stdout pipes, with the transcript rendered
  live in a real screenshot. This is also what caught a real bug, not a
  panel-side one: `theodore chat` used to construct its Anthropic client
  (and hard-require an API key) before the REPL loop even started, so
  meta-commands that never touch Claude -- `pending`, `versions`, `build`
  -- were needlessly blocked by a missing key, and a bad key crashed the
  whole process with a raw traceback instead of a clean message. Fixed in
  `theodore/cli.py`'s `chat()` to construct the client lazily, on the
  first instruction that actually needs it.
- `theodore_bridge.js` has a full `node --test` suite (`npm test`, 27
  tests) with no Electron/Resolve dependency, and every `.js` file parses
  cleanly.
- The `<Id>`/`<Name>`/`<Version>`/`<Description>`/`<FilePath>`
  `manifest.xml` schema, the `WorkflowIntegration.Initialize()` /
  `GetResolve()` call pattern, and the Resolve 19.0.2+ sandboxing
  requirements (`contextIsolation`, a preload script, no
  `nodeIntegration`) were checked against Blackmagic's own documentation
  and a real published sample plugin's `manifest.xml`, fetched during this
  session -- not written from memory.

**Still genuinely unverified -- the two things to check first on your
machine:**
- **The plugin has never loaded inside actual Resolve.** Everything above
  proves the Electron app itself is sound; it does not prove Resolve's
  Workflow Integration host accepts this exact folder layout. This is the
  first real test once you're on Resolve Studio.
- **`WorkflowIntegration.node` is not included, and Theodore cannot
  generate it** -- it's a native module Blackmagic ships as part of
  Resolve Studio itself. In Resolve: **Help -> Documentation -> Developer**,
  then open the **Workflow Integrations** folder -- the SDK there includes
  a sample plugin with a working `WorkflowIntegration.node` for your
  platform. Copy that one file into this folder. Without it, the panel
  still runs (verified above) -- it just can't ask Resolve which project
  is open, and `resolve-project-name` stays "(not connected)".

**Real, permanent limitations (not bugs, not open questions):**
- No live "project changed" / "timeline changed" event exists in this SDK
  at all (only `RenderStart`, `RenderStop`, `ResolveQuit` callbacks), and
  that's true for every third-party plugin, not just this one -- there is
  no way to be *told* Resolve's state changed. The `resolve-project-name`
  indicator works around this the only way possible: it polls
  `currentResolveProjectName` every 5 seconds on its own, so it reflects
  the currently open project without a manual click, just not instantly.
  Everything else in the panel (Segments, `pending` in chat, etc.) is
  read on demand when its quick action is clicked or typed, not
  auto-refreshed in the background -- those read Theodore's own files,
  not Resolve's live state, so there is nothing to poll for in the first
  place.
- Workflow Integration Plugins are **Windows and Mac OS X only** (per
  Blackmagic's own docs) -- there is no Linux path for this panel, even
  though the `theodore` CLI itself runs fine on Linux.

## Setup

Do this in order -- each step is a real checkpoint, not busywork.

**1. Install and configure, before touching Resolve at all:**

```bash
cd resolve_panel
npm install
cp theodore-panel-config.example.json theodore-panel-config.json
# edit theodore-panel-config.json: point theodoreExecutable at your
# theodore install's venv (e.g. /path/to/theodore/.venv/bin/theodore) and
# theodoreDataDir at the same data/ directory your CLI runs against.
```

`theodore-panel-config.json` is gitignored on purpose -- it's local paths,
not something to commit. **Why a config file at all, rather than just
running `theodore` bare:** Resolve launches this plugin as its own
Electron process, which does **not** inherit your shell's `PATH` or your
Python venv's activation -- `child_process.execFile('theodore', ...)`
would fail to find it. Pointing straight at the venv's `theodore`
executable sidesteps that.

**2. Test the shell standalone, before Resolve is involved at all:**

```bash
npm start
```

This is the fastest way to catch a config typo or a `theodore` path
problem: you'll see the real window and (if something's wrong) the red
error banner naming the fix, all without Resolve in the loop.
`resolve-project-name` will read "(not connected)" here, always -- that's
expected outside Resolve, not a bug. Try footage on one real clip end to
end (choose it, type a project/subject, **Ingest & Analyze**, watch the
console fill in live), click **Segments** to confirm the resulting
analysis shows up, then type an instruction into the chat box at the
bottom -- it's a live `theodore chat` process, so the same Anthropic key
from `theodore setup` is what it's using -- before moving on.

**3. Get `WorkflowIntegration.node` from Resolve itself:**

In Resolve Studio: **Help -> Documentation -> Developer**, then open the
**Workflow Integrations** folder. It contains a sample plugin with a
working `WorkflowIntegration.node` for your OS -- copy that one file into
this `resolve_panel/` folder, next to `main.js`.

**4. Install it as a real Resolve panel:**

Drop this whole `resolve_panel/` folder (rename it something like
`com.theodore.editor.panel` to match the convention other plugins use,
`node_modules/` and all) into Resolve's Workflow Integration Plugins
directory:
- Mac: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/`
- Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\`

Restart Resolve, then open it from `Workspace -> Workflow Integrations ->
Theodore`. If it doesn't appear, re-check `manifest.xml`'s `<Id>` is
unique and the folder is directly inside the Plugins directory (not
nested another level deeper).

## How it's wired

```
index.html / js/renderer.js   <- plain DOM, no framework, no build step
        |  window.theodore.*  (contextBridge, preload.js)
        v
preload.js  ---ipcRenderer--->  main.js  ---ipcMain.handle--->  theodore_bridge.js
                                   |
                                   +-- WorkflowIntegration.node (optional;
                                       only used for "what Resolve project
                                       is currently open")
```

`theodore_bridge.js` does four things, and only four:
1. **Reads** Theodore's own JSON files directly off disk
   (`analysis.json`, `redundancy.json`, `edits/pending.json`, ...) -- fast,
   no subprocess, and it's already the source of truth (see the main
   project README's "Output, per project" section for the full file list).
2. **Runs** the `theodore` CLI as a child process for anything that's a
   quick action (`say`, `build`, `dupes`, `gaps`, ...), via
   `child_process.execFile` (`runTheodore()`), with `THEODORE_DATA_DIR`
   set from the config so the CLI and the panel always agree on where the
   data lives. This buffers all output and resolves once, which is fine
   for anything that finishes in a second or two.
3. **Streams** the one-shot commands that don't fit the buffered model:
   `theodore run` (the full ingest -> transcribe -> analyze -> markers ->
   notes pipeline) can take minutes, so `spawnTheodore()` uses
   `child_process.spawn` and calls back with each stdout/stderr chunk as
   it arrives via `main.js`'s `theodore:runStreaming` IPC channel, instead
   of leaving the panel showing nothing until the whole thing finishes --
   which would look identical to frozen.
4. **Holds open** the one long-lived, stateful command: `theodore chat`
   is a REPL, not a one-shot process, so `startChat()` spawns it once per
   Chat-tab session and keeps it alive across many lines -- each
   `send(line)` writes to its stdin, each reply streams back over the same
   `theodore:chat-output` channel `runStreaming` uses for output, until
   `stop()` (or the process exiting on its own) tears it down. One process
   per open chat, not one per line, is what lets it hold the project's
   registry and conversation state in memory the way an interactive
   session should, instead of re-reading every subject's transcript and
   analysis off disk for every single instruction.

Nothing here duplicates Theodore's own logic -- the panel is a thin
window onto the same CLI and the same JSON files, which is why it needed
no changes to the `theodore` Python package at all.

## Extending it

The footage row covers `theodore run` (the full per-subject pipeline, via
a native picker so no path is ever typed, streamed live into the shared
console); the chat box at the bottom covers `theodore chat` (a running
conversation instead of one instruction at a time). Everything in between
-- Timeline Status, Segments, Delivery, Dupes, Gaps, Learn, Find -- is a
**quick action**: a button in `#quick-actions` that builds an argv array
and hands it to `window.theodore.run(...)` via `runQuickAction()` in
`js/renderer.js`, printing the result into the same `#chat-log` every
other action writes to. Adding one more CLI command as a quick action is
just a new `<button class="action-btn" data-action="...">` plus, if its
name doesn't match its CLI subcommand 1:1, one line in `initQuickActions`'s
`labels` map -- no new bridge code needed. A long-running command instead
wants `window.theodore.runStreaming(args, onOutput, onDone)`, modeled on
`initIngestTab()`; a REPL-shaped one wants
`startChat`/`sendChat`/`onChatOutput`, modeled on `initChat()`.

Delivery and Dupes are meant to be run in that order: Delivery extracts
each segment's measured prosody (pitch, energy, pace, pauses, onset delay)
against the subject's own speaking baseline and writes `delivery.json`;
Dupes then reads it (if present) and prints the actual facts behind each
recommended take alongside the strength score, not just which segment id
won. Running Dupes first still works -- it falls back to transcript
strength alone and prints a one-line hint to run Delivery for the fuller
picture.

Timeline-building surfaces (`theodore build`, `multicam`, versions/diff/
revert, `peaks`) are deliberately not wired to a button right now --
building a new timeline from scratch isn't today's workflow, which is
centered on analyzing and eventually trimming an editor's own already-
synced timeline in place instead. The CLI commands themselves are
untouched; re-adding a quick action for any of them is the same one-line
change described above whenever that becomes the workflow again.
