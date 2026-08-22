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

  el('run-dupes-btn').addEventListener('click', async () => {
    if (!state.project || !state.subject) return;
    const out = el('dupes-output');
    out.textContent = 'Running...';
    const result = await window.theodore.run(['dupes', '--project', state.project, '--subject', state.subject]);
    out.textContent = formatRunResult(result);
  });

  el('run-gaps-btn').addEventListener('click', async () => {
    if (!state.project || !state.subject) return;
    const out = el('gaps-output');
    out.textContent = 'Running...';
    const result = await window.theodore.run(['gaps', '--project', state.project, '--subject', state.subject]);
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
