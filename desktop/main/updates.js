"use strict";
/**
 * "A newer version exists" check against GitHub Releases, plus an in-app
 * download of the matching installer.
 *
 * There is no auto-update framework: builds are unsigned and handed out by
 * hand, so this only tells the user a newer release exists and fetches the
 * right file for them. The check is one HTTPS GET to api.github.com -- the
 * only network request the desktop app ever makes -- and carries nothing
 * about the user or their files.
 *
 * State, mirrored to the renderer on the "gst:update" channel:
 *   idle -> checking -> latest | available -> downloading -> downloaded
 *                    \-> error   (available again if a download fails)
 */
const { app, ipcMain, session, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const CHECK_DELAY_MS = 3_000;               // after the window is up
const CHECK_INTERVAL_MS = 6 * 60 * 60_000;  // while the app stays open
const FETCH_TIMEOUT_MS = 10_000;

let repo = null;       // "owner/name", from package.json homepage
let getWindow = null;  // () => BrowserWindow | null
let pending = null;    // download we asked for but will-download hasn't claimed yet
let state = { state: "idle", currentVersion: "" };

function parseRepo(homepage) {
  const m = /github\.com\/([^/]+)\/([^/#?]+)/i.exec(homepage || "");
  return m ? `${m[1]}/${m[2].replace(/\.git$/, "")}` : null;
}

/** Numeric compare of "1.2.3"-style versions; > 0 when a is newer than b. */
function compareVersions(a, b) {
  const parts = (v) =>
    String(v).replace(/^v/i, "").split("-")[0].split(".").map((x) => parseInt(x, 10) || 0);
  const [pa, pb] = [parts(a), parts(b)];
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
}

/** The release asset for this OS, CPU and install flavour (GitHub replaces
 *  spaces in asset names with dots, so match on the suffix only). */
function pickAsset(assets) {
  const find = (...patterns) => {
    for (const p of patterns) {
      const hit = assets.find((a) => p.test(a.name));
      if (hit) return hit;
    }
    return null;
  };
  switch (process.platform) {
    case "win32":
      return process.env.PORTABLE_EXECUTABLE_FILE
        ? find(/-Portable\.exe$/i, /\.exe$/i)
        : find(/-Setup\.exe$/i, /\.exe$/i);
    case "darwin":
      return find(new RegExp(`-${process.arch}\\.dmg$`, "i"), /\.dmg$/i, /\.zip$/i);
    default:
      return process.env.APPIMAGE ? find(/\.AppImage$/i, /\.deb$/i) : find(/\.deb$/i, /\.AppImage$/i);
  }
}

function currentVersion() {
  // Dev-only knob: pretend to be an older version so the "update available"
  // path can be exercised without cutting a release (see scripts/smoke.mjs).
  if (!app.isPackaged && process.env.GST_UPDATE_FAKE_VERSION) return process.env.GST_UPDATE_FAKE_VERSION;
  return app.getVersion();
}

function setState(next) {
  state = { ...next, currentVersion: currentVersion() };
  const win = getWindow && getWindow();
  if (win && !win.isDestroyed()) win.webContents.send("gst:update", state);
  return state;
}

async function check() {
  if (!repo) return setState({ state: "error", error: "No GitHub repository configured." });
  if (state.state === "downloading" || state.state === "downloaded") return state; // leave a download alone
  setState({ state: "checking" });
  try {
    const res = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": `gst-purchase-matcher/${app.getVersion()}`,
      },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`GitHub answered HTTP ${res.status}`);
    const release = await res.json();
    const version = String(release.tag_name || "").replace(/^v/i, "");
    if (!version || compareVersions(version, currentVersion()) <= 0) return setState({ state: "latest" });

    const asset = pickAsset(release.assets || []);
    const fakeAsset = !app.isPackaged && process.env.GST_UPDATE_FAKE_ASSET; // dev-only, see currentVersion()
    return setState({
      state: "available",
      version,
      notesUrl: release.html_url,
      assetUrl: fakeAsset || (asset ? asset.browser_download_url : null),
      assetName: fakeAsset ? path.basename(new URL(fakeAsset).pathname) : asset ? asset.name : null,
    });
  } catch (err) {
    return setState({ state: "error", error: (err && err.message) || String(err) });
  }
}

function uniquePath(dir, name) {
  const ext = path.extname(name);
  const stem = ext ? name.slice(0, -ext.length) : name;
  let candidate = path.join(dir, name);
  for (let i = 2; fs.existsSync(candidate); i++) candidate = path.join(dir, `${stem} (${i})${ext}`);
  return candidate;
}

function download() {
  const win = getWindow && getWindow();
  if (state.state !== "available" || !win) return state;
  if (!state.assetUrl) {
    // Nothing built for this platform in that release: send them to the page.
    shell.openExternal(state.notesUrl);
    return state;
  }
  pending = { url: state.assetUrl, version: state.version, notesUrl: state.notesUrl, assetName: state.assetName };
  setState({ ...state, state: "downloading", received: 0, total: 0, error: undefined });
  win.webContents.downloadURL(state.assetUrl);
  return state;
}

function onWillDownload(_event, item) {
  // Claim only our own download; the CSV export keeps Electron's save dialog.
  if (!pending || item.getURLChain()[0] !== pending.url) return;
  const info = pending;
  pending = null;
  const file = uniquePath(app.getPath("downloads"), info.assetName || item.getFilename());
  item.setSavePath(file);
  item.on("updated", (_e, status) => {
    if (status === "progressing") {
      setState({ ...state, state: "downloading", received: item.getReceivedBytes(), total: item.getTotalBytes() });
    }
  });
  item.once("done", (_e, status) => {
    if (status === "completed") {
      setState({ state: "downloaded", version: info.version, notesUrl: info.notesUrl, assetName: info.assetName, file });
    } else {
      setState({
        state: "available",
        version: info.version,
        notesUrl: info.notesUrl,
        assetUrl: info.url,
        assetName: info.assetName,
        error: status === "cancelled" ? "Download cancelled." : "Download failed. Please try again.",
      });
    }
  });
}

async function install() {
  if (state.state !== "downloaded") return state;
  if (process.platform === "win32" && /Setup\.exe$/i.test(state.assetName || "")) {
    // Launch the installer, then get out of its way: it replaces this install.
    const err = await shell.openPath(state.file);
    if (err) return setState({ ...state, error: err });
    setTimeout(() => app.quit(), 300);
  } else {
    // Portable exe, dmg, AppImage, deb: the user swaps/installs it by hand.
    shell.showItemInFolder(state.file);
  }
  return state;
}

/**
 * @param {object} o
 * @param {string} o.homepage  package.json homepage (the GitHub repo URL)
 * @param {() => import("electron").BrowserWindow | null} o.window
 */
function init({ homepage, window }) {
  repo = parseRepo(homepage);
  getWindow = window;
  setState({ state: "idle" });
  session.defaultSession.on("will-download", onWillDownload);
  ipcMain.handle("gst:update-state", () => state);
  ipcMain.handle("gst:update-check", () => check());
  ipcMain.handle("gst:update-download", () => download());
  ipcMain.handle("gst:update-install", () => install());
  setTimeout(check, CHECK_DELAY_MS);
  setInterval(check, CHECK_INTERVAL_MS).unref();
}

module.exports = { init, check, compareVersions, pickAsset };
