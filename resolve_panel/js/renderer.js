'use strict';

// Runs in the renderer, sandboxed per preload.js: the only Theodore/Resolve
// surface available here is `window.theodore`, exposed via contextBridge.
// No requires, no filesystem, no child_process -- this file is plain DOM
// scripting, deliberately with no framework or build step.
//
// One shared scrolling console instead of a tab-per-command dashboard:
// every action (ingest, a quick command, a chat line) appends to the same
// #chat-log, the way a single conversation would -- not a maze of panels
// to hunt through. Timeline-building surfaces (multicam clip naming,
// versions/diff/revert, say/build) are deliberately not wired to a button
// right now, since building a new timeline isn't today's workflow; the
// CLI commands themselves are untouched and easy to re-surface later.

const state = { project: null, subject: null };

const el = (id) => document.getElementById(id);

// The single most likely first-run failure: no theodore-panel-config.json
// yet (see README -- it's gitignored on purpose, copied from the .example
// file and edited once per install). Every IPC call that touches
// theodore_bridge.js can throw that same ConfigError, so every entry point
// below is wrapped to show it here instead of leaving the panel silently
// blank with no explanation -- which is worse than an error message.
function showError(err) {
  const banner = el('error-banner');
  banner.textContent = String((err && err.message) || err);
  banner.classList.remove('hidden');
}

function clearError() {
  el('error-banner').classList.add('hidden');
}

async function safely(fn) {
  try {
    await fn();
    clearError();
  } catch (err) {
    showError(err);
  }
}

async function refreshProjects() {
  const projects = await window.theodore.listProjects();
  const select = el('project-select');
  select.innerHTML = '';
  for (const p of projects) {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = p;
    select.appendChild(opt);
  }
  state.project = projects[0] || null;
  if (state.project) select.value = state.project;
  await refreshSubjects();
}

async function refreshSubjects() {
  const select = el('subject-select');
  select.innerHTML = '';
  if (!state.project) {
    state.subject = null;
    return;
  }
  const subjects = await window.theodore.listSubjects(state.project);
  for (const s of subjects) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    select.appendChild(opt);
  }
  state.subject = subjects[0] || null;
  if (state.subject) select.value = state.subject;
}

// Every entry in this one shared console is its own labeled block, never a
// raw text blob -- "you", "Theodore", and a tool's own output are visually
// distinct, closer to reading back a real conversation than scrolling a
// terminal. `kind` picks the styling (see panel.css: msg-user/msg-theodore/
// msg-action/msg-system); action output is set via textContent throughout,
// so nothing here ever interprets a CLI's own output as markup.
function startBlock(kind, label) {
  const log = el('chat-log');
  const wrap = document.createElement('div');
  wrap.className = `msg msg-${kind}`;
  if (label) {
    const head = document.createElement('div');
    head.className = 'msg-label';
    head.textContent = label;
    wrap.appendChild(head);
  }
  const body = document.createElement('div');
  body.className = 'msg-body';
  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return body;
}

function appendToBody(body, text) {
  body.textContent += text;
  el('chat-log').scrollTop = el('chat-log').scrollHeight;
}

function addMessage(kind, label, text) {
  appendToBody(startBlock(kind, label), text);
}

function formatRunResult(result) {
  const lines = [];
  if (result.stdout) lines.push(result.stdout.trimEnd());
  if (result.stderr) lines.push(result.stderr.trimEnd());
  if (!result.ok) lines.push(`(exit code ${result.code})`);
  return lines.join('\n') || '(no output)';
}

function formatSegments(analysis) {
  if (!analysis) return 'No analysis.json yet -- run Ingest & Analyze first.';
  const selectsById = {};
  for (const s of analysis.selects || []) selectsById[s.segment_id] = s;
  const lines = [];
  for (const seg of analysis.segments || []) {
    const sel = selectsById[seg.id] || {};
    const strength = sel.strength != null ? sel.strength.toFixed(2) : '?';
    lines.push(`[${seg.id}] (${strength}) ${seg.question_text || '(volunteered)'}`);
    if (sel.best_line) lines.push(`    "${sel.best_line}"`);
    if (sel.issues && sel.issues.length) lines.push(`    issues: ${sel.issues.join(', ')}`);
  }
  return lines.join('\n') || '(no segments yet)';
}

// One-shot commands (Dupes/Gaps/Learn/Timeline Status): build the argv,
// run it, print the result into the shared console. Segments and Find
// have their own handlers below since they don't fit this exact shape
// (Segments reads a file directly; Find needs the query text).
async function runQuickAction(label, args) {
  const body = startBlock('action', label);
  try {
    const result = await window.theodore.run(args);
    appendToBody(body, formatRunResult(result));
    clearError();
    return result;
  } catch (err) {
    appendToBody(body, '(command did not run -- see error above)');
    showError(err);
    return null;
  }
}

function requireSubject(command) {
  if (!state.project || !state.subject) {
    showError('Pick a project and subject first.');
    return null;
  }
  return [command, '--project', state.project, '--subject', state.subject];
}

function initQuickActions() {
  // Delegated on #single-pane, not #quick-actions: the action buttons this
  // handles now live in several sibling sections (#quick-actions, #trim-row,
  // #cut-timeline-row), and .closest('.action-btn') already filters out
  // every other button on the page (ingest/chat controls aren't tagged
  // .action-btn), so binding higher is safe, not broader in effect.
  el('single-pane').addEventListener('click', async (e) => {
    const btn = e.target.closest('.action-btn');
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === 'segments') {
      if (!state.project || !state.subject) { showError('Pick a project and subject first.'); return; }
      const body = startBlock('action', 'Segments');
      try {
        const analysis = await window.theodore.readAnalysis(state.project, state.subject);
        appendToBody(body, formatSegments(analysis));
        clearError();
      } catch (err) {
        showError(err);
      }
      return;
    }

    if (action === 'find') {
      const query = el('find-input').value.trim();
      if (!state.project || !query) { showError('Pick a project and type something to find first.'); return; }
      await runQuickAction(`Find: ${query}`, ['find', query, '--project', state.project, '--subject', state.subject]);
      el('find-input').value = '';
      return;
    }

    if (action === 'trim-timeline') {
      const raw = el('trim-cuts-input').value.trim();
      if (!raw) { showError('Enter one or more cut ranges first, e.g. 86800:87000.'); return; }
      const args = ['trim-timeline'];
      for (const part of raw.split(',')) {
        const range = part.trim();
        if (range) args.push('--cut', range);
      }
      await runQuickAction('Trim Timeline', args);
      return;
    }

    if (action === 'caption-timeline') {
      const args = requireSubject(action);
      if (args === null) return;
      args.push('--import-to-resolve');
      await runQuickAction('Caption Timeline', args);
      return;
    }

    // Preview and Apply are two DIFFERENT buttons, not a checkbox, so
    // building the timeline can never happen without a preview run first
    // in the same session -- Apply starts disabled and only re-enables
    // right after a successful Preview (see the exclude-input listener
    // below, which disables it again the moment that input changes).
    if (action === 'cut-timeline-preview' || action === 'cut-timeline-apply') {
      const args = requireSubject('cut-timeline');
      if (args === null) return;
      const excludeRaw = el('cut-exclude-input').value.trim();
      if (excludeRaw) args.push('--exclude', excludeRaw);
      const applying = action === 'cut-timeline-apply';
      if (applying) args.push('--apply');
      const result = await runQuickAction(applying ? 'Cut Timeline: Apply' : 'Cut Timeline: Preview', args);
      el('cut-timeline-apply-btn').disabled = !(result && result.ok && !applying);
      return;
    }

    const args = requireSubject(action);
    if (args === null) return;
    const labels = {
      'timeline-status': 'Timeline Status', dupes: 'Dupes', gaps: 'Gaps', learn: 'Learn',
      'remove-silence': 'Remove Silence', delivery: 'Delivery',
    };
    await runQuickAction(labels[action] || action, args);
  });

  // Editing which segments to exclude invalidates whatever was last
  // previewed -- re-disable Apply so it can't build a cut that no longer
  // matches what's actually in the exclude field.
  el('cut-exclude-input').addEventListener('input', () => {
    el('cut-timeline-apply-btn').disabled = true;
  });
}

// Ingest: pick footage with a native dialog (never type a path), stream
// `theodore run`'s output live into the shared console so a multi-minute
// pipeline never looks frozen, then refresh the project/subject pickers
// so a newly-registered project shows up without a manual Refresh click.
function initIngestTab() {
  let sourcePath = null;
  let fromTimeline = false;

  el('pick-source-btn').addEventListener('click', async () => {
    try {
      const picked = await window.theodore.pickSource();
      if (picked) {
        sourcePath = picked;
        fromTimeline = false;
        el('source-path').textContent = sourcePath;
      }
      clearError();
    } catch (err) {
      showError(err);
    }
  });

  // Ingests straight from the currently-open Resolve timeline's audio
  // tracks -- no re-browsing to a file Resolve already knows the location
  // of. Mutually exclusive with "Choose footage...": whichever was clicked
  // last is what Ingest & Analyze uses.
  el('from-timeline-btn').addEventListener('click', () => {
    sourcePath = null;
    fromTimeline = true;
    el('source-path').textContent = '(from current Resolve timeline)';
    clearError();
  });

  el('run-ingest-btn').addEventListener('click', () => {
    const project = el('ingest-project').value.trim();
    const subject = el('ingest-subject').value.trim();

    if (!sourcePath && !fromTimeline) { showError('Choose footage, or click "Ingest from Timeline", first.'); return; }
    if (!project || !subject) { showError('Project and subject are both required.'); return; }

    const args = fromTimeline
      ? ['run', '--from-timeline', '--project', project, '--subject', subject]
      : ['run', sourcePath, '--project', project, '--subject', subject];

    clearError();
    const body = startBlock('action', `Ingest & Analyze: ${project} / ${subject}`);
    el('run-ingest-btn').disabled = true;

    window.theodore.runStreaming(
      args,
      (chunk) => appendToBody(body, chunk.text),
      async (result) => {
        el('run-ingest-btn').disabled = false;
        if (!result.ok) {
          appendToBody(body, `\n(exit code ${result.code}${result.error ? ': ' + result.error : ''})`);
        }
        // The run may have registered a brand-new project/subject --
        // reload the picker and land on exactly what was just built.
        await safely(refreshProjects);
        el('project-select').value = project;
        state.project = project;
        await safely(refreshSubjects);
        el('subject-select').value = subject;
        state.subject = subject;
      },
    );
  });
}

// Chat: a running `theodore chat --project X` conversation instead of
// one-shot `theodore say` calls, so instructions build on shared context
// (the REPL's meta-commands: pending/versions/build/help) without
// re-invoking the CLI per line. The child process doesn't echo what's
// typed (it reads over a pipe, not a tty), so the user's own line is
// appended locally before sending -- otherwise only Theodore's replies
// would ever appear.
let chatProject = null;
let currentReplyBody = null;
// Theodore's replies stream in as raw stdout chunks over one long-lived
// pipe, with no per-line framing -- there is no event marking "a reply
// finished", only "more text arrived". A new block can't just be opened
// the instant a line is sent: the PREVIOUS reply (e.g. the startup
// banner) may still be mid-flight, and switching blocks early would
// silently orphan its tail end into whatever block came next. Instead
// this flag defers opening the next block until the first chunk actually
// arrives after sending, so a still-streaming previous reply keeps
// landing where it started.
let awaitingNewReplyBlock = false;

// No block is pre-created for the session-start banner: it doesn't arrive
// until after the child process spawns, and creating one eagerly would
// plant it in the DOM ahead of the user's own message, which sends
// immediately afterward in the same synchronous call -- reading as a
// reply that showed up before the question that prompted it. Both the
// banner and every later reply instead go through the same lazy path:
// awaitingNewReplyBlock says a block is owed, and onChatOutput only
// creates it once the first byte actually arrives, so DOM order always
// matches chronological order.
function ensureChatSession() {
  if (!state.project || chatProject === state.project) return;
  chatProject = state.project;
  addMessage('system', null, `switched to project "${state.project}"`);
  window.theodore.startChat(state.project);
}

function initChat() {
  window.theodore.onChatOutput((chunk) => {
    if (!currentReplyBody || awaitingNewReplyBlock) {
      currentReplyBody = startBlock('theodore', 'Theodore');
      awaitingNewReplyBlock = false;
    }
    appendToBody(currentReplyBody, chunk.text);
  });
  window.theodore.onChatExit((info) => {
    addMessage('system', null, `chat session ended${info.error ? ': ' + info.error : ''}`);
    chatProject = null;
    currentReplyBody = null;
  });

  const send = () => {
    const input = el('chat-input');
    const line = input.value.trim();
    if (!line) return;
    if (!state.project) { showError('Pick a project first.'); return; }
    ensureChatSession();
    addMessage('user', 'You', line);
    awaitingNewReplyBlock = true;
    window.theodore.sendChat(line);
    input.value = '';
  };
  el('chat-send-btn').addEventListener('click', send);
  el('chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
}

async function init() {
  initIngestTab();
  initQuickActions();
  initChat();

  el('project-select').addEventListener('change', (e) => {
    state.project = e.target.value;
    safely(refreshSubjects);
  });
  el('subject-select').addEventListener('change', (e) => {
    state.subject = e.target.value;
  });
  el('refresh-btn').addEventListener('click', () => safely(refreshProjects));

  // Resolve exposes no "project changed" event at all (its Workflow
  // Integration SDK only has RenderStart/RenderStop/ResolveQuit) -- there is
  // no way for the panel to be TOLD when the editor opens or switches
  // projects. Polling on an interval is the only way to reflect that
  // without a manual Refresh click for every check; not live, but not
  // stale for more than a few seconds either. Independent of the project
  // list load below (which needs only the config file, not Resolve at all)
  // so one being unavailable never blocks the other.
  const RESOLVE_POLL_INTERVAL_MS = 5000;
  async function pollResolveStatus() {
    const status = el('resolve-status');
    try {
      const resolveProject = await window.theodore.currentResolveProjectName();
      el('resolve-project-name').textContent = resolveProject || '(not connected)';
      status.classList.toggle('connected', Boolean(resolveProject));
    } catch (err) {
      el('resolve-project-name').textContent = '(not connected)';
      status.classList.remove('connected');
    }
  }
  await pollResolveStatus();
  setInterval(pollResolveStatus, RESOLVE_POLL_INTERVAL_MS);

  await safely(refreshProjects);
}

init();
