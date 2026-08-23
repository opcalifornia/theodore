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

  // Native picker so the editor chooses footage by clicking, never by
  // typing a path into a text box.
  pickSource: () => ipcRenderer.invoke('theodore:pickSource'),

  // Streaming counterpart to run(): fire the command, then listen for
  // output chunks and a final result via the two callbacks below, keyed by
  // requestId so an old listener from a previous run never gets called
  // for a new one. Returns an unsubscribe function.
  runStreaming: (args, onOutput, onDone) => {
    const requestId = `${Date.now()}-${Math.random()}`;
    const outputListener = (_e, id, chunk) => { if (id === requestId) onOutput(chunk); };
    const doneListener = (_e, id, result) => {
      if (id !== requestId) return;
      ipcRenderer.removeListener('theodore:run-output', outputListener);
      ipcRenderer.removeListener('theodore:run-done', doneListener);
      onDone(result);
    };
    ipcRenderer.on('theodore:run-output', outputListener);
    ipcRenderer.on('theodore:run-done', doneListener);
    ipcRenderer.send('theodore:runStreaming', requestId, args);
    return () => {
      ipcRenderer.removeListener('theodore:run-output', outputListener);
      ipcRenderer.removeListener('theodore:run-done', doneListener);
    };
  },
});
