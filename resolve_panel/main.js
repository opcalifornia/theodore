'use strict';

// DaVinci Resolve's Workflow Integration host launches THIS file (named in
// manifest.xml's <FilePath>) as an Electron main process when the editor
// picks "Theodore" from Workspace -> Workflow Integrations.
//
// Must match manifest.xml's <Id> exactly -- WorkflowIntegration.node uses it
// to identify this plugin to Resolve.
const PLUGIN_ID = 'com.theodore.editor.panel';

const { app, BrowserWindow, ipcMain } = require('electron');
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
