"use strict";
/**
 * Lifecycle of the bundled Python backend (the FastAPI app frozen by
 * PyInstaller into `gst-backend`).
 *
 *   start()  -> spawn it on a free loopback port and wait for /api/health
 *   stop()   -> ask it to exit (SIGTERM + closing its stdin)
 *
 * In development, when no frozen build exists under desktop/backend-dist/,
 * it falls back to running backend/desktop_server.py with the backend venv.
 */
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const EXE_NAME = process.platform === "win32" ? "gst-backend.exe" : "gst-backend";
const START_TIMEOUT_MS = 60_000; // first launch on Windows can be slow (AV scan)
const POLL_INTERVAL_MS = 250;
const MAX_LOG_BYTES = 5 * 1024 * 1024;
const TAIL_LINES = 40;

let child = null;
let logPath = null;
let logStream = null;
let stopping = false;
let onUnexpectedExit = null;
const tail = []; // last few log lines, shown in error dialogs

/** Ask the OS for a free TCP port on loopback. */
function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function resolveLaunch({ isPackaged, resourcesPath, appRoot }) {
  if (isPackaged) {
    const cmd = path.join(resourcesPath, "backend", EXE_NAME);
    return { kind: "frozen", cmd, args: [], cwd: path.dirname(cmd) };
  }
  const frozen = path.join(appRoot, "backend-dist", "gst-backend", EXE_NAME);
  if (fs.existsSync(frozen)) {
    return { kind: "frozen", cmd: frozen, args: [], cwd: path.dirname(frozen) };
  }
  // Dev fallback: run from source with the backend's virtualenv.
  const srcDir = path.resolve(appRoot, "..", "backend");
  const venvPython =
    process.platform === "win32"
      ? path.join(srcDir, ".venv", "Scripts", "python.exe")
      : path.join(srcDir, ".venv", "bin", "python");
  const python =
    process.env.GST_PYTHON ||
    (fs.existsSync(venvPython) ? venvPython : process.platform === "win32" ? "python" : "python3");
  return {
    kind: "source",
    cmd: python,
    args: [path.join(srcDir, "desktop_server.py")],
    cwd: srcDir,
  };
}

function openLog(logDir) {
  fs.mkdirSync(logDir, { recursive: true });
  logPath = path.join(logDir, "backend.log");
  try {
    if (fs.statSync(logPath).size > MAX_LOG_BYTES) fs.truncateSync(logPath, 0);
  } catch {
    /* no log yet */
  }
  logStream = fs.createWriteStream(logPath, { flags: "a" });
  logStream.on("error", () => {});
}

function record(chunk) {
  const text = chunk.toString();
  if (logStream) logStream.write(text);
  for (const line of text.split(/\r?\n/)) {
    if (!line) continue;
    tail.push(line);
    if (tail.length > TAIL_LINES) tail.shift();
  }
  if (process.env.GST_DEBUG || process.env.GST_SMOKE_TEST) process.stderr.write(text);
}

function describeExit(exit) {
  if (!exit) return "";
  if (exit.error) return `could not be started: ${exit.error.message}`;
  return `exited early (code ${exit.code ?? "none"}, signal ${exit.signal ?? "none"})`;
}

async function waitForHealth(url, isDead) {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const dead = isDead();
    if (dead) {
      throw new Error(
        `The reconciliation engine ${describeExit(dead)}.\n\n${tail.slice(-12).join("\n")}`,
      );
    }
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new Error(
    `The reconciliation engine did not answer within ${START_TIMEOUT_MS / 1000}s.\n\n` +
      tail.slice(-12).join("\n"),
  );
}

/**
 * @param {object} o
 * @param {number} o.port         loopback port to listen on
 * @param {string} o.origin       the renderer's origin, allowed for CORS
 * @param {string} o.logDir       where backend.log goes
 * @param {boolean} o.isPackaged  app.isPackaged
 * @param {string} o.resourcesPath process.resourcesPath
 * @param {string} o.appRoot      the desktop/ folder (or app.asar)
 */
async function start({ port, origin, logDir, isPackaged, resourcesPath, appRoot }) {
  const launch = resolveLaunch({ isPackaged, resourcesPath, appRoot });
  openLog(logDir);
  logStream.write(
    `\n=== ${new Date().toISOString()} starting ${launch.kind} backend on :${port}\n` +
      `=== ${launch.cmd} ${launch.args.join(" ")}\n`,
  );

  const env = {
    ...process.env,
    CORS_ALLOW_ORIGINS: origin,
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
  };
  stopping = false;
  child = spawn(
    launch.cmd,
    [...launch.args, "--host", "127.0.0.1", "--port", String(port), "--watch-stdin"],
    { cwd: launch.cwd, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true },
  );

  let exit = null;
  const proc = child;
  proc.stdin.on("error", () => {}); // EPIPE once the child is gone
  proc.stdout.on("data", record);
  proc.stderr.on("data", record);
  proc.on("error", (error) => {
    exit = { error };
    record(`spawn error: ${error.message}\n`);
  });
  proc.on("exit", (code, signal) => {
    exit = { code, signal };
    record(`backend exited (code ${code}, signal ${signal})\n`);
    if (child === proc) child = null;
    if (!stopping && onUnexpectedExit) onUnexpectedExit({ code, signal });
  });

  await waitForHealth(`http://127.0.0.1:${port}/api/health`, () => exit);
  return launch;
}

function stop() {
  stopping = true;
  const proc = child;
  if (!proc) return;
  child = null;
  try {
    proc.stdin.end(); // desktop_server.py exits on stdin EOF (belt)
  } catch {
    /* ignore */
  }
  try {
    proc.kill("SIGTERM"); // uvicorn shuts down gracefully (braces)
  } catch {
    /* ignore */
  }
}

module.exports = {
  pickFreePort,
  start,
  stop,
  isRunning: () => child !== null,
  logFile: () => logPath,
  onExit: (fn) => {
    onUnexpectedExit = fn;
  },
};
