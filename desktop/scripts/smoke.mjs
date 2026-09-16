#!/usr/bin/env node
// End-to-end smoke test: launches the app with GST_SMOKE_TEST=1, which makes
// main.js drive the real UI origin (bridge -> health -> samples -> reconcile),
// save a screenshot, print SMOKE_OK/SMOKE_FAIL and exit.
//
//   node scripts/smoke.mjs                      # dev: electron . (built renderer + backend)
//   node scripts/smoke.mjs <path-to-packaged-exe>  # a packaged build from electron-builder
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

//   node scripts/smoke.mjs <exe> --no-sandbox     # extra args go to the app (Linux CI)
const [target, ...extraArgs] = process.argv.slice(2);
const cmd = target || require("electron"); // the dev Electron binary path
const args = [...(target ? [] : [desktopDir]), ...extraArgs];

const child = spawn(cmd, args, {
  env: { ...process.env, GST_SMOKE_TEST: "1" }, // screenshot -> GST_SMOKE_TEST_DIR or the OS temp dir
  stdio: ["ignore", "pipe", "pipe"],
});

let out = "";
const timer = setTimeout(() => {
  console.error("SMOKE_FAIL timed out after 120s");
  child.kill();
  process.exit(1);
}, 120_000);

for (const stream of [child.stdout, child.stderr]) {
  stream.on("data", (d) => {
    const s = d.toString();
    out += s;
    process.stderr.write(s);
  });
}
child.on("exit", (code) => {
  clearTimeout(timer);
  const ok = code === 0 && out.includes("SMOKE_OK");
  console.log(ok ? "\nSmoke test passed." : `\nSmoke test FAILED (exit ${code}).`);
  process.exit(ok ? 0 : 1);
});
