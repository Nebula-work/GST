"""
FastAPI backend for the GST purchase reconciliation tool.

Endpoints
---------
GET  /api/health              - liveness probe
POST /api/reconcile           - upload portal + purchase .xlsx, get the report
GET  /api/sample/{which}      - download a ready-made sample file to try the tool

This service is public and unauthenticated, so it defends itself with: per-file
and combined upload size caps, a per-client rate limit, a concurrency cap, and a
wall-clock timeout around the CPU-bound parse+reconcile (which also runs off the
event loop so one big job can't freeze health checks). The parser and matcher
have their own caps against decompression bombs and algorithmic blow-ups.

Layering note: the *raw request body* is bounded at the edge by nginx
(client_max_body_size) — by the time this handler runs, Starlette has already
read/spooled the multipart body. The in-app rate limit and size caps therefore
gate the expensive parse/reconcile and avoid materialising oversize content in
RAM; they are not a substitute for the nginx body cap. Run behind nginx in prod.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import urllib.parse
import urllib.request
from pathlib import Path

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .limits import ConcurrencyGate, SlidingWindowRateLimiter
from .parser import parse_workbook
from .reconcile import DEFAULT_TOLERANCE, reconcile

logger = logging.getLogger(__name__)

app = FastAPI(title="GST Reconciliation API", version="1.0.0")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _cors_origins() -> list[str]:
    """Allowed CORS origins from the CORS_ALLOW_ORIGINS env var.

    Comma-separated list, e.g.
        CORS_ALLOW_ORIGINS="https://app.example.com,https://www.example.com"
    Unset or "*" allows any origin (handy for local dev).
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


# CORS: the frontend is served from a different origin than this API.
# Configure allowed origins per environment via env vars; a regex is also
# supported for wildcard hosts (e.g. AWS Amplify branch URLs):
#     CORS_ALLOW_ORIGIN_REGEX="https://.*\\.amplifyapp\\.com"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=os.getenv("CORS_ALLOW_ORIGIN_REGEX") or None,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"

# --- abuse / resource limits (all overridable via env) ---------------------
MAX_BYTES = _env_int("MAX_UPLOAD_BYTES", 15 * 1024 * 1024)          # per file
MAX_TOTAL_BYTES = _env_int("MAX_TOTAL_UPLOAD_BYTES", 2 * MAX_BYTES + 1024 * 1024)
RECONCILE_TIMEOUT = _env_float("RECONCILE_TIMEOUT_SECONDS", 30.0)   # wall-clock budget
MAX_CONCURRENT = _env_int("MAX_CONCURRENT_RECONCILES", 2)
RATE_LIMIT_REQUESTS = _env_int("RATE_LIMIT_REQUESTS", 20)           # per window, per client
RATE_LIMIT_WINDOW = _env_float("RATE_LIMIT_WINDOW_SECONDS", 60.0)
MAX_TOLERANCE = _env_float("MAX_TOLERANCE", 1000.0)
# Client identity for rate limiting. We believe forwarded headers (X-Real-IP /
# X-Forwarded-For) ONLY when the direct peer is a trusted proxy — loopback by
# default, since the documented deployment runs nginx on the same host. This is
# both correct AND spoof-safe with no config:
#   * behind nginx (peer 127.0.0.1) -> trust X-Real-IP -> real per-client keying
#     (works on a code-only deploy; no env needed),
#   * exposed directly (peer is the attacker) -> ignore headers, key on the peer
#     -> a client can't forge X-Real-IP to dodge the limiter.
# TRUST_PROXY=true forces trust regardless of peer (e.g. nginx on another host);
# TRUSTED_PROXY_IPS adds non-loopback proxy addresses to the trusted set.
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").strip().lower() in ("1", "true", "yes")
_TRUSTED_PROXY_IPS = {"127.0.0.1", "::1"} | {
    ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
}

# Cloudflare Turnstile (bot/human check). Enabled only when the SECRET is set —
# the public site key lives in the frontend. Leave the secret unset for local
# dev/tests and verification is skipped.
TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

_rate_limiter = SlidingWindowRateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)
_concurrency = ConcurrencyGate(MAX_CONCURRENT)
_MB = 1024 * 1024

# Dedicated limiter for the CPU-bound parse/reconcile. asyncio.wait_for cancels
# the *await* on timeout but not the worker thread (sync CPU can't be
# interrupted), so a timed-out job keeps running and holds its token until it
# truly finishes — capping live reconcile threads to MAX_CONCURRENT even when the
# (fast-503) ConcurrencyGate is released early. Kept separate from the default
# threadpool so quick I/O like the Turnstile call isn't starved by it.
_reconcile_limiter: "anyio.CapacityLimiter | None" = None


@app.on_event("startup")
async def _init_reconcile_limiter() -> None:
    global _reconcile_limiter
    try:
        _reconcile_limiter = anyio.CapacityLimiter(max(1, MAX_CONCURRENT))
    except Exception as exc:  # noqa: BLE001 - fall back to the default limiter
        logger.warning("Could not create reconcile limiter: %r", exc)


def _verify_turnstile(token: str, remote_ip: str) -> bool:
    """Validate a Turnstile token with Cloudflare (synchronous; run off-loop).

    Uses stdlib urllib so the backend gains no new runtime dependency.
    """
    payload = urllib.parse.urlencode(
        {"secret": TURNSTILE_SECRET, "response": token, "remoteip": remote_ip}
    ).encode()
    try:
        req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(json.loads(resp.read().decode()).get("success"))
    except Exception as exc:  # noqa: BLE001 - treat any failure as "not verified"
        logger.warning("Turnstile verification error: %r", exc)
        return False


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _client_ip(request: Request) -> str:
    """Best-effort client identity for rate limiting.

    Trust forwarded headers only from a trusted proxy peer (loopback by default,
    or TRUST_PROXY=true to force): nginx sets X-Real-IP to the real $remote_addr
    and appends the peer to X-Forwarded-For. A directly-connected client is not a
    trusted peer, so its headers are ignored and we key on its real socket IP.
    """
    peer = request.client.host if request.client else ""
    if TRUST_PROXY or peer in _TRUSTED_PROXY_IPS:
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()
    return peer or "unknown"


async def _read_xlsx(upload: UploadFile, label: str, budget_left: int) -> bytes:
    """Read an upload while enforcing the size caps, never buffering an oversize
    body in full. Streams in chunks and aborts as soon as a limit is crossed."""
    name = (upload.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail=f"The {label} file must be an Excel .xlsx file (got "
                   f"'{upload.filename}'). If it is .xls, re-save it as .xlsx.",
        )
    limit = min(MAX_BYTES, budget_left)
    # Fast path: reject before reading when the spooled size is already known.
    size = getattr(upload, "size", None)
    if size is not None and size > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"The {label} file is too large (max {MAX_BYTES // _MB} MB).")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_MB)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"The {label} file is too large (max {MAX_BYTES // _MB} MB).")
        if total > limit:
            raise HTTPException(
                status_code=400,
                detail="The two files together are too large. Please upload "
                       "smaller exports.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail=f"The {label} file is empty.")
    return content


def _reconcile_blocking(portal_bytes: bytes, purchase_bytes: bytes,
                        tolerance: float) -> tuple[dict, dict, dict]:
    """The CPU-bound work, run off the event loop in a worker thread."""
    portal_parsed = parse_workbook(portal_bytes, "portal")
    purchase_parsed = parse_workbook(purchase_bytes, "purchase")
    result = reconcile(
        portal_parsed["records"],
        purchase_parsed["records"],
        tolerance=tolerance,
    )
    return portal_parsed, purchase_parsed, result


@app.post("/api/reconcile")
async def reconcile_endpoint(
    request: Request,
    portal_file: UploadFile = File(..., description="GST portal export (GSTR-2A/2B)"),
    purchase_file: UploadFile = File(..., description="Your purchase register"),
    tolerance: float = Form(DEFAULT_TOLERANCE),
    turnstile_token: str = Form("", alias="cf-turnstile-response"),
):
    # 1) Cheapest rejection first: per-client rate limit.
    client = _client_ip(request)
    if not _rate_limiter.allow(client):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a few seconds and try again.")

    # 1b) Cloudflare Turnstile (only enforced when the secret is configured).
    if TURNSTILE_SECRET:
        if not turnstile_token:
            raise HTTPException(
                status_code=403,
                detail="Please complete the verification check and try again.")
        if not await run_in_threadpool(_verify_turnstile, turnstile_token, client):
            raise HTTPException(
                status_code=403,
                detail="Verification failed. Please complete the check again.")

    # 2) Validate tolerance: reject non-finite, clamp to a sane range. An
    #    unbounded value would otherwise mark everything "matched" or 500 the
    #    JSON response (NaN/Inf aren't JSON-serialisable).
    if not math.isfinite(tolerance) or tolerance < 0:
        raise HTTPException(
            status_code=400,
            detail="tolerance must be a finite number greater than or equal to 0.")
    tolerance = min(tolerance, MAX_TOLERANCE)

    # 3) Read uploads with streamed size enforcement (per-file + combined).
    portal_bytes = await _read_xlsx(portal_file, "GST portal", MAX_TOTAL_BYTES)
    purchase_bytes = await _read_xlsx(
        purchase_file, "purchase register", MAX_TOTAL_BYTES - len(portal_bytes))

    # 4) Bound concurrent heavy jobs; do the CPU work off the event loop under a
    #    hard wall-clock timeout so it can't pin the worker or freeze /api/health.
    if not _concurrency.try_acquire():
        raise HTTPException(
            status_code=503,
            detail="The server is busy reconciling other files. Please retry in "
                   "a few seconds.")
    try:
        portal_parsed, purchase_parsed, result = await asyncio.wait_for(
            anyio.to_thread.run_sync(_reconcile_blocking, portal_bytes,
                                     purchase_bytes, tolerance,
                                     limiter=_reconcile_limiter),
            timeout=RECONCILE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Reconcile exceeded %.0fs budget; returning 503.", RECONCILE_TIMEOUT)
        raise HTTPException(
            status_code=503,
            detail="Reconciliation took too long. Please try again with smaller "
                   "files.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - clean 500, detail logged not leaked
        logger.exception("Unexpected error reconciling files")
        raise HTTPException(
            status_code=500,
            detail="Could not process the files. Please check they are valid "
                   ".xlsx exports and try again.") from exc
    finally:
        _concurrency.release()

    # meta.warnings is the single source the frontend reads; drop the internal
    # top-level copy reconcile() returned so it isn't serialised twice.
    warnings = (portal_parsed.get("warnings", [])
                + purchase_parsed.get("warnings", [])
                + result.pop("warnings", []))
    result["meta"] = {
        "portal_file": portal_file.filename,
        "purchase_file": purchase_file.filename,
        "tolerance": tolerance,
        "warnings": warnings,
        "portal": {
            "sheet": portal_parsed["sheet"],
            "rows": portal_parsed["row_count"],
            "detected_columns": portal_parsed["detected_columns"],
        },
        "purchase": {
            "sheet": purchase_parsed["sheet"],
            "rows": purchase_parsed["row_count"],
            "detected_columns": purchase_parsed["detected_columns"],
        },
    }
    return result


@app.get("/api/sample/{which}")
def sample(which: str):
    files = {
        "portal": "sample_gst_portal.xlsx",
        "purchase": "sample_purchase_register.xlsx",
    }
    if which not in files:
        raise HTTPException(status_code=404, detail="Unknown sample.")
    path = SAMPLE_DIR / files[which]
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Sample files not generated yet. Run "
                   "`python -m scripts.generate_samples` in the backend folder.",
        )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=files[which],
    )
