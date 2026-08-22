'use strict';

// Everything this panel knows how to do to Theodore, in one place, with zero
// Electron or DaVinci Resolve dependency -- so it's testable with plain
// `node --test`, the same "validate what's actually testable" discipline
// the rest of this project holds to. main.js is a thin IPC wrapper around
// this module; it adds nothing of its own.
//
// Every exported function takes an explicit `config` as its first argument
// so tests can pass a fake one pointing at a temp directory. Production
// call sites (main.js) omit it and get loadConfig()'s real one.

const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');

const CONFIG_PATH = path.join(__dirname, 'theodore-panel-config.json');

class ConfigError extends Error {}

function loadConfig() {
  let raw;
  try {
    raw = fs.readFileSync(CONFIG_PATH, 'utf8');
  } catch (err) {
    throw new ConfigError(
      `No config at ${CONFIG_PATH}. Copy theodore-panel-config.example.json to ` +
      'theodore-panel-config.json next to it and fill in theodoreExecutable / ' +
      'theodoreDataDir -- see README.md.'
    );
  }
  let config;
  try {
    config = JSON.parse(raw);
  } catch (err) {
    throw new ConfigError(`theodore-panel-config.json is not valid JSON: ${err.message}`);
  }
  if (!config.theodoreExecutable) {
    throw new ConfigError('theodore-panel-config.json is missing "theodoreExecutable".');
  }
  if (!config.theodoreDataDir) {
    throw new ConfigError('theodore-panel-config.json is missing "theodoreDataDir".');
  }
  return config;
}

function projectDir(config, project) {
  return path.join(config.theodoreDataDir, project);
}

function subjectDir(config, project, subject) {
  return path.join(projectDir(config, project), 'subjects', subject);
}

function readJSONFileSync(filePath) {
  if (!fs.existsSync(filePath)) return null;
  const raw = fs.readFileSync(filePath, 'utf8');
  try {
    return JSON.parse(raw);
  } catch (err) {
    // A malformed file on disk is worth surfacing loudly -- Theodore's own
    // JSON is always machine-written, so this means something else touched
    // it, or a write was interrupted. Silently returning null would look
    // identical to "nothing built yet".
    throw new Error(`${filePath} is not valid JSON: ${err.message}`);
  }
}

function listProjects(config) {
  config = config || loadConfig();
  if (!fs.existsSync(config.theodoreDataDir)) return [];
  return fs.readdirSync(config.theodoreDataDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .filter((entry) => fs.existsSync(path.join(config.theodoreDataDir, entry.name, 'project.json')))
    .map((entry) => entry.name)
    .sort();
}

function listSubjects(config, project) {
  config = config || loadConfig();
  const registry = readJSONFileSync(path.join(projectDir(config, project), 'project.json'));
  if (!registry || !registry.subjects) return [];
  return Object.keys(registry.subjects).sort();
}

function readAnalysis(config, project, subject) {
  config = config || loadConfig();
  return readJSONFileSync(path.join(subjectDir(config, project, subject), 'analysis.json'));
}

function readJSONFile(config, project, subject, filename) {
  config = config || loadConfig();
  return readJSONFileSync(path.join(subjectDir(config, project, subject), filename));
}

function readPending(config, project) {
  config = config || loadConfig();
  return readJSONFileSync(path.join(projectDir(config, project), 'edits', 'pending.json'));
}

// Kept separate from runTheodore() so the validation itself is testable
// without spawning a process.
function buildArgs(args) {
  if (!Array.isArray(args) || args.some((a) => typeof a !== 'string')) {
    throw new TypeError('theodore command args must be an array of strings');
  }
  return args;
}

// `execFileImpl` is injected only so tests can fake the actual process
// spawn; production callers always omit it and get the real child_process.
function runTheodore(config, args, execFileImpl) {
  config = config || loadConfig();
  const finalArgs = buildArgs(args);
  const exec = execFileImpl || execFile;
  return new Promise((resolve) => {
    exec(
      config.theodoreExecutable,
      finalArgs,
      { env: Object.assign({}, process.env, { THEODORE_DATA_DIR: config.theodoreDataDir }) },
      (error, stdout, stderr) => {
        // Resolved, not rejected, even on failure: a non-zero exit from
        // `theodore` (a bad --exclude id, a cost guardrail, a rejected
        // cross-subject build) is normal, expected output for the panel to
        // show the editor -- not a bug in the panel itself.
        resolve({
          ok: !error,
          code: error ? (error.code == null ? 1 : error.code) : 0,
          stdout: stdout || '',
          stderr: stderr || (error ? error.message : ''),
        });
      },
    );
  });
}

module.exports = {
  ConfigError,
  loadConfig,
  projectDir,
  subjectDir,
  listProjects,
  listSubjects,
  readAnalysis,
  readJSONFile,
  readPending,
  buildArgs,
  runTheodore,
};
