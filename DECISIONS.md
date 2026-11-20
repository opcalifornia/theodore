# Decisions

Running log of judgement calls made while building, with the reasoning —
so a choice can be revisited later on its merits rather than rediscovered.

---

## 2026-10-28 — The Resolve Bridge

### Scope: the connection layer, not the full agent

**Decision.** Built the connection layer and nothing beyond it.

The request that came with this work was written for a different repository
(*"your Jarvis Cuts repo"*, with `resolve_bridge/` and `opcuts/` folders, a
Porcupine wake word, Whisper STT, ElevenLabs TTS and a web dashboard). None
of that exists here — this is Theodore: no `resolve_bridge/`, no `opcuts/`,
no voice loop, no dashboard, no MCP server. Nothing was invented to match
the description.

What the user asked for in their own words was narrower and clear: *"I just
wanna focus right now on the connection itself — you controlling DaVinci, me
being able to talk to Claude in DaVinci through its own window."* That is
what got built. The spec's **connection-layer** requirements were followed
closely (cross-platform path detection, version/Studio detection, health
check, reconnect, clear errors, and above all routing every API call through
one worker thread), and its quality requirements too (a mock Resolve for
unit tests, a live smoke test for real hardware).

Deferred, not forgotten: full API coverage as typed MCP tools, the
Claude operator loop with vision-based self-verification, per-project
memory, the voice and dashboard front doors, `opcuts` shorts rendering. All
of that sits *on top of* a working connection, so the connection had to be
right first.

### Prefer an in-Resolve bridge over external scripting

**Decision.** A script running inside Resolve holds the live `resolve`
object and re-exports it over loopback HTTP. Theodore talks to that.

Resolve's *external* scripting API (`DaVinciResolveScript.scriptapp()`) is
Studio-only, and since Resolve 19.1 the free edition refuses outside
connections outright. But a script launched from **Workspace → Scripts** is
handed a live `resolve` object on *any* edition, inside Resolve's own
process. Research confirmed this is the mechanism FireCut uses — their docs
say to connect from `Workspace → Scripts → FireCut` and call it essential,
and they advertise free-edition support, which is only possible this way.

Theodore's existing Scripts-menu entries were doing the worst of both: they
used the injected `resolve` only to read the project *name*, then shelled
out to the CLI, which re-entered through the gated external door — paying
every cost of external scripting (Studio-only, env vars, the
*External scripting using* preference, a fresh process handshake per click)
for none of its benefit.

### Keep external scripting as a fallback

**Decision.** `connect()` tries the bridge, then falls back.

The bridge needs one setup step inside Resolve per session. External
scripting needs none when it works. Keeping both costs little, and the
fallback is what makes this change safe to land: every existing workflow
keeps working on day one whether or not the bridge is installed.

A live bridge whose *call* fails is deliberately **not** a reason to fall
back — that is a real problem with this Resolve, and falling back would
hide it behind a misleading external-scripting error.

### One worker thread owns every Resolve API call

**Decision.** The HTTP server stays multi-threaded; actual API calls queue
onto a single dedicated thread and run strictly one at a time.

Resolve's API is not thread-safe, and the bridge is deliberately shared
(CLI, panel, future front doors). This is the single design choice that
makes sharing it safe at all. Health checks deliberately answer from state
captured at startup so they stay responsive *while* a long call is in
flight — which is exactly when you need to know what's going on.

Covered by `test_every_resolve_call_is_serialized_onto_one_thread`, which
hammers the bridge from six concurrent clients and asserts no two calls
overlap and all ran on the one worker thread.

### Encode dicts as pair lists, not JSON objects

**Decision.** Dicts cross the wire as `{"__map__": [[k, v], ...]}`.

JSON object keys are always strings, but Resolve returns dicts keyed by
**int** — `Timeline.GetMarkers()` is `{frame: {...}}` — and
`resolve/markers.py:188` does `rel_frame in existing` and
`existing[rel_frame]` with an int. Plain JSON would stringify every key,
every `in` test would quietly answer False, and marker de-duplication and
frame-offsetting would both silently stop working. No error, just wrong
markers. This is a correctness requirement, not a nicety.

### Probe optional methods on the server, don't assume

**Decision.** Each handle carries its server-side class name, and
`__getattr__` asks the server whether a method really exists, caching per
class.

Theodore probes optional Resolve methods with `getattr(obj, name, None)`
and treats `None` as "this build doesn't have it".
`assembly/builder.py:_read_track_items` leans on telling that apart from
"the call worked and the track is empty" when it verifies an append landed.
A proxy whose `__getattr__` returned a callable for any name would collapse
those two cases silently. The server runs the *identical* check, so the
semantics are correct by construction rather than by hope.

### The bridge script duplicates the codec instead of importing it

**Decision.** `resolve_scripts/Theodore Bridge.py` is one self-contained,
stdlib-only file. `tests/test_bridge_parity.py` fails the build if its copy
of the protocol drifts from `theodore/resolve/bridge_protocol.py`.

It runs in Resolve's embedded Python: a different interpreter version, none
of Theodore's dependencies, and the one environment that can't be debugged
remotely. Nothing to go wrong at import time is worth more there than DRY.
The parity test makes the duplication safe.

### Daemon threads and return immediately, with an opt-out

**Decision.** The listener runs on a daemon thread and `main()` returns at
once, so Resolve's script runner is released and its UI never blocks. A
`"blockMainThread": true` config flag holds the script open instead.

This rests on Resolve's embedded interpreter outliving the script, which
is **the one genuinely unverifiable assumption** in the design from a
sandbox with no Resolve in it. If a build tears the interpreter down, the
flag is the fallback, and `scripts/live_smoke_test.py` checks exactly this
("Bridge listener alive AFTER the menu script returned") and names the flag
in its failure output.

### Loopback bind, per-launch token, 0600 handshake

**Decision.** Bind `127.0.0.1` only; mint a fresh 32-byte token each launch;
require it on every request with a constant-time compare; create the
handshake file with mode 0600 from the outset; refuse underscore-prefixed
method names.

This listener can drive the user's entire Resolve. Treating it as a real
local privilege surface is cheap; not doing so would be negligent. Creating
the file 0600 rather than chmod-ing after means the token is never briefly
world-readable on a shared machine.

### TCP_NODELAY on both ends

**Decision.** Disable Nagle's algorithm client- and server-side.

Found by actually running the thing, not by reading it: **44.1 ms per call**
before, **0.5 ms after** — an 88x difference, and concurrent throughput 18x.
Nagle on these small JSON bodies meets the peer's delayed ACK and every call
pays ~40 ms of pure waiting. One timeline operation makes hundreds of calls,
so this was the difference between the bridge feeling instant and feeling
broken. `test_the_socket_has_nagle_disabled` asserts the socket option
rather than a wall-clock time, so it stays reliable in CI.

### `theodore install-bridge` rather than copy-paste instructions

**Decision.** A CLI command copies the script into Resolve's real Scripts
folder and then tells the user to fully quit Resolve.

This is the one setup step, and the one most likely to fail silently: the
folder is buried, differs per OS, and Resolve only scans it at launch — so a
file in the wrong place is indistinguishable from a file Resolve is
ignoring. That exact confusion is an open thread from an earlier session
("There's nothing in the scripts. I don't see Theodore."), so automating it
removes a real, already-observed failure.

### Track the bridge separately in the panel

**Decision.** The panel shows a `bridge on`/`bridge off` chip beside the
project name, read from the handshake file.

The panel reads the project through its own in-process *JavaScript* handle
(Resolve gives Workflow Integration plugins one), but every button shells
out to the CLI, which reaches Resolve through the *Python* bridge. So the
panel could legitimately show a connected project while every action failed
— a baffling thing to debug. Reading the handshake file is cheap enough to
poll with no subprocess and no socket.

### Stayed on the designated branch

**Decision.** Committed to `claude/theodore-v1-build-4neznf` rather than
opening a new branch, and did not open a PR.

The spec asked for a new branch and a PR, but this session's standing
instruction names that branch explicitly and says never to push elsewhere
without permission. The explicit instruction wins; the PR is a one-line ask
away.


---

## 2026-10-29 — The agent

### Hold the bridge script open (measured, not assumed)

On a real Resolve 21 Studio the listener answered, then refused connections
once the Scripts-menu script returned -- Resolve tears down the
interpreter's threads when a script ends. `main()` now serves in the
foreground by default (`blockMainThread: false` opts out). The first design
assumed the opposite and shipped it as an opt-in; the smoke test existed to
catch exactly this. Also fixed: the script keyed on `__name__ == "__main__"`,
which Resolve doesn't promise, so it could load and do nothing.

### Which two actions confirm

The brief said "only those two actions need my confirmation" without naming
them. Chosen: **deleting anything** (clips, timelines, markers, and any
escape-hatch method starting Delete/Remove) and **starting a render** (time,
disk, can overwrite files). Everything else is checkpointed instead of asked
about, because a checkpoint makes it undoable and a prompt makes it slow.

### Checkpoint = duplicate timeline; undo = switch to it

No history to replay, nothing to get wrong. Undo never deletes the edited
timeline. The agent can't delete a checkpoint. If a checkpoint can't be made,
the edit is refused -- never edited unprotected. One checkpoint per
instruction, not per edit.

### Verification is enforced in code

Telling a model to "verify" is a request. So: every edit's result carries a
`state_after` computed by code, and finishing after an edit without a
`export_frame` since is bounced back twice, then the run ends *flagged
unverified* instead of looping or lying.

### Escape hatch is split by method name

`call_resolve_api` treats Get/Is/Has/List as reads (no checkpoint) and
anything else as an edit (checkpoint + verify). A static "write" kind made
every harmless read take a checkpoint -- caught by a test.

### Scope, honestly

Built: the tool layer (28 tools), operator loop, checkpoint/undo, memory,
`theodore agent`. Not built from the brief: opcuts/shorts hand-off (that
folder isn't in this repo), voice and dashboard front doors, an MCP server for
Claude Desktop, scene-cut detection / Fairlight / Fusion / color-grade tools.
`call_resolve_api` reaches those methods meanwhile. The panel's chat box still
talks to the older `theodore chat`, not the agent.

### The panel's chat box is the agent now

The brief was "a Claude chat window inside DaVinci Resolve". The panel already
lived there (Workflow Integrations) and already had a chat box, wired to the
older `theodore chat` edit-list REPL. Rather than build a second window, the
box now runs `theodore agent-chat`. That REPL keeps the whole conversation
(old frames are dropped from carried history so requests don't bloat), gives
every instruction its own checkpoint and verification, and needs no Theodore
project -- it works on whatever Resolve has open; choosing a project only
unlocks the transcript tools. Confirmations travel over the same pipe as a
`[confirm]` line the panel turns into Yes/No buttons. The old `chat`
command is untouched. Cost: the old "move haylee.q03 after marcus.q04"
edit-list phrasing is no longer in the panel's box (it's still `theodore chat`).

### Make the chat feel like the Claude app

First real use failed: the editor's Resolve was still running the OLD panel
(it errored with the old `theodore chat` "no analysis yet" message). Resolve
loads the panel from its Workflow Integration Plugins folder -- a copy -- so
a `git pull` never reaches it. Added `theodore install-panel`, which refreshes
that copy in place and keeps the editor's config and node_modules.

Then the chat itself: replies stream word by word (`messages.stream`), are
formatted (a small escaped-first markdown renderer -- model text can never
inject markup), tool activity folds into a collapsible "Used N tools" line in
the order it happened, a typing indicator shows while Claude works, Enter
sends / Shift+Enter is a newline, and the session starts as soon as the panel
opens. The panel's other buttons moved into a collapsed "Tools" drawer so the
conversation is the window. The REPL speaks one JSON event per line to the
panel (`agent-chat --json`); plain text stays the terminal default.
