# Theodore -- DaVinci Resolve panel

A DaVinci Resolve Studio **Workflow Integration Plugin**: an Electron app
that Resolve loads into its own UI (`Workspace -> Workflow Integrations ->
Theodore`), showing segments/selects, running `theodore dupes`/`gaps`, and
driving `theodore say`/`build` without alt-tabbing to a terminal.

## What's real here, and what isn't yet

This session had no DaVinci Resolve installed, so the Resolve-specific
half is unverified against the real app. But everything that COULD be run
in a sandboxed Linux container without Resolve was actually run, not just
written and hoped about:

**Actually verified, not just written:**
- `npm install` succeeds and pulls a real, working Electron binary.
- The app **actually launches** (verified under Xvfb with
  `--no-sandbox`) and **actually renders** -- a real screenshot of the
  live window shows the topbar, all 8 tabs, and a segments table
  correctly populated from real `analysis.json` fixture data, read
  through the real `contextBridge` -> `ipcMain` -> `theodore_bridge.js`
  path, no mocking.
- The missing-config failure mode (the single most likely first-run
  mistake: forgetting to copy/edit `theodore-panel-config.json`) was
  deliberately triggered and produces a clear red banner naming the exact
  fix, instead of the silent blank screen an earlier version of this
  panel actually had -- caught and fixed by running it for real, not by
  inspection.
- `theodore_bridge.js` has a full `node --test` suite (`npm test`, 18
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
problem: you'll see the real window, the real Segments/Dupes/Say tabs,
and (if something's wrong) the red error banner naming the fix, all
without Resolve in the loop. `resolve-project-name` will read
"(not connected)" here, always -- that's expected outside Resolve, not a
bug. Pick a real project/subject and confirm the Segments table shows
real data before moving on.

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

`theodore_bridge.js` does two things, and only two:
1. **Reads** Theodore's own JSON files directly off disk
   (`analysis.json`, `redundancy.json`, `edits/pending.json`, ...) -- fast,
   no subprocess, and it's already the source of truth (see the main
   project README's "Output, per project" section for the full file list).
2. **Runs** the `theodore` CLI as a child process for anything that's an
   action (`say`, `build`, `dupes`, `gaps`, ...), via
   `child_process.execFile`, with `THEODORE_DATA_DIR` set from the config
   so the CLI and the panel always agree on where the data lives.

Nothing here duplicates Theodore's own logic -- the panel is a thin
window onto the same CLI and the same JSON files, which is why it needed
no changes to the `theodore` Python package at all.

## Extending it

The tabs (Segments, Dupes, Gaps, Delivery, Find, Learn, Versions,
Say/Pending) cover every CLI command except `multicam` and the v1
ingest/transcribe/markers pipeline, which are one-time-per-subject setup
steps rather than things an editor reaches for repeatedly while cutting.
Every action tab follows the same shape: `bindRunButton()` in
`js/renderer.js` builds an argv array and hands it to
`window.theodore.run(...)`, which round-trips to `theodore_bridge.js`'s
`runTheodore()` -- no new bridge code needed for a new command, just a
button and an argv builder. Versions/Diff/Revert and Delivery/Peaks all
share one output pane per tab, since they're closely related actions on
the same underlying data; splitting that further is a UI call, not an
architectural one.
