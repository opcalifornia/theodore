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
  mainWindow.loadFile('index.html');
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
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
  activeChat = bridge.startChat(
    undefined,
    project,
    (chunk) => { if (!event.sender.isDestroyed()) event.sender.send('theodore:chat-output', chunk); },
    (info) => {
      activeChat = null;
      if (!event.sender.isDestroyed()) event.sender.send('theodore:chat-exit', info);
    },
  );
});

ipcMain.on('theodore:chatSend', (_e, line) => {
  if (activeChat) activeChat.send(line);
});

ipcMain.on('theodore:chatStop', () => {
  if (activeChat) activeChat.stop();
  activeChat = null;
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
