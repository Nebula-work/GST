"""
Entry point for the desktop (Electron) build of the backend.

The Electron shell spawns this on a free loopback port, waits for
/api/health, then points the bundled UI at it. It is the same FastAPI app as
the web deployment (app.main) with defaults tuned for a single trusted user on
127.0.0.1 instead of a public server:

  * no per-client rate limit (RATE_LIMIT_REQUESTS=0),
  * larger upload cap and a longer reconcile budget (big yearly exports),
  * CORS restricted to the app's own origin (Electron passes it in).

Any of these can still be overridden through the environment; Electron sets
CORS_ALLOW_ORIGINS explicitly and the rest fall back to the defaults below.

PyInstaller freezes this file into `gst-backend` (see desktop_server.spec).
It also runs from source for development:

    backend/.venv/bin/python desktop_server.py --port 8011
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

_MB = 1024 * 1024

DESKTOP_DEFAULTS = {
    "RATE_LIMIT_REQUESTS": "0",             # single local user: never 429
    "MAX_UPLOAD_BYTES": str(50 * _MB),      # per file (parser has its own bomb caps)
    "RECONCILE_TIMEOUT_SECONDS": "120",     # yearly exports on a slow laptop
    "MAX_CONCURRENT_RECONCILES": "1",       # one UI, one job at a time
    "CORS_ALLOW_ORIGINS": "app://gst",      # the Electron renderer's origin
}


def _exit_when_parent_closes() -> None:
    """Block on stdin until EOF, then exit.

    Electron keeps our stdin open as a pipe. If the Electron process dies for
    any reason (crash, force-quit, SIGKILL) the OS closes that pipe and we get
    EOF here -- so the backend can never be left running as an orphan.
    """
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        while stream.read(4096):
            pass
    except Exception:  # noqa: BLE001 - any stdin failure means "parent is gone"
        pass
    os._exit(0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="GST reconciliation backend (desktop mode)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--watch-stdin",
        action="store_true",
        help="exit as soon as stdin reaches EOF (i.e. the parent process is gone)",
    )
    args = parser.parse_args(argv)

    # Limits are read from the environment when app.main is imported, so the
    # defaults must be in place *before* the import below.
    for key, value in DESKTOP_DEFAULTS.items():
        os.environ.setdefault(key, value)

    if args.watch_stdin:
        threading.Thread(target=_exit_when_parent_closes, daemon=True).start()

    import uvicorn  # noqa: WPS433 - imported late on purpose (see above)
    from app.main import app  # noqa: WPS433

    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
