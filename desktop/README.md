# GST Purchase Matcher — desktop app

A self-contained desktop version of the reconciliation tool. Users install
nothing else: the app bundles the web UI and a frozen copy of the Python
backend, runs the backend on a random `127.0.0.1` port when it starts, and
stops it when it quits. Files never leave the machine. The only network
request is a check for a newer GitHub Release (see *Updates* below); offline
it is skipped.

```
┌─ Electron shell (main/) ──────────────────────────────────────────────┐
│  app://gst/  ← serves renderer/ (Next.js static export, desktop mode)  │
│  spawns  →  resources/backend/gst-backend --port <free>  (PyInstaller) │
│  preload →  window.gstDesktop.apiBase = http://127.0.0.1:<free>        │
└────────────────────────────────────────────────────────────────────────┘
```

| Piece | Source | Built into |
| --- | --- | --- |
| UI | `frontend/` (`NEXT_PUBLIC_DESKTOP=1` static export) | `renderer/` |
| Backend | `backend/desktop_server.py` + `desktop_server.spec` | `backend-dist/gst-backend/` |
| Shell | `main/` (main process, preload, menu, loading page) | the app itself |

## Build an installer

Needs Node 20+ and Python 3.10+ (the backend venv from the main README is
picked up automatically; otherwise set `GST_PYTHON=/path/to/python`).

```bash
cd desktop
npm install
npm run dist          # -> dist/  (installer for the OS you are on)
```

Platform-specific: `npm run dist:win`, `dist:mac`, `dist:linux`. The frozen
backend cannot be cross-compiled, so **build on the OS you are targeting**.
For Windows builds from a Mac, use the GitHub Actions workflow
(`.github/workflows/desktop.yml`): run it from the *Actions* tab, or push a
`v*` tag and it attaches the installers to a GitHub Release.

Outputs:

| OS | Files | Notes |
| --- | --- | --- |
| Windows | `GST Purchase Matcher-<v>-Setup.exe`, `…-Portable.exe` | Setup installs per-user, no admin. Portable runs from anywhere (slower first start: it unpacks to a temp folder). Unsigned: SmartScreen shows *More info → Run anyway* once. |
| macOS | `.dmg`, `.zip` | Unsigned/un-notarized: right-click → Open the first time. |
| Linux | `.AppImage`, `.deb` | |

## Develop

```bash
npm run build:frontend   # rebuild renderer/ after UI changes
npm run build:backend    # re-freeze after backend changes (optional in dev)
npm start                # run the app
```

Without a frozen backend in `backend-dist/`, `npm start` runs
`backend/desktop_server.py` from source with `backend/.venv`, so backend
changes are picked up on restart without re-freezing. `GST_DEBUG=1` echoes
the backend log to the terminal.

`npm run smoke` launches the app with `GST_SMOKE_TEST=1`, which drives the
real UI origin (bridge → health → sample files → full reconcile), saves
`gst-smoke.png` and exits 0/1. `node scripts/smoke.mjs <path-to-exe>` does
the same against a packaged build; CI runs it on every installer.

## How the pieces fit

- **Backend** — `desktop_server.py` wraps the unchanged FastAPI app with
  desktop defaults (no rate limit, 50 MB uploads, 120 s budget, CORS locked
  to `app://gst`). `--watch-stdin` makes it exit the moment Electron's pipe
  closes, so a crashed shell never leaves an orphan process.
- **UI** — `frontend/lib/desktop.ts` holds the build-time `IS_DESKTOP` flag
  (drops the Turnstile bot check and the Cloudflare beacon) and the runtime
  bridge (`window.gstDesktop`) that supplies the backend URL.
- **Shell** — `main/main.js` serves `renderer/` from a custom `app://gst`
  origin (absolute `/_next/…` paths and `/privacy` routing work as on a web
  host), adds a CSP, sandboxes the renderer, and opens `mailto:`/`https:`
  links in the system browser. `main/backend.js` owns the child process.

## Updates

There is no auto-updater (builds are unsigned). Instead, on launch and every
six hours, `main/updates.js` asks `api.github.com` for the repo's latest
release (repo taken from `homepage` in package.json). If its tag is newer
than the running version, a banner appears at the top of the window:
**Download update** fetches the matching asset into the user's Downloads
folder with a progress bar (Windows: `…-Setup.exe`, or `…-Portable.exe` when
running the portable build; macOS: the `.dmg` for the CPU; Linux: `.AppImage`
or `.deb`), then **Install now** launches the installer and quits the app on
Windows, or reveals the file on other platforms. The footer shows the version
and a manual **Check for updates**.

To release an update: bump `version` in `desktop/package.json`, merge, and
publish a GitHub Release tagged `v<version>`; the workflow attaches the
installers and every running copy will offer it on its next check.

Dev knobs (ignored in packaged builds): `GST_UPDATE_FAKE_VERSION=0.9.0`
makes the app believe it is older than the latest release, and
`GST_UPDATE_FAKE_ASSET=<url>` substitutes the download URL, so
`npm run smoke` can drive the whole banner → download → downloaded flow.

## Troubleshooting

- **"could not start" dialog** — the backend log is at the path shown:
  `%APPDATA%\GST Purchase Matcher\logs\` on Windows,
  `~/Library/Logs/GST Purchase Matcher/` on macOS. Press **F12** for DevTools.
- **Windows Defender flags the app** — PyInstaller binaries are a common
  false positive. The one-folder build used here trips it far less than
  one-file builds; code signing removes it entirely.
- **Slow first launch** — the OS scans new executables once; later launches
  take ~1 s.
