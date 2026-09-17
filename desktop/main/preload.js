"use strict";
// Runs in the renderer before the page, sandboxed and context-isolated.
// Exposes the few bits the UI needs (see frontend/lib/desktop.ts): runtime
// data passed via webPreferences.additionalArguments from main.js, and the
// update-check API backed by main/updates.js over IPC.
const { contextBridge, ipcRenderer } = require("electron");

function arg(name) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld(
  "gstDesktop",
  Object.freeze({
    apiBase: arg("gst-api-base"),
    version: arg("gst-app-version"),
    platform: process.platform,
    updates: Object.freeze({
      state: () => ipcRenderer.invoke("gst:update-state"),
      check: () => ipcRenderer.invoke("gst:update-check"),
      download: () => ipcRenderer.invoke("gst:update-download"),
      install: () => ipcRenderer.invoke("gst:update-install"),
      onChange: (callback) => {
        const handler = (_event, next) => callback(next);
        ipcRenderer.on("gst:update", handler);
        return () => ipcRenderer.removeListener("gst:update", handler);
      },
    }),
  }),
);
