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
  const previous = state.project;
  select.innerHTML = '';
  // These are Theodore's ingested-footage projects, not Resolve projects.
  // Defaulting to the first one handed the chat some other project's
  // transcripts while it edited whatever Resolve has open, so the default is
  // none: the chat follows Resolve, and a project is only an opt-in for the
  // transcript-based tools.
  const none = document.createElement('option');
  none.value = '';
  none.textContent = '(none)';
  select.appendChild(none);
  for (const p of projects) {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = p;
    select.appendChild(opt);
  }
  state.project = projects.includes(previous) ? previous : null;
  select.value = state.project || '';
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

// The chat is a conversation with Claude, which operates the open Resolve
// project. It talks to `theodore agent-chat --json`: one JSON event per line
// (streamed text, tool steps, confirmations, "done"), rendered here the way a
// chat app would -- words arriving as they're written, tool activity folded
// into a collapsible "steps" line, formatted replies, a typing indicator.
let chatProject = null;
let chatBuffer = '';
let chatBusy = false;
let turn = null;   // the reply currently being built

function ensureChatSession() {
  // The agent works on whatever Resolve has open, so it needs no Theodore
  // project; picking one only unlocks the transcript-based tools. Restart
  // only when that choice changes.
  const key = state.project || '';
  if (chatProject === key) return;
  const first = chatProject === null;
  chatProject = key;
  chatBuffer = '';
  turn = null;
  if (!first) addMessage('system', null, `switched to project "${key || '(none)'}"`);
  setBusy(true);
  showTyping(true);
  window.theodore.startChat(key);
}

function scrollChat() {
  const log = el('chat-log');
  log.scrollTop = log.scrollHeight;
}

function showTyping(on) {
  let dots = el('typing-dots');
  if (on && !dots) {
    dots = document.createElement('div');
    dots.id = 'typing-dots';
    dots.innerHTML = '<span></span><span></span><span></span>';
  }
  if (on) { el('chat-log').appendChild(dots); scrollChat(); }
  else if (dots) dots.remove();
}

function setBusy(busy) {
  chatBusy = busy;
  el('chat-send-btn').disabled = busy;
  el('chat-input').placeholder = busy
    ? 'Claude is working...'
    : 'Tell Claude what to do in Resolve -- e.g. "cut the ums and render a vertical version"';
}

function newTurn() {
  return { reply: null, raw: '', steps: null, stepList: null, stepCount: 0, lastStep: null };
}

function ensureReply() {
  if (!turn) turn = newTurn();
  if (!turn.reply) {
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-assistant';
    turn.reply = document.createElement('div');
    turn.reply.className = 'md';
    wrap.appendChild(turn.reply);
    el('chat-log').appendChild(wrap);
    turn.raw = '';
  }
}

function renderReply() {
  turn.reply.innerHTML = window.renderMarkdown(turn.raw);
  scrollChat();
}

function ensureSteps() {
  if (!turn) turn = newTurn();
  if (!turn.steps) {
    turn.steps = document.createElement('details');
    turn.steps.className = 'steps';
    turn.steps.open = true;
    turn.steps.innerHTML = '<summary></summary><div class="step-list"></div>';
    turn.stepList = turn.steps.querySelector('.step-list');
    el('chat-log').appendChild(turn.steps);
  }
}

function summariseSteps(finished) {
  if (!turn || !turn.steps) return;
  const n = turn.stepCount;
  turn.steps.querySelector('summary').textContent =
    finished ? `Used ${n} tool${n === 1 ? '' : 's'}` : `Working... ${n} step${n === 1 ? '' : 's'}`;
}

// Tools used after Claude has started writing belong in a NEW steps line
// below that text, not folded back into the group above it.
function closeStepsGroup() {
  if (!turn || !turn.steps) return;
  summariseSteps(true);
  turn.steps.open = false;
  turn.steps = null;
  turn.stepCount = 0;
}

function handleAgentEvent(ev) {
  switch (ev.t) {
    case 'ready':
      showTyping(false);
      setBusy(false);
      addMessage('system', null, ev.text);
      break;
    case 'delta':
      showTyping(false);
      closeStepsGroup();
      ensureReply();
      turn.raw += ev.text;
      renderReply();
      break;
    case 'say':
      showTyping(false);
      closeStepsGroup();
      ensureReply();
      turn.raw = ev.text;
      renderReply();
      break;
    case 'say_end':
      // The next stretch of text (after tool calls) is a new paragraph block.
      if (turn) turn.reply = null;
      showTyping(true);
      break;
    case 'tool': {
      showTyping(false);
      ensureSteps();
      if (turn.reply) turn.reply = null;
      turn.stepCount += 1;
      const line = document.createElement('div');
      line.className = 'step';
      const args = JSON.stringify(ev.input || {});
      line.textContent = `${ev.name}${args === '{}' ? '' : ' ' + args.slice(0, 110)}`;
      turn.stepList.appendChild(line);
      turn.lastStep = line;
      summariseSteps(false);
      showTyping(true);
      scrollChat();
      break;
    }
    case 'result':
      if (turn && turn.lastStep) {
        turn.lastStep.classList.add(ev.ok ? 'ok' : 'failed');
        if (!ev.ok && ev.error) turn.lastStep.textContent += `  -- ${ev.error}`;
        if (ev.frame) turn.lastStep.textContent += '  (looked at the frame)';
      }
      break;
    case 'confirm':
      showTyping(false);
      el('confirm-text').textContent = ev.text;
      el('confirm-bar').classList.remove('hidden');
      break;
    case 'note':
      addMessage('system', null, ev.text);
      break;
    case 'error':
      showTyping(false);
      addMessage('system', null, ev.text);
      break;
    case 'done':
      showTyping(false);
      summariseSteps(true);
      if (turn && turn.steps) turn.steps.open = false;
      turn = null;
      setBusy(false);
      el('chat-input').focus();
      break;
    default:
      break;
  }
}

function handleChatStdout(text) {
  chatBuffer += text;
  let nl;
  while ((nl = chatBuffer.indexOf('\n')) !== -1) {
    const line = chatBuffer.slice(0, nl).trim();
    chatBuffer = chatBuffer.slice(nl + 1);
    if (!line) continue;
    let ev;
    try { ev = JSON.parse(line); } catch (err) { addMessage('system', null, line); continue; }
    handleAgentEvent(ev);
  }
}

function initChat() {
  window.theodore.onChatOutput((chunk) => {
    if (chunk.stream === 'stderr') {
      // Tracebacks and startup errors (e.g. no API key) -- show, don't hide.
      showTyping(false);
      addMessage('system', null, chunk.text.trim());
      return;
    }
    handleChatStdout(chunk.text);
  });
  window.theodore.onChatExit((info) => {
    showTyping(false);
    addMessage('system', null, `Claude chat ended${info.error ? ': ' + info.error : ''}. Send a message to start it again.`);
    chatProject = null;
    turn = null;
    chatBuffer = '';
    setBusy(false);
    el('confirm-bar').classList.add('hidden');
  });

  const answer = (reply) => {
    el('confirm-bar').classList.add('hidden');
    addMessage('user', null, reply === 'yes' ? 'Yes, go ahead.' : 'No, don\'t.');
    showTyping(true);
    window.theodore.sendChat(reply);
  };
  el('confirm-yes-btn').addEventListener('click', () => answer('yes'));
  el('confirm-no-btn').addEventListener('click', () => answer('no'));

  const input = el('chat-input');
  const autosize = () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    input.style.overflowY = input.scrollHeight > 140 ? 'auto' : 'hidden';
  };
  const send = () => {
    const line = input.value.trim();
    if (!line || chatBusy) return;
    ensureChatSession();
    addMessage('user', null, line);
    turn = newTurn();
    setBusy(true);
    showTyping(true);
    window.theodore.sendChat(line);
    input.value = '';
    autosize();
  };
  el('chat-send-btn').addEventListener('click', send);

  // Videos go into the message as quoted paths, so the editor can still say
  // what to do with them ("...and cut my footage the same way") before sending.
  const addVideos = (paths) => {
    const clean = paths.filter(Boolean);
    if (!clean.length) return;
    const quoted = clean.map((p) => `"${p}"`).join(' ');
    // A still is a look to match; a video is a reference to study.
    const images = clean.every((p) => /\.(jpe?g|png|tiff?|webp|heic)$/i.test(p));
    const lead = images
      ? (clean.length === 1 ? 'Grade my timeline to match the look of this image:' : 'Match the look of these images:')
      : (clean.length === 1 ? 'Study this video:' : 'Study these videos:');
    input.value = `${input.value.trim() ? input.value.trim() + '\n' : ''}${lead} ${quoted} `;
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  };
  el('attach-btn').addEventListener('click', async () => {
    try { addVideos(await window.theodore.pickVideos()); } catch (err) { showError(err); }
  });
  document.addEventListener('dragover', (e) => {
    e.preventDefault();
    document.body.classList.add('dropping');
  });
  document.addEventListener('dragleave', (e) => {
    if (e.target === document.documentElement || !e.relatedTarget) document.body.classList.remove('dropping');
  });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    document.body.classList.remove('dropping');
    addVideos([...(e.dataTransfer.files || [])].map((f) => window.theodore.pathForFile(f)));
  });
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    // Enter sends, Shift+Enter is a newline -- the way a chat box works.
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
}

async function init() {
  initIngestTab();
  initQuickActions();
  initChat();

  el('project-select').addEventListener('change', (e) => {
    state.project = e.target.value || null;
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

    // Separate from the project name above on purpose. That comes from this
    // panel's own in-process Resolve handle, but every button below shells
    // out to the `theodore` CLI, which reaches Resolve through the Python
    // bridge. Without this, a panel showing a happily connected project
    // while every action fails is genuinely baffling to debug.
    const bridgeEl = el('bridge-state');
    if (!bridgeEl) return;
    let running = false;
    try {
      running = await window.theodore.bridgeRunning();
    } catch (err) {
      running = false;
    }
    bridgeEl.textContent = running ? 'bridge on' : 'bridge off';
    bridgeEl.classList.toggle('bridge-on', running);
    bridgeEl.classList.toggle('bridge-off', !running);
    bridgeEl.title = running
      ? 'Theodore is connected to Resolve. Actions run instantly.'
      : 'Not connected. In Resolve: Workspace -> Scripts -> Theodore -> '
        + 'Theodore Bridge (once per Resolve session). If it is not in that '
        + 'menu, run: theodore install-bridge, then fully quit and reopen Resolve.';
  }
  await pollResolveStatus();
  setInterval(pollResolveStatus, RESOLVE_POLL_INTERVAL_MS);

  // Start the chat only once the project picker has settled: starting it
  // first meant the very first message found a "changed" project and
  // restarted the agent mid-send.
  await safely(refreshProjects);
  ensureChatSession();
}

init();
