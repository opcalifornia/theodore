'use strict';

// Runs in the renderer, sandboxed per preload.js: the only Theodore/Resolve
// surface available here is `window.theodore`, exposed via contextBridge.
// No requires, no filesystem, no child_process -- this file is plain DOM
// scripting, deliberately with no framework or build step.

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
  await refreshAll();
}

async function refreshAll() {
  await Promise.all([refreshSegments(), refreshPending()]);
}

async function refreshSegments() {
  const tbody = document.querySelector('#segments-table tbody');
  tbody.innerHTML = '';
  if (!state.project || !state.subject) return;

  const analysis = await window.theodore.readAnalysis(state.project, state.subject);
  if (!analysis) {
    tbody.innerHTML = '<tr><td colspan="5">No analysis.json yet -- run `theodore analyze` first.</td></tr>';
    return;
  }

  const selectsById = {};
  for (const s of analysis.selects || []) selectsById[s.segment_id] = s;

  for (const seg of analysis.segments || []) {
    const sel = selectsById[seg.id] || {};
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHTML(seg.id)}</td>
      <td>${escapeHTML(seg.question_text || '(volunteered)')}</td>
      <td>${sel.strength != null ? sel.strength.toFixed(2) : ''}</td>
      <td>${escapeHTML(sel.best_line || '')}</td>
      <td>${escapeHTML((sel.issues || []).join(', '))}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function refreshPending() {
  const list = el('pending-list');
  list.innerHTML = '';
  if (!state.project) return;
  const pending = await window.theodore.readPending(state.project);
  if (!pending || !pending.sequence || pending.sequence.length === 0) {
    list.innerHTML = '<li>(nothing queued)</li>';
    return;
  }
  for (const entry of pending.sequence) {
    const li = document.createElement('li');
    li.textContent = entry.segment_id;
    list.appendChild(li);
  }
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function switchTab(name) {
  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.classList.toggle('active', btn.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll('.tab-panel')) {
    panel.classList.toggle('active', panel.id === `tab-${name}`);
  }
  if (name === 'chat') ensureChatSession();
}

function formatRunResult(result) {
  const lines = [];
  if (result.stdout) lines.push(result.stdout.trimEnd());
  if (result.stderr) lines.push(result.stderr.trimEnd());
  if (!result.ok) lines.push(`(exit code ${result.code})`);
  return lines.join('\n') || '(no output)';
}

// Every "click a button, run a theodore command, show the output" tab
// (Dupes, Gaps, Delivery, Learn, Versions) is the same three steps.
// `buildArgs` returns the argv array to run, or null to silently no-op
// (e.g. nothing selected yet, or the user cancelled a confirm()).
// `afterRun` is for the rare case something else needs a refresh
// afterward (revert changes the current edit version).
function bindRunButton(buttonId, outputId, buildArgs, afterRun) {
  el(buttonId).addEventListener('click', async () => {
    const args = buildArgs();
    if (args === null) return;
    const out = el(outputId);
    out.textContent = 'Running...';
    try {
      const result = await window.theodore.run(args);
      out.textContent = formatRunResult(result);
      clearError();
      if (afterRun) await afterRun();
    } catch (err) {
      // theodore.run() itself only throws before ever spawning a process
      // (a missing/broken config, or a programming error in the args
      // built above) -- an actual failed `theodore` command resolves with
      // ok:false instead and is already shown via formatRunResult.
      out.textContent = '(command did not run -- see error above)';
      showError(err);
    }
  });
}

// Ingest tab: pick footage with a native dialog (never type a path),
// stream `theodore run`'s output live so a multi-minute pipeline never
// looks frozen, then refresh the project/subject pickers so a
// newly-registered project shows up without a manual Refresh click.
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
    const out = el('ingest-output');

    if (!sourcePath) { showError('Choose footage first.'); return; }
    if (!project || !subject) { showError('Project and subject are both required.'); return; }

    const args = ['run', sourcePath, '--project', project, '--subject', subject];
    const displayName = el('ingest-display-name').value.trim();
    const interviewer = el('ingest-interviewer').value.trim();
    if (displayName) args.push('--display-name', displayName);
    if (interviewer) args.push('--interviewer', interviewer);

    clearError();
    out.textContent = '';
    el('run-ingest-btn').disabled = true;

    window.theodore.runStreaming(
      args,
      (chunk) => { out.textContent += chunk.text; out.scrollTop = out.scrollHeight; },
      async (result) => {
        el('run-ingest-btn').disabled = false;
        if (!result.ok) {
          out.textContent += `\n(exit code ${result.code}${result.error ? ': ' + result.error : ''})`;
        }
        // The run may have registered a brand-new project/subject --
        // reload the picker and land on exactly what was just built.
        await safely(refreshProjects);
        el('project-select').value = project;
        state.project = project;
        await safely(refreshSubjects);
        el('subject-select').value = subject;
        state.subject = subject;
        await safely(refreshAll);
      },
    );
  });
}

// Chat tab: a running `theodore chat --project X` conversation instead of
// one-shot `theodore say` calls, so instructions build on shared context
// (the REPL's meta-commands: pending/versions/build/help) without
// re-invoking the CLI per line. The child process doesn't echo what's
// typed (it reads over a pipe, not a tty), so the user's own line is
// appended locally before sending -- otherwise only Theodore's replies
// would ever appear.
let chatProject = null;

function appendChat(text) {
  const log = el('chat-log');
  log.textContent += text;
  log.scrollTop = log.scrollHeight;
}

function ensureChatSession() {
  if (!state.project || chatProject === state.project) return;
  chatProject = state.project;
  appendChat(`\n----- switching chat to project "${state.project}" -----\n`);
  window.theodore.startChat(state.project);
}

function initChatTab() {
  window.theodore.onChatOutput((chunk) => appendChat(chunk.text));
  window.theodore.onChatExit((info) => {
    appendChat(`\n(chat session ended${info.error ? ': ' + info.error : ''})\n`);
    chatProject = null;
  });

  const send = () => {
    const input = el('chat-input');
    const line = input.value.trim();
    if (!line) return;
    if (!state.project) { showError('Pick a project first.'); return; }
    ensureChatSession();
    appendChat(`> ${line}\n`);
    window.theodore.sendChat(line);
    input.value = '';
  };
  el('chat-send-btn').addEventListener('click', send);
  el('chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
}

async function init() {
  initIngestTab();
  initChatTab();

  el('project-select').addEventListener('change', (e) => {
    state.project = e.target.value;
    safely(refreshSubjects);
  });
  el('subject-select').addEventListener('change', (e) => {
    state.subject = e.target.value;
    safely(refreshAll);
  });
  el('refresh-btn').addEventListener('click', () => safely(refreshAll));

  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  }

  const requireSubject = (command) => () => {
    if (!state.project || !state.subject) return null;
    return [command, '--project', state.project, '--subject', state.subject];
  };

  bindRunButton('run-multicam-btn', 'multicam-output', requireSubject('multicam'));
  bindRunButton('run-dupes-btn', 'dupes-output', requireSubject('dupes'));
  bindRunButton('run-gaps-btn', 'gaps-output', requireSubject('gaps'));
  bindRunButton('run-delivery-btn', 'delivery-output', requireSubject('delivery'));
  bindRunButton('run-peaks-btn', 'delivery-output', requireSubject('peaks'));
  bindRunButton('run-learn-btn', 'learn-output', requireSubject('learn'));

  bindRunButton('run-versions-btn', 'versions-output', () => {
    if (!state.project) return null;
    return ['versions', '--project', state.project];
  });

  bindRunButton('run-diff-btn', 'versions-output', () => {
    const a = el('diff-a-input').value.trim();
    const b = el('diff-b-input').value.trim();
    if (!state.project || !a || !b) return null;
    return ['diff', a, b, '--project', state.project];
  });

  bindRunButton('run-revert-btn', 'versions-output', () => {
    const v = el('revert-input').value.trim();
    if (!state.project || !v) return null;
    // Non-destructive by design (edits.revert() forks a new version forward,
    // never deletes history) -- still a real persisted change, so confirm.
    if (!confirm(`Revert to ${v}? This forks a new version on top of it and makes that the current one.`)) {
      return null;
    }
    return ['revert', v, '--project', state.project];
  }, refreshPending);

  el('find-btn').addEventListener('click', async () => {
    if (!state.project) return;
    const query = el('find-input').value.trim();
    if (!query) return;
    const args = ['find', query, '--project', state.project];
    if (el('find-scope-subject').checked && state.subject) {
      args.push('--subject', state.subject);
    }
    const out = el('find-output');
    out.textContent = 'Searching...';
    try {
      const result = await window.theodore.run(args);
      out.textContent = formatRunResult(result);
      clearError();
    } catch (err) {
      out.textContent = '(command did not run -- see error above)';
      showError(err);
    }
  });

  el('say-btn').addEventListener('click', async () => {
    if (!state.project) return;
    const text = el('say-input').value.trim();
    if (!text) return;
    const out = el('say-output');
    out.textContent = 'Running...';
    try {
      const result = await window.theodore.run(['say', text, '--project', state.project]);
      out.textContent = formatRunResult(result);
      el('say-input').value = '';
      clearError();
      await refreshPending();
    } catch (err) {
      out.textContent = '(command did not run -- see error above)';
      showError(err);
    }
  });

  el('build-btn').addEventListener('click', async () => {
    if (!state.project || !state.subject) return;
    const out = el('say-output');
    out.textContent = 'Building...';
    try {
      const result = await window.theodore.run(['build', '--project', state.project, '--subject', state.subject]);
      out.textContent = formatRunResult(result);
      clearError();
      await refreshPending();
    } catch (err) {
      out.textContent = '(command did not run -- see error above)';
      showError(err);
    }
  });

  // Each independent: Resolve not being connected must never prevent the
  // project list (which needs only the config file, not Resolve at all)
  // from loading, and vice versa.
  try {
    const resolveProject = await window.theodore.currentResolveProjectName();
    el('resolve-project-name').textContent = resolveProject || '(not connected)';
  } catch (err) {
    el('resolve-project-name').textContent = '(not connected)';
  }

  await safely(refreshProjects);
}

init();
