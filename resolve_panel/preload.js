'use strict';

// The renderer's ENTIRE view of Theodore and Resolve: no Node, no
// filesystem, no child_process reachable from index.html/renderer.js at
// all -- only these named calls, each round-tripping through main.js's
// ipcMain handlers. This is what DaVinci Resolve 19.0.2+ requires
// (contextIsolation + a preload script) for a Workflow Integration plugin,
// and it's good practice on its own terms.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('theodore', {
  listProjects: () => ipcRenderer.invoke('theodore:listProjects'),
  listSubjects: (project) => ipcRenderer.invoke('theodore:listSubjects', project),
  readAnalysis: (project, subject) => ipcRenderer.invoke('theodore:readAnalysis', project, subject),
  readJSONFile: (project, subject, filename) =>
    ipcRenderer.invoke('theodore:readJSONFile', project, subject, filename),
  readPending: (project) => ipcRenderer.invoke('theodore:readPending', project),
  run: (args) => ipcRenderer.invoke('theodore:run', args),
  currentResolveProjectName: () => ipcRenderer.invoke('theodore:currentResolveProjectName'),
});
