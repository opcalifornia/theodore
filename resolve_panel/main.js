'use strict';

// DaVinci Resolve's Workflow Integration host launches THIS file (named in
// manifest.xml's <FilePath>) as an Electron main process when the editor
// picks "Theodore" from Workspace -> Workflow Integrations.
//
// Must match manifest.xml's <Id> exactly -- WorkflowIntegration.node uses it
// to identify this plugin to Resolve.
const PLUGIN_ID = 'com.theodore.editor.panel';

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');

const bridge = require('./theodore_bridge');

// WorkflowIntegration.node is a native module Blackmagic ships with Resolve
// / its Workflow Integration SDK -- Theodore cannot generate or vendor it
// (see README). It's simply absent when this panel is run standalone for
// local development, which must never be fatal: everything except the
// "what Resolve project is currently open" convenience degrades gracefully
// without it.
let resolveAPI = null;
try {
  const WorkflowIntegration = require('./WorkflowIntegration.node');
  if (WorkflowIntegration.Initialize(PLUGIN_ID)) {
    resolveAPI = WorkflowIntegration.GetResolve();
  } else {
    console.warn('WorkflowIntegration.Initialize() returned false -- running without a live Resolve connection.');
  }
} catch (err) {
  console.warn('WorkflowIntegration.node not available -- running without a live Resolve connection:', err.message);
}

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 980,
    height: 720,
    title: 'Theodore',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // Required by DaVinci Resolve 19.0.2+'s Workflow Integration
      // sandboxing rules, and just good practice regardless: the renderer
      // gets NO Node/Resolve access except through preload.js's narrow
      // contextBridge surface.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Resolve runs this app as a daemon and only launches it when it isn't
  // already running. If closing the window left the app alive (the macOS
  // default), picking Theodore from Workspace -> Workflow Integrations again
  // would find a windowless process with nothing to show -- the panel would
  // just never appear. Quit with the window, as Blackmagic's SamplePlugin
  // does, so the next pick is a clean relaunch.
  mainWindow.on('close', () => app.quit());
  mainWindow.loadFile(path.join(__dirname, 'index.html'));
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => app.quit());

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => {
  if (activeChat) activeChat.stop();
  try {
    const WorkflowIntegration = require('./WorkflowIntegration.node');
    WorkflowIntegration.CleanUp();
  } catch (err) {
    // Not available or already cleaned up -- fine either way.
  }
});

// ---- IPC surface -----------------------------------------------------
// Every handler is a thin pass-through to theodore_bridge.js, which is
// what's actually under test. Nothing here has its own logic to get wrong.

ipcMain.handle('theodore:listProjects', () => bridge.listProjects());
ipcMain.handle('theodore:listSubjects', (_e, project) => bridge.listSubjects(undefined, project));
ipcMain.handle('theodore:readAnalysis', (_e, project, subject) => bridge.readAnalysis(undefined, project, subject));
ipcMain.handle('theodore:readJSONFile', (_e, project, subject, filename) =>
  bridge.readJSONFile(undefined, project, subject, filename));
ipcMain.handle('theodore:readPending', (_e, project) => bridge.readPending(undefined, project));
ipcMain.handle('theodore:run', (_e, args) => bridge.runTheodore(undefined, args));

// Native "choose a file or a folder of footage" picker so the editor never
// has to know or type a filesystem path -- the single biggest source of the
// "back and forth to terminal" friction this Ingest tab exists to remove.
// Combining openFile + openDirectory in one dialog is Mac-only behavior in
// Electron; that's fine, DaVinci Resolve's Workflow Integration plugins only
// run on Mac and Windows, and this panel is developed against Mac.
ipcMain.handle('theodore:pickSource', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose footage to ingest',
    properties: ['openFile', 'openDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

// Videos for Claude to study (a reference to copy, or footage to cut).
ipcMain.handle('theodore:pickVideos', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose videos or images for Claude',
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Video or image', extensions: ['mp4', 'mov', 'm4v', 'mxf', 'avi', 'mkv', 'webm', 'mts',
      'jpg', 'jpeg', 'png', 'tif', 'tiff', 'webp', 'heic'] }],
  });
  return result.canceled ? [] : result.filePaths;
});

// `theodore run` (ingest -> transcribe -> analyze -> markers -> notes) can
// take minutes, so it's streamed instead of using theodore:run's buffered
// invoke/resolve: `event.sender.send` pushes each output chunk to the
// renderer as it arrives, with a final 'theodore:run-done' once the process
// exits. Fire-and-forget (`ipcMain.on`, not `.handle`) because the caller
// isn't waiting on a single return value -- it's listening for a stream.
ipcMain.on('theodore:runStreaming', (event, requestId, args) => {
  bridge.spawnTheodore(
    undefined,
    args,
    (chunk) => {
      if (event.sender.isDestroyed()) return;
      event.sender.send('theodore:run-output', requestId, chunk);
    },
  ).then((result) => {
    if (event.sender.isDestroyed()) return;
    event.sender.send('theodore:run-done', requestId, result);
  });
});

// One conversational `theodore chat` process at a time, matching the
// panel's single-project-open-at-once UI. Held here (not in
// theodore_bridge.js) because it's per-window session state, not logic --
// exactly the "main.js is a thin wrapper" split the rest of the IPC
// surface already follows.
let activeChat = null;

ipcMain.on('theodore:chatStart', (event, project) => {
  if (activeChat) activeChat.stop();
  // A replaced chat's process exits AFTER its successor has started. Its
  // output and exit belong to nobody now: forwarding them would null out the
  // live chat here and tell the renderer "chat ended", so every following
  // message would spawn a fresh agent with no memory of the conversation.
  let chat;
  try {
    chat = bridge.startAgentChat(
      undefined,
      project,
      (chunk) => {
        if (activeChat !== chat || event.sender.isDestroyed()) return;
        event.sender.send('theodore:chat-output', chunk);
      },
      (info) => {
        if (activeChat !== chat) return;
        activeChat = null;
        if (!event.sender.isDestroyed()) event.sender.send('theodore:chat-exit', info);
      },
    );
  } catch (err) {
    // e.g. a missing theodore-panel-config.json. Thrown from an ipcMain.on
    // listener it would vanish, leaving the panel "working" forever.
    activeChat = null;
    if (!event.sender.isDestroyed()) event.sender.send('theodore:chat-exit', { code: 1, error: err.message });
    return;
  }
  activeChat = chat;
});

ipcMain.on('theodore:chatSend', (_e, line) => {
  if (activeChat) activeChat.send(line);
});

ipcMain.on('theodore:chatStop', () => {
  if (activeChat) activeChat.stop();
  activeChat = null;
});

// Whether Theodore's PYTHON bridge is running inside Resolve.
//
// Distinct from resolveAPI above, and both matter. resolveAPI is this
// panel's own in-process JavaScript handle, which Resolve hands to a
// Workflow Integration plugin -- it is what the status line's project name
// comes from. But every quick action shells out to the `theodore` CLI, and
// the CLI reaches Resolve through the Python bridge instead.
//
// So the panel can legitimately show a connected project while every button
// fails, which is a baffling thing to debug. Reading the handshake file
// tells us which, cheaply enough to poll (no subprocess, no socket).
function bridgeHandshakePath() {
  const home = process.env.THEODORE_BRIDGE_HOME
    || path.join(require('os').homedir(), '.theodore');
  return path.join(home, 'bridge.json');
}

ipcMain.handle('theodore:bridgeRunning', () => {
  try {
    const raw = require('fs').readFileSync(bridgeHandshakePath(), 'utf8');
    const data = JSON.parse(raw);
    // A handshake file alone doesn't prove the bridge is alive -- Resolve
    // may have quit without cleaning up -- but it does tell us whether it
    // was ever started, which is the difference between "run the bridge"
    // and "restart the bridge".
    return Boolean(data && data.port && data.token);
  } catch (err) {
    return false;
  }
});

ipcMain.handle('theodore:currentResolveProjectName', () => {
  if (!resolveAPI) return null;
  try {
    const project = resolveAPI.GetProjectManager().GetCurrentProject();
    return project ? project.GetName() : null;
  } catch (err) {
    console.warn('Could not read the current Resolve project name:', err.message);
    return null;
  }
});
