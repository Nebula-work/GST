#!/usr/bin/env node
// Freeze the FastAPI backend with PyInstaller into desktop/backend-dist/gst-backend/.
//
// Picks a Python (GST_PYTHON, then backend/.venv, then python3/python), makes
// sure the runtime + build requirements are installed, and runs the spec in
// backend/desktop_server.spec. Must run on the platform you are packaging for.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = path.resolve(desktopDir, "..", "backend");
const distDir = path.join(desktopDir, "backend-dist");
const workDir = path.join(backendDir, "build", "pyinstaller");
const exeName = process.platform === "win32" ? "gst-backend.exe" : "gst-backend";

function run(cmd, args, opts = {}) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) {
    console.error(`\n${cmd} ${args.join(" ")} failed (exit ${r.status ?? r.signal})`);
    process.exit(r.status ?? 1);
  }
}

function findPython() {
  const candidates = [];
  if (process.env.GST_PYTHON) candidates.push(process.env.GST_PYTHON);
  candidates.push(
    process.platform === "win32"
      ? path.join(backendDir, ".venv", "Scripts", "python.exe")
      : path.join(backendDir, ".venv", "bin", "python"),
    "python3",
    "python",
  );
  for (const c of candidates) {
    if (path.isAbsolute(c) && !fs.existsSync(c)) continue;
    const r = spawnSync(c, ["-c", "import sys; print(sys.version_info >= (3, 10))"], { encoding: "utf8" });
    if (r.status === 0 && r.stdout.trim() === "True") return c;
  }
  console.error(
    "No Python 3.10+ found. Create the backend venv (see README) or set GST_PYTHON=/path/to/python.",
  );
  process.exit(1);
}

const python = findPython();
console.log(`Using Python: ${python}`);

const deps = spawnSync(python, ["-c", "import fastapi, uvicorn, openpyxl, multipart, PyInstaller"], {
  encoding: "utf8",
});
if (deps.status !== 0) {
  console.log("Installing backend runtime + build requirements…");
  run(python, ["-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-desktop.txt"], {
    cwd: backendDir,
  });
}

const sample = path.join(backendDir, "sample_data", "sample_gst_portal.xlsx");
if (!fs.existsSync(sample)) run(python, ["scripts/generate_samples.py"], { cwd: backendDir });

fs.rmSync(distDir, { recursive: true, force: true });
run(
  python,
  ["-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", distDir, "--workpath", workDir, "desktop_server.spec"],
  { cwd: backendDir },
);

const exe = path.join(distDir, "gst-backend", exeName);
if (!fs.existsSync(exe)) {
  console.error(`PyInstaller finished but ${exe} is missing.`);
  process.exit(1);
}
console.log(`\nBackend ready: ${exe}`);
