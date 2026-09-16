#!/usr/bin/env node
// Build the Next.js static export in desktop mode and copy it to desktop/renderer/.
//
// Desktop mode (NEXT_PUBLIC_DESKTOP=1) drops the Cloudflare bot check and the
// analytics beacon, and lets the UI take the backend URL from the Electron
// shell at runtime (see frontend/lib/desktop.ts). Note this rebuilds
// frontend/out in that flavour; the web deploy pipeline rebuilds its own.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendDir = path.resolve(desktopDir, "..", "frontend");
const outDir = path.join(frontendDir, "out");
const rendererDir = path.join(desktopDir, "renderer");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";

function run(cmd, args, opts = {}) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { stdio: "inherit", shell: process.platform === "win32", ...opts });
  if (r.status !== 0) {
    console.error(`\n${cmd} ${args.join(" ")} failed (exit ${r.status ?? r.signal})`);
    process.exit(r.status ?? 1);
  }
}

if (!fs.existsSync(path.join(frontendDir, "node_modules"))) {
  run(npm, ["ci"], { cwd: frontendDir });
}

run(npm, ["run", "build"], {
  cwd: frontendDir,
  env: {
    ...process.env,
    NEXT_PUBLIC_DESKTOP: "1",
    NEXT_PUBLIC_API_BASE: "",          // runtime bridge supplies the real one
    NEXT_PUBLIC_TURNSTILE_SITE_KEY: "", // no bot check against a local backend
    NEXT_TELEMETRY_DISABLED: "1",
  },
});

if (!fs.existsSync(path.join(outDir, "index.html"))) {
  console.error(`Expected a static export at ${outDir} (is next.config.mjs still output: "export"?)`);
  process.exit(1);
}

fs.rmSync(rendererDir, { recursive: true, force: true });
fs.cpSync(outDir, rendererDir, { recursive: true });
console.log(`\nRenderer ready: ${rendererDir}`);
