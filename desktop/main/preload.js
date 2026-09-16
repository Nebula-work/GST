"use strict";
// Runs in the renderer before the page, sandboxed and context-isolated.
// Exposes the few bits of runtime data the UI needs (see frontend/lib/desktop.ts).
// The values arrive via webPreferences.additionalArguments from main.js.
const { contextBridge } = require("electron");

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
  }),
);
