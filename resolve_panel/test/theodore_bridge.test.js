'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

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
