# Theodore -- DaVinci Resolve panel

A DaVinci Resolve Studio **Workflow Integration Plugin**: an Electron app
that Resolve loads (`Workspace -> Workflow Integrations -> Theodore`),
covering the entire loop -- picking raw footage and running the full
ingest/transcribe/analyze pipeline, holding a live Claude-backed
conversation about the edit, showing segments/selects, running `theodore
dupes`/`gaps`, and driving `theodore say`/`build` -- with no terminal
involved at any point.

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
  `--no-sandbox`) and **actually renders** -- a real screenshot of the
  live window shows the topbar, all 10 tabs, and a segments table
  correctly populated from real `analysis.json` fixture data, read
  through the real `contextBridge` -> `ipcMain` -> `theodore_bridge.js`
  path, no mocking.
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
  at all (only `RenderStart`, `RenderStop`, `ResolveQuit` callbacks) -- the
  panel is refresh-driven by design (the "Refresh" button, and after every
  `Say`/`Build`), not push-updated.
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
problem: you'll see the real window, the real Ingest/Chat/Segments/Dupes/
Say tabs, and (if something's wrong) the red error banner naming the fix,
all without Resolve in the loop. `resolve-project-name` will read
"(not connected)" here, always -- that's expected outside Resolve, not a
bug. Try the Ingest tab on one real clip end-to-end (choose footage, type
a project/subject, Run Theodore, watch the console fill in live), confirm
the Segments tab shows the resulting analysis, then open the Chat tab and
type an instruction -- it's a live `theodore chat` process, so the same
Anthropic key from `theodore setup` is what it's using -- before moving on.

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

The Ingest tab covers `theodore run` (the full per-subject pipeline, via
a native footage picker so no path is ever typed); the Chat tab covers
`theodore chat` (a running conversation instead of one instruction at a
time); the rest of the tabs (Segments, Dupes, Gaps, Delivery, Find, Learn,
Versions, Say/Pending) cover every other CLI command an editor reaches
for repeatedly while cutting, except `multicam`. Every quick-action tab
follows the same shape: `bindRunButton()` in `js/renderer.js` builds an
argv array and hands it to `window.theodore.run(...)`, which round-trips
to `theodore_bridge.js`'s `runTheodore()` -- no new bridge code needed
for a new command, just a button and an argv builder. A long-running
one-shot command instead wants
`window.theodore.runStreaming(args, onOutput, onDone)`, modeled on the
Ingest tab's `initIngestTab()`; a REPL-shaped one wants
`startChat`/`sendChat`/`onChatOutput`, modeled on the Chat tab's
`initChatTab()`. Versions/Diff/Revert and Delivery/Peaks all share one
output pane per tab, since they're closely related actions on the same
underlying data; splitting that further is a UI call, not an
architectural one.
