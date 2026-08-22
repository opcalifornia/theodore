'use strict';

// Runs in the renderer, sandboxed per preload.js: the only Theodore/Resolve
// surface available here is `window.theodore`, exposed via contextBridge.
// No requires, no filesystem, no child_process -- this file is plain DOM
// scripting, deliberately with no framework or build step.

const state = { project: null, subject: null };

const el = (id) => document.getElementById(id);

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
    const result = await window.theodore.run(args);
    out.textContent = formatRunResult(result);
    if (afterRun) await afterRun();
  });
}

async function init() {
  el('project-select').addEventListener('change', async (e) => {
    state.project = e.target.value;
    await refreshSubjects();
  });
  el('subject-select').addEventListener('change', async (e) => {
    state.subject = e.target.value;
    await refreshAll();
  });
  el('refresh-btn').addEventListener('click', refreshAll);

  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  }

  const requireSubject = (command) => () => {
    if (!state.project || !state.subject) return null;
    return [command, '--project', state.project, '--subject', state.subject];
  };

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
    const result = await window.theodore.run(args);
    out.textContent = formatRunResult(result);
  });

  el('say-btn').addEventListener('click', async () => {
    if (!state.project) return;
    const text = el('say-input').value.trim();
    if (!text) return;
    const out = el('say-output');
    out.textContent = 'Running...';
    const result = await window.theodore.run(['say', text, '--project', state.project]);
    out.textContent = formatRunResult(result);
    el('say-input').value = '';
    await refreshPending();
  });

  el('build-btn').addEventListener('click', async () => {
    if (!state.project || !state.subject) return;
    const out = el('say-output');
    out.textContent = 'Building...';
    const result = await window.theodore.run(['build', '--project', state.project, '--subject', state.subject]);
    out.textContent = formatRunResult(result);
    await refreshPending();
  });

  const resolveProject = await window.theodore.currentResolveProjectName();
  el('resolve-project-name').textContent = resolveProject || '(not connected)';

  await refreshProjects();
}

init();
