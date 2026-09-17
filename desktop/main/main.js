"use strict";
/**
 * Electron main process for the GST Purchase Matcher desktop app.
 *
 * Startup:
 *   1. pick a free loopback port and spawn the bundled Python backend on it
 *      (main/backend.js) while a small loading page is shown,
 *   2. serve the Next.js static export (renderer/) from a custom `app://gst`
 *      origin so absolute /_next/... asset paths and /privacy routing work,
 *   3. hand the backend URL to the page through the preload bridge.
 *
 * The renderer is sandboxed with no Node access; the only thing it learns from
 * the shell is the backend URL and the app version.
 */
const { app, BrowserWindow, dialog, net, protocol, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const backend = require("./backend");
const { installMenu } = require("./menu");
const updates = require("./updates");
const pkg = require("../package.json");

const APP_SCHEME = "app";
const APP_HOST = "gst";
const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;
const RENDERER_DIR = path.join(__dirname, "..", "renderer");
const LOADING_PAGE = path.join(__dirname, "loading.html");
const AUTHOR_NAME = String(pkg.author || "").replace(/\s*<[^>]*>/, "").trim();
const COPYRIGHT = `Copyright © ${new Date().getFullYear()} ${AUTHOR_NAME}`;

// Must run before app is ready: makes app:// behave like https:// (origin,
// history API, fetch, blob: URLs for the CSV export).
protocol.registerSchemesAsPrivileged([
  {
    scheme: APP_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
  },
]);

let mainWindow = null;
let apiBase = null;
let quitting = false;

// ---------------------------------------------------------------- renderer --

function contentSecurityPolicy() {
  // 'unsafe-inline' for scripts is needed by Next.js' static export (inline
  // RSC payload / hydration scripts); everything else is locked down.
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${apiBase}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}

async function isFile(p) {
  try {
    return (await fs.promises.stat(p)).isFile();
  } catch {
    return false;
  }
}

async function fileResponse(absPath, status) {
  const res = await net.fetch(pathToFileURL(absPath).toString());
  const headers = new Headers(res.headers);
  if (absPath.endsWith(".html")) headers.set("Content-Security-Policy", contentSecurityPolicy());
  return new Response(res.body, { status, headers });
}

/** Serve renderer/ like a static web host: /x -> x, x.html or x/index.html. */
async function serveRenderer(request) {
  const url = new URL(request.url);
  if (url.host !== APP_HOST) return new Response("Not found", { status: 404 });

  let rel;
  try {
    rel = decodeURIComponent(url.pathname).replace(/^\/+/, "");
  } catch {
    return new Response("Bad request", { status: 400 });
  }

  const candidates = rel === "" ? ["index.html"] : [rel];
  if (rel !== "" && !path.posix.extname(rel)) {
    candidates.push(`${rel}.html`, path.posix.join(rel, "index.html"));
  }
  for (const candidate of candidates) {
    const abs = path.resolve(RENDERER_DIR, candidate);
    if (abs !== RENDERER_DIR && !abs.startsWith(RENDERER_DIR + path.sep)) continue; // stay inside renderer/
    if (await isFile(abs)) return fileResponse(abs, 200);
  }

  const notFound = path.join(RENDERER_DIR, "404.html");
  if (await isFile(notFound)) return fileResponse(notFound, 404);
  return new Response("Not found", { status: 404 });
}

// ------------------------------------------------------------------ window --

function openExternally(url) {
  if (/^(https?:|mailto:)/i.test(url)) shell.openExternal(url);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: "#fdfbf4",
    title: pkg.productName,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      // Read by preload.js and exposed to the page as window.gstDesktop.
      additionalArguments: [`--gst-api-base=${apiBase}`, `--gst-app-version=${app.getVersion()}`],
    },
  });
  win.once("ready-to-show", () => win.show());

  // Links out of the app (mailto:, https:) go to the system handler; nothing
  // else may open windows or navigate the renderer away from app://.
  win.webContents.setWindowOpenHandler(({ url }) => {
    openExternally(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`${APP_ORIGIN}/`)) {
      event.preventDefault();
      openExternally(url);
    }
  });
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type === "keyDown" && input.key === "F12") {
      win.webContents.toggleDevTools();
      event.preventDefault();
    }
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });
  return win;
}

function showFatal(title, message) {
  dialog.showErrorBox(title, `${message}\n\nLog file: ${backend.logFile() || "(none)"}`);
}

// -------------------------------------------------------------- smoke test --

/**
 * `GST_SMOKE_TEST=1 electron .` drives the real UI origin end to end -- bridge,
 * CORS, CSP, samples, a full reconcile -- then writes a screenshot and exits
 * 0/1. Used by `npm run smoke` and CI; never runs for users.
 */
async function runSmokeTest(win) {
  try {
    const result = await win.webContents.executeJavaScript(
      `(async () => {
        const base = window.gstDesktop && window.gstDesktop.apiBase;
        if (!base) throw new Error("desktop bridge missing");
        const health = await (await fetch(base + "/api/health")).json();
        const files = await Promise.all(["portal", "purchase"].map(async (w) => {
          const r = await fetch(base + "/api/sample/" + w);
          if (!r.ok) throw new Error("sample " + w + " -> HTTP " + r.status);
          return new File([await r.blob()], w + ".xlsx");
        }));
        const form = new FormData();
        form.append("portal_file", files[0]);
        form.append("purchase_file", files[1]);
        form.append("tolerance", "1");
        const r = await fetch(base + "/api/reconcile", { method: "POST", body: form });
        if (!r.ok) throw new Error("reconcile -> HTTP " + r.status + " " + (await r.text()));
        const report = await r.json();
        return {
          origin: location.origin,
          title: document.title,
          health,
          summary: report.summary,
          turnstileRendered: !!document.querySelector(".turnstile"),
          externalScripts: [...document.scripts].map((s) => s.src).filter((s) => /^https?:/.test(s)),
          footerVersion: document.querySelector(".foot-sub")?.textContent.match(/v\\d[\\w.-]*/)?.[0] ?? null,
          updateControl: document.querySelector(".foot-btn")?.textContent ?? null,
        };
      })()`,
      true,
    );
    if (process.env.GST_UPDATE_FAKE_VERSION) {
      result.update = await win.webContents.executeJavaScript(
        `(async () => {
          const u = window.gstDesktop.updates;
          const wait = async (pred, ms) => { for (let i = 0; i < ms / 100; i++) { const s = await u.state(); if (pred(s)) return s; await new Promise((r) => setTimeout(r, 100)); } throw new Error("timed out waiting for update state: " + JSON.stringify(await u.state())); };
          let s = await u.check();
          if (s.state !== "available") throw new Error("expected an available update, got " + JSON.stringify(s));
          await wait(() => !!document.querySelector(".update-banner"), 3000);
          await u.download();
          s = await wait((x) => x.state === "downloaded" || x.state === "error" || (x.state === "available" && x.error), 60000);
          if (s.state !== "downloaded") throw new Error("download did not complete: " + JSON.stringify(s));
          await wait(() => /downloaded/i.test(document.querySelector(".update-banner")?.textContent || ""), 3000);
          return { version: s.version, file: s.file, bytes: s.received ?? null };
        })()`,
        true,
      );
    }
    await new Promise((r) => setTimeout(r, 1200)); // let fonts settle for the screenshot
    const shot = path.join(process.env.GST_SMOKE_TEST_DIR || app.getPath("temp"), "gst-smoke.png");
    fs.writeFileSync(shot, (await win.webContents.capturePage()).toPNG());
    console.log(`SMOKE_OK ${JSON.stringify({ ...result, screenshot: shot })}`);
    quitting = true;
    backend.stop();
    app.exit(0);
  } catch (err) {
    console.error(`SMOKE_FAIL ${(err && err.stack) || err}`);
    quitting = true;
    backend.stop();
    app.exit(1);
  }
}

// --------------------------------------------------------------- lifecycle --

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    app.setAppLogsPath();
    app.setAboutPanelOptions({
      applicationName: pkg.productName,
      applicationVersion: app.getVersion(),
      copyright: COPYRIGHT,
      credits: "Reconciles GSTR-2A/2B against your purchase register, entirely on this computer.",
    });
    protocol.handle(APP_SCHEME, serveRenderer);
    installMenu();
    updates.init({ homepage: pkg.homepage, window: () => mainWindow });

    const port = await backend.pickFreePort();
    apiBase = `http://127.0.0.1:${port}`;

    mainWindow = createWindow();
    await mainWindow.loadFile(LOADING_PAGE);

    backend.onExit(({ code, signal }) => {
      if (quitting) return;
      const choice = dialog.showMessageBoxSync({
        type: "error",
        buttons: ["Restart app", "Quit"],
        defaultId: 0,
        cancelId: 1,
        title: pkg.productName,
        message: "The reconciliation engine stopped unexpectedly.",
        detail: `It exited with code ${code ?? "none"} (signal ${signal ?? "none"}).\n\nLog file: ${backend.logFile()}`,
      });
      quitting = true;
      if (choice === 0) app.relaunch();
      app.exit(1);
    });

    try {
      await backend.start({
        port,
        origin: APP_ORIGIN,
        logDir: app.getPath("logs"),
        isPackaged: app.isPackaged,
        resourcesPath: process.resourcesPath,
        appRoot: path.join(__dirname, ".."),
      });
    } catch (err) {
      quitting = true;
      showFatal(`${pkg.productName} could not start`, err.message);
      backend.stop();
      app.exit(1);
      return;
    }

    if (!mainWindow) return; // closed while the backend was starting
    await mainWindow.loadURL(`${APP_ORIGIN}/`);
    if (process.env.GST_SMOKE_TEST) runSmokeTest(mainWindow);
  }).catch((err) => {
    quitting = true;
    console.error(err);
    showFatal(`${pkg.productName} could not start`, (err && err.stack) || String(err));
    backend.stop();
    app.exit(1);
  });

  app.on("activate", () => {
    // macOS: dock click with no windows open.
    if (BrowserWindow.getAllWindows().length === 0 && apiBase && backend.isRunning()) {
      mainWindow = createWindow();
      mainWindow.loadURL(`${APP_ORIGIN}/`);
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
    backend.stop();
  });
}
