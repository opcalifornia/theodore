'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { EventEmitter } = require('events');

const bridge = require('../theodore_bridge');

function makeTempDataDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'theodore-panel-test-'));
}

function writeJSON(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data));
}

test('listProjects finds only directories with a project.json', () => {
  const dataDir = makeTempDataDir();
  writeJSON(path.join(dataDir, 'docproj', 'project.json'), { name: 'docproj' });
  fs.mkdirSync(path.join(dataDir, 'not_a_project'));
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.listProjects(config), ['docproj']);
});

test('listProjects returns empty array for a missing data dir', () => {
  const config = { theodoreDataDir: '/no/such/path', theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.listProjects(config), []);
});

test('listProjects sorts multiple projects', () => {
  const dataDir = makeTempDataDir();
  writeJSON(path.join(dataDir, 'zzz', 'project.json'), {});
  writeJSON(path.join(dataDir, 'aaa', 'project.json'), {});
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.listProjects(config), ['aaa', 'zzz']);
});

test('listSubjects reads project.json subjects, sorted', () => {
  const dataDir = makeTempDataDir();
  writeJSON(path.join(dataDir, 'docproj', 'project.json'), {
    name: 'docproj', subjects: { marcus: {}, haylee: {} },
  });
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.listSubjects(config, 'docproj'), ['haylee', 'marcus']);
});

test('listSubjects returns empty array when project.json is missing', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.listSubjects(config, 'nope'), []);
});

test('readAnalysis returns null when analysis.json does not exist yet', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.equal(bridge.readAnalysis(config, 'docproj', 'haylee'), null);
});

test('readAnalysis reads a real analysis.json', () => {
  const dataDir = makeTempDataDir();
  const analysis = { segments: [{ id: 'haylee.q01' }], selects: [] };
  writeJSON(path.join(dataDir, 'docproj', 'subjects', 'haylee', 'analysis.json'), analysis);
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.readAnalysis(config, 'docproj', 'haylee'), analysis);
});

test('readJSONFile is generic over the filename', () => {
  const dataDir = makeTempDataDir();
  writeJSON(path.join(dataDir, 'docproj', 'subjects', 'haylee', 'redundancy.json'), { groups: [] });
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.readJSONFile(config, 'docproj', 'haylee', 'redundancy.json'), { groups: [] });
});

test('readJSONFile raises a clear error on malformed JSON rather than returning null', () => {
  const dataDir = makeTempDataDir();
  const filePath = path.join(dataDir, 'docproj', 'subjects', 'haylee', 'redundancy.json');
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, '{not valid json');
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.throws(
    () => bridge.readJSONFile(config, 'docproj', 'haylee', 'redundancy.json'),
    /not valid JSON/,
  );
});

test('readPending reads edits/pending.json', () => {
  const dataDir = makeTempDataDir();
  writeJSON(path.join(dataDir, 'docproj', 'edits', 'pending.json'), { version: '(pending)', sequence: [] });
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.deepEqual(bridge.readPending(config, 'docproj'), { version: '(pending)', sequence: [] });
});

test('readPending returns null when nothing is queued', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: 'theodore' };
  assert.equal(bridge.readPending(config, 'docproj'), null);
});

test('loadConfig raises ConfigError with a clear message when the config file is missing', () => {
  // theodore-panel-config.json is gitignored and must not exist in CI/tests.
  assert.throws(() => bridge.loadConfig(), bridge.ConfigError);
});

test('buildArgs rejects a non-array', () => {
  assert.throws(() => bridge.buildArgs('say hello'), TypeError);
});

test('buildArgs rejects an array with a non-string element', () => {
  assert.throws(() => bridge.buildArgs(['say', 123]), TypeError);
});

test('buildArgs passes through a valid argv array unchanged', () => {
  const args = ['say', 'move haylee.q03 after marcus.q04', '--project', 'docproj'];
  assert.deepEqual(bridge.buildArgs(args), args);
});

test('runTheodore invokes the configured executable with THEODORE_DATA_DIR set, and resolves ok on success', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  let captured = null;
  const fakeExecFile = (cmd, args, opts, cb) => {
    captured = { cmd, args, opts };
    cb(null, 'ok output', '');
  };
  const result = await bridge.runTheodore(config, ['status', '--project', 'docproj'], fakeExecFile);
  assert.equal(captured.cmd, '/usr/bin/theodore-fake');
  assert.deepEqual(captured.args, ['status', '--project', 'docproj']);
  assert.equal(captured.opts.env.THEODORE_DATA_DIR, dataDir);
  assert.deepEqual(result, { ok: true, code: 0, stdout: 'ok output', stderr: '' });
});

test('runTheodore reports a non-zero exit without throwing', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const fakeExecFile = (cmd, args, opts, cb) => {
    const err = new Error('boom');
    err.code = 1;
    cb(err, '', 'Error: something went wrong');
  };
  const result = await bridge.runTheodore(
    config, ['build', '--project', 'docproj', '--subject', 'haylee'], fakeExecFile,
  );
  assert.equal(result.ok, false);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /something went wrong/);
});

test('runTheodore rejects bad args before ever spawning a process', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  let spawned = false;
  const fakeExecFile = (cmd, args, opts, cb) => { spawned = true; cb(null, '', ''); };
  assert.throws(() => bridge.runTheodore(config, 'not-an-array', fakeExecFile), TypeError);
  assert.equal(spawned, false);
});

// A minimal stand-in for the ChildProcess node.spawn() returns: an
// EventEmitter with .stdout/.stderr sub-emitters, driven by the test itself
// rather than a real process.
function makeFakeChild() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  return child;
}

test('spawnTheodore streams stdout/stderr chunks via onChunk as they arrive', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChild();
  let captured = null;
  const fakeSpawn = (cmd, args, opts) => {
    captured = { cmd, args, opts };
    return child;
  };
  const chunks = [];
  const promise = bridge.spawnTheodore(config, ['run', 'src', '--project', 'docproj', '--subject', 'haylee'], (c) => chunks.push(c), fakeSpawn);

  child.stdout.emit('data', Buffer.from('ingesting...\n'));
  child.stderr.emit('data', Buffer.from('warning: low disk\n'));
  child.stdout.emit('data', Buffer.from('done.\n'));
  child.emit('close', 0);

  const result = await promise;
  assert.equal(captured.cmd, '/usr/bin/theodore-fake');
  assert.equal(captured.opts.env.THEODORE_DATA_DIR, dataDir);
  assert.deepEqual(chunks, [
    { stream: 'stdout', text: 'ingesting...\n' },
    { stream: 'stderr', text: 'warning: low disk\n' },
    { stream: 'stdout', text: 'done.\n' },
  ]);
  assert.deepEqual(result, { ok: true, code: 0 });
});

test('spawnTheodore reports a non-zero exit without throwing', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChild();
  const fakeSpawn = () => child;
  const promise = bridge.spawnTheodore(config, ['run', 'src', '--project', 'docproj', '--subject', 'haylee'], () => {}, fakeSpawn);

  child.emit('close', 1);

  const result = await promise;
  assert.deepEqual(result, { ok: false, code: 1 });
});

test('spawnTheodore resolves (not rejects) when the process itself fails to start', async () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/no/such/executable' };
  const child = makeFakeChild();
  const fakeSpawn = () => child;
  const promise = bridge.spawnTheodore(config, ['run'], () => {}, fakeSpawn);

  child.emit('error', new Error('spawn ENOENT'));

  const result = await promise;
  assert.equal(result.ok, false);
  assert.match(result.error, /ENOENT/);
});

test('spawnTheodore rejects bad args before ever spawning a process', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  let spawned = false;
  const fakeSpawn = () => { spawned = true; return makeFakeChild(); };
  assert.throws(() => bridge.spawnTheodore(config, 'not-an-array', () => {}, fakeSpawn), TypeError);
  assert.equal(spawned, false);
});

function makeFakeChatChild() {
  const child = makeFakeChild();
  child.stdin = { written: [], write(text) { this.written.push(text); }, ended: false, end() { this.ended = true; } };
  child.killed = false;
  child.kill = function () { this.killed = true; };
  return child;
}

test('startChat spawns `chat --project X` and streams both stdout and stderr via onChunk', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChatChild();
  let captured = null;
  const fakeSpawn = (cmd, args, opts) => { captured = { cmd, args, opts }; return child; };
  const chunks = [];
  bridge.startChat(config, 'docproj', (c) => chunks.push(c), () => {}, fakeSpawn);

  assert.equal(captured.cmd, '/usr/bin/theodore-fake');
  assert.deepEqual(captured.args, ['chat', '--project', 'docproj']);
  assert.equal(captured.opts.env.THEODORE_DATA_DIR, dataDir);

  child.stdout.emit('data', Buffer.from("Theodore chat -- project 'docproj', 1 subject(s) loaded.\ntheodore> "));
  assert.deepEqual(chunks, [
    { stream: 'stdout', text: "Theodore chat -- project 'docproj', 1 subject(s) loaded.\ntheodore> " },
  ]);
});

test('startChat.send() writes the line plus a newline to the child\'s stdin', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChatChild();
  const session = bridge.startChat(config, 'docproj', () => {}, () => {}, () => child);

  session.send('move haylee.q03 after marcus.q04');

  assert.deepEqual(child.stdin.written, ['move haylee.q03 after marcus.q04\n']);
});

test('startChat.stop() ends stdin and kills the child', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChatChild();
  const session = bridge.startChat(config, 'docproj', () => {}, () => {}, () => child);

  session.stop();

  assert.equal(child.stdin.ended, true);
  assert.equal(child.killed, true);
});

test('startChat calls onExit with the exit code when the REPL process closes', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  const child = makeFakeChatChild();
  let exitInfo = null;
  bridge.startChat(config, 'docproj', () => {}, (info) => { exitInfo = info; }, () => child);

  child.emit('close', 0);

  assert.deepEqual(exitInfo, { code: 0 });
});

test('startChat rejects a missing/blank project name before ever spawning', () => {
  const dataDir = makeTempDataDir();
  const config = { theodoreDataDir: dataDir, theodoreExecutable: '/usr/bin/theodore-fake' };
  let spawned = false;
  const fakeSpawn = () => { spawned = true; return makeFakeChatChild(); };
  assert.throws(() => bridge.startChat(config, '', () => {}, () => {}, fakeSpawn), TypeError);
  assert.equal(spawned, false);
});
