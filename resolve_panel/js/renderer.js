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

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Every entry in this one shared console is appended, never replaces --
// the whole point is a running record, like scrolling back through a chat.
function appendConsole(text) {
  const log = el('chat-log');
  log.textContent += text;
  log.scrollTop = log.scrollHeight;
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
  appendConsole(`\n> [${label}]\n`);
  try {
    const result = await window.theodore.run(args);
    appendConsole(formatRunResult(result) + '\n');
    clearError();
  } catch (err) {
    appendConsole('(command did not run -- see error above)\n');
    showError(err);
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
  el('quick-actions').addEventListener('click', async (e) => {
    const btn = e.target.closest('.action-btn');
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === 'segments') {
      if (!state.project || !state.subject) { showError('Pick a project and subject first.'); return; }
      appendConsole('\n> [Segments]\n');
      try {
        const analysis = await window.theodore.readAnalysis(state.project, state.subject);
        appendConsole(formatSegments(analysis) + '\n');
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

    const args = requireSubject(action);
    if (args === null) return;
    const labels = { 'timeline-status': 'Timeline Status', dupes: 'Dupes', gaps: 'Gaps', learn: 'Learn' };
    await runQuickAction(labels[action] || action, args);
  });
}

// Ingest: pick footage with a native dialog (never type a path), stream
// `theodore run`'s output live into the shared console so a multi-minute
// pipeline never looks frozen, then refresh the project/subject pickers
// so a newly-registered project shows up without a manual Refresh click.
function initIngestTab() {
  let sourcePath = null;

  el('pick-source-btn').addEventListener('click', async () => {
    try {
      const picked = await window.theodore.pickSource();
      if (picked) {
        sourcePath = picked;
        el('source-path').textContent = sourcePath;
      }
      clearError();
    } catch (err) {
      showError(err);
    }
  });

  el('run-ingest-btn').addEventListener('click', () => {
    const project = el('ingest-project').value.trim();
    const subject = el('ingest-subject').value.trim();

    if (!sourcePath) { showError('Choose footage first.'); return; }
    if (!project || !subject) { showError('Project and subject are both required.'); return; }

    const args = ['run', sourcePath, '--project', project, '--subject', subject];

    clearError();
    appendConsole(`\n> [Ingest & Analyze: ${project} / ${subject}]\n`);
    el('run-ingest-btn').disabled = true;

    window.theodore.runStreaming(
      args,
      (chunk) => appendConsole(chunk.text),
      async (result) => {
        el('run-ingest-btn').disabled = false;
        if (!result.ok) {
          appendConsole(`\n(exit code ${result.code}${result.error ? ': ' + result.error : ''})\n`);
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

function ensureChatSession() {
  if (!state.project || chatProject === state.project) return;
  chatProject = state.project;
  appendConsole(`\n----- switching chat to project "${state.project}" -----\n`);
  window.theodore.startChat(state.project);
}

function initChat() {
  window.theodore.onChatOutput((chunk) => appendConsole(chunk.text));
  window.theodore.onChatExit((info) => {
    appendConsole(`\n(chat session ended${info.error ? ': ' + info.error : ''})\n`);
    chatProject = null;
  });

  const send = () => {
    const input = el('chat-input');
    const line = input.value.trim();
    if (!line) return;
    if (!state.project) { showError('Pick a project first.'); return; }
    ensureChatSession();
    appendConsole(`\n> ${line}\n`);
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
    try {
      const resolveProject = await window.theodore.currentResolveProjectName();
      el('resolve-project-name').textContent = resolveProject || '(not connected)';
    } catch (err) {
      el('resolve-project-name').textContent = '(not connected)';
    }
  }
  await pollResolveStatus();
  setInterval(pollResolveStatus, RESOLVE_POLL_INTERVAL_MS);

  await safely(refreshProjects);
}

init();
