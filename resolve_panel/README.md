# Theodore -- DaVinci Resolve panel

A DaVinci Resolve Studio **Workflow Integration Plugin**: an Electron app
that Resolve loads into its own UI (`Workspace -> Workflow Integrations ->
Theodore`), showing segments/selects, running `theodore dupes`/`gaps`, and
driving `theodore say`/`build` without alt-tabbing to a terminal.

## What's real here, and what isn't yet

This was scaffolded in a sandboxed session with **no DaVinci Resolve
installed and no display** -- so it was built the same way the rest of
this project was built: validate everything that's actually testable, and
say plainly what isn't.

**Tested, for real:**
- `theodore_bridge.js` -- reading `project.json`/`analysis.json`/etc. off
  disk, and constructing the `theodore` CLI invocation -- has a full
  `node --test` suite (`npm test`) with no Electron or Resolve dependency.
  Run it yourself: `cd resolve_panel && npm test`.
- Every `.js` file parses cleanly (`node --check`) and `manifest.xml` is
  well-formed XML.

**Not testable from that sandboxed session, and still open:**
- The Electron shell has never actually been launched (no `npm install`,
  no display). `main.js`/`preload.js`/`index.html` follow Resolve's
  documented plugin shape and current (19.0.2+) sandboxing requirements
  (`contextIsolation: true`, a preload script, no `nodeIntegration`), but
  the first real test is you running `npm install && npm start` -- yet to
  add a `start` script once you've confirmed how Resolve actually launches
  it locally -- or dropping the folder into Resolve's plugin directory (see
  below) and opening it from the Workflow Integrations menu.
- **`WorkflowIntegration.node` is not included, and Theodore cannot
  generate it.** It's a native module Blackmagic ships as part of the
  Workflow Integration SDK / sample plugin. Get it from your own Resolve
  installation or Blackmagic's developer support downloads, and place it
  in this folder before `main.js`'s `require('./WorkflowIntegration.node')`
  will succeed. Without it, the panel still runs (see below) -- it just
  can't ask Resolve which project is open.
- No live "project changed" / "timeline changed" event exists in this SDK
  at all (only `RenderStart`, `RenderStop`, `ResolveQuit` callbacks) -- the
  panel is refresh-driven by design (the "Refresh" button, and after every
  `Say`/`Build`), not push-updated. That's not a bug to fix later; it's a
  real limitation of what Resolve exposes.
- Workflow Integration Plugins are **Windows and Mac OS X only** (per
  Blackmagic's own docs) -- there is no Linux path for this panel, even
  though the `theodore` CLI itself runs fine on Linux.

## Setup (once you're on a machine with Resolve Studio)

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

Then either:
- Drop this whole `resolve_panel/` folder (rename it something like
  `com.theodore.editor.panel` to match the convention other plugins use)
  into Resolve's Workflow Integration Plugins directory:
  - Mac: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/`
  - Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\`

  and launch it from Resolve's `Workspace -> Workflow Integrations` menu, or
- Run it as a plain desktop app for local UI development (`npx electron .`
  from this folder, once `electron` is installed) -- it will run with
  `resolveAPI` staying `null` the whole time (no `WorkflowIntegration.node`
  present outside Resolve's own plugin directory), which is the expected,
  handled case, not a crash.

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
