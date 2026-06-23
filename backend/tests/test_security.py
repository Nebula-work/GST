"""Security / abuse-resistance regression tests for the public reconcile API.

These pin the hardening added after a security audit of the (login-less) API:
  * parser: decompression-bomb guard + normalised-key / cell-text length caps
  * reconcile: bounded fuzzy matcher (work budget) + per-key duplicate cap
  * limits: per-client rate limiter + concurrency gate
  * endpoint: streamed size caps, tolerance validation/clamping, rate-limit 429,
    concurrency 503, zip-bomb 422 with no internal detail leak

Run directly (needs httpx for the HTTP-level tests):
    python -m tests.test_security
or under pytest:
    pytest backend/tests/test_security.py
"""

from __future__ import annotations

import io
import os
import sys
import zipfile

from openpyxl import Workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import parser as parser_mod  # noqa: E402
from app import reconcile as reconcile_mod  # noqa: E402
from app.limits import ConcurrencyGate, SlidingWindowRateLimiter  # noqa: E402
from app.parser import parse_workbook, norm_gstin, norm_invoice  # noqa: E402
from app.reconcile import reconcile  # noqa: E402

try:
    from fastapi.testclient import TestClient
    import app.main as main_mod
    _HAVE_CLIENT = True
except Exception as _exc:  # pragma: no cover - httpx not installed
    _HAVE_CLIENT = False
    _CLIENT_ERR = _exc


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _valid_xlsx(rows: list[list]) -> bytes:
    """A minimal single-header-row workbook the parser accepts."""
    wb = Workbook()
    ws = wb.active
    ws.append(["GSTIN", "Supplier Name", "Invoice No", "Invoice Date",
               "Taxable Value", "IGST", "CGST", "SGST", "Invoice Value"])
    for r in rows:
        ws.append(r)
    return _bytes(wb)


def _one_row(gstin="19AAAAA1111A1Z1", inv="INV1", taxable=1000, igst=180):
    return _valid_xlsx([[gstin, "Alpha", inv, "02-04-2026", taxable, igst, 0, 0,
                         taxable + igst]])


def _zip_bomb(uncompressed_mb: int = 40) -> bytes:
    """A valid zip whose member decompresses far beyond its compressed size."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", b"A" * (uncompressed_mb * 1024 * 1024))
    return buf.getvalue()


def _mkrec(source, i, gstin, inv, taxable=100.0):
    return {"id": f"{source}:{i}", "source": source, "row_no": i, "sheet": "S",
            "gstin": gstin, "gstin_norm": gstin, "supplier_name": "X",
            "invoice_no": inv, "invoice_norm": inv, "invoice_date": None,
            "_date_obj": None, "taxable_value": taxable, "igst": 18.0,
            "cgst": 0.0, "sgst": 0.0, "cess": 0.0, "total_tax": 18.0,
            "invoice_value": taxable + 18.0, "rate": 18.0}


# --- parser hardening --------------------------------------------------------

def test_norm_keys_are_length_capped():
    assert len(norm_invoice("INV-" + "A" * 50_000)) == parser_mod.MAX_INVOICE_KEY
    assert len(norm_gstin("X" * 50_000)) == parser_mod.MAX_GSTIN_KEY


def test_cell_text_is_clipped():
    huge = "B" * 100_000
    parsed = parse_workbook(_valid_xlsx([
        ["19AAAAA1111A1Z1", huge, "INV1", "02-04-2026", 1000, 180, 0, 0, 1180]
    ]), "portal")
    assert len(parsed["records"][0]["supplier_name"]) <= parser_mod.MAX_CELL_CHARS


def test_zip_bomb_is_rejected():
    try:
        parse_workbook(_zip_bomb(40), "portal")
    except ValueError as exc:
        assert "unsafe" in str(exc).lower()
        return
    raise AssertionError("zip bomb was not rejected")


def test_normal_xlsx_passes_bomb_guard():
    # A legitimate file must not be mistaken for a bomb.
    parsed = parse_workbook(_one_row(), "portal")
    assert parsed["row_count"] == 1


# --- matcher bounds ----------------------------------------------------------

def test_fuzzy_work_budget_truncates_and_warns(monkeypatch):  # noqa: ARG001
    monkeypatch.setattr(reconcile_mod, "FUZZY_WORK_BUDGET", 10)
    # Same GSTIN, distinct invoice numbers -> all go to the fuzzy pass.
    portal = [_mkrec("portal", i, "27AAAAA0000A1Z5", f"P{i:05d}") for i in range(200)]
    purchase = [_mkrec("purchase", i, "27AAAAA0000A1Z5", f"Q{i:05d}") for i in range(200)]
    res = reconcile(portal, purchase)
    assert res.get("warnings"), "expected a truncation warning when budget is tiny"


def test_no_warning_on_small_clean_input():
    portal = [_mkrec("portal", 0, "27AAAAA0000A1Z5", "INV1")]
    purchase = [_mkrec("purchase", 0, "27AAAAA0000A1Z5", "INV1")]
    res = reconcile(portal, purchase)
    assert not res.get("warnings")
    assert res["summary"]["matched_count"] == 1


def test_cross_gstin_typo_still_matches_after_bucketing():
    """A GSTIN-typo pair (different GSTIN, ~0.9-similar invoice, equal amount)
    must still pair as 'probable' even when the invoice numbers fall in
    different invoice-blocks — the amount bucket has to catch it. Regression for
    the pass-2 bucketing that previously dropped these to only_in_*."""
    cases = [
        ("A1234567890", "B1234567890"),          # differs in the first char
        ("20240000001234567", "2024000001234567"),  # off-by-one length
    ]
    for p_inv, c_inv in cases:
        portal = [_mkrec("portal", 0, "19AAAAA1111A1Z1", p_inv, taxable=1000.0)]
        purchase = [_mkrec("purchase", 0, "19AAAAA1111A1Z9", c_inv, taxable=1000.0)]
        res = reconcile(portal, purchase)
        s = res["summary"]
        assert s["only_in_portal_count"] == 0 and s["only_in_purchase_count"] == 0, \
            f"{p_inv} vs {c_inv} was not paired: {s}"
        # Different GSTIN -> a material diff -> lands in 'mismatched'.
        assert s["matched_count"] + s["mismatch_count"] == 1


def test_per_key_duplicate_cap():
    n = 200  # > MAX_ROWS_PER_KEY on both sides, all identical key
    portal = [_mkrec("portal", i, "27AAAAA0000A1Z5", "INV1") for i in range(n)]
    purchase = [_mkrec("purchase", i, "27AAAAA0000A1Z5", "INV1") for i in range(n)]
    res = reconcile(portal, purchase)
    s = res["summary"]
    assert s["matched_count"] == reconcile_mod.MAX_ROWS_PER_KEY
    assert s["duplicate_count"] > 0  # the overflow rows are flagged as duplicates


# --- limits primitives -------------------------------------------------------

def test_rate_limiter_window():
    rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=10)
    assert [rl.allow("ip", now=t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert rl.allow("ip", now=11) is True   # first hit aged out of the window
    assert rl.allow("other", now=0) is True  # independent key


def test_rate_limiter_disabled_and_key_bound():
    assert SlidingWindowRateLimiter(0, 60).allow("x") is True
    rl = SlidingWindowRateLimiter(1, 60, max_keys=5)
    for i in range(50):
        rl.allow(f"ip{i}", now=0)
    assert len(rl._hits) <= 5


def test_concurrency_gate():
    g = ConcurrencyGate(2)
    assert g.try_acquire() and g.try_acquire()
    assert g.try_acquire() is False
    g.release()
    assert g.try_acquire() is True


def test_client_ip_proxy_aware():
    """Behind a loopback proxy, key on the forwarded real IP (no env needed);
    when the peer is NOT a trusted proxy, ignore spoofable headers."""
    if not _HAVE_CLIENT:
        return

    class _H:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}

        def get(self, k, default=None):
            return self._d.get(k.lower(), default)

    class _Req:
        def __init__(self, peer, headers):
            self.client = type("C", (), {"host": peer})()
            self.headers = _H(headers)

    ci = main_mod._client_ip
    # loopback nginx peer -> trust X-Real-IP (real per-client keying)
    assert ci(_Req("127.0.0.1", {"X-Real-IP": "1.2.3.4"})) == "1.2.3.4"
    # directly-exposed: untrusted peer -> ignore spoofed header, use the peer
    assert ci(_Req("203.0.113.9", {"X-Real-IP": "1.2.3.4"})) == "203.0.113.9"
    # XFF fallback takes the last (nginx-appended) hop
    assert ci(_Req("127.0.0.1", {"X-Forwarded-For": "9.9.9.9, 8.8.8.8"})) == "8.8.8.8"


# --- endpoint behaviour (needs httpx) ---------------------------------------

def _client() -> "TestClient":
    # Fresh, permissive limits per test so unrelated requests don't trip 429,
    # and so a prior test's full gate can't leak.
    main_mod._rate_limiter = SlidingWindowRateLimiter(100_000, 60)
    main_mod._concurrency = ConcurrencyGate(main_mod.MAX_CONCURRENT)
    return TestClient(main_mod.app)


def _files(portal: bytes, purchase: bytes) -> dict:
    return {
        "portal_file": ("portal.xlsx", portal, XLSX_MIME),
        "purchase_file": ("purchase.xlsx", purchase, XLSX_MIME),
    }


def test_endpoint_valid_reconcile_200():
    c = _client()
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["matched_count"] == 1
    assert body["meta"]["tolerance"] == 1.0


def test_endpoint_rejects_non_xlsx():
    c = _client()
    files = {"portal_file": ("evil.exe", b"MZ...", "application/octet-stream"),
             "purchase_file": ("purchase.xlsx", _one_row(), XLSX_MIME)}
    r = c.post("/api/reconcile", files=files, data={"tolerance": "1.0"})
    assert r.status_code == 400 and "Excel" in r.json()["detail"]


def test_endpoint_rejects_empty_file():
    c = _client()
    r = c.post("/api/reconcile", files=_files(b"", _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 400


def test_endpoint_rejects_oversize_file(monkeypatch):
    c = _client()
    monkeypatch.setattr(main_mod, "MAX_BYTES", 1024)  # 1 KB cap for the test
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()


def test_endpoint_tolerance_inf_and_nan_rejected():
    c = _client()
    for bad in ("inf", "-inf", "nan"):
        r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
                   data={"tolerance": bad})
        assert r.status_code == 400, f"{bad} -> {r.status_code}"


def test_endpoint_tolerance_negative_rejected():
    c = _client()
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "-5"})
    assert r.status_code == 400


def test_endpoint_tolerance_huge_is_clamped_not_blindly_matched():
    c = _client()
    # Files differ by 1,000,000 in taxable value. A clamped tolerance must NOT
    # silently call this a match (the pre-fix 1e308 bug did exactly that).
    portal = _one_row(taxable=1000)
    purchase = _one_row(taxable=1_001_000)
    r = c.post("/api/reconcile", files=_files(portal, purchase),
               data={"tolerance": "1e308"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["tolerance"] == main_mod.MAX_TOLERANCE
    assert body["summary"]["matched_count"] == 0  # the big diff is still caught


def test_endpoint_zip_bomb_422_no_leak():
    c = _client()
    r = c.post("/api/reconcile", files=_files(_zip_bomb(40), _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "unsafe" in detail
    for leak in ("openpyxl", "zipfile", "traceback", "/users/", "badzip"):
        assert leak not in detail


def test_endpoint_rate_limit_429():
    c = TestClient(main_mod.app)
    main_mod._rate_limiter = SlidingWindowRateLimiter(2, 60)
    main_mod._concurrency = ConcurrencyGate(main_mod.MAX_CONCURRENT)
    codes = [c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
                    data={"tolerance": "1.0"}).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429, codes


def test_endpoint_turnstile(monkeypatch):
    """When the Turnstile secret is set: missing token -> 403; bad token -> 403;
    valid token -> 200. (Disabled by default, so other tests are unaffected.)"""
    c = _client()
    monkeypatch.setattr(main_mod, "TURNSTILE_SECRET", "test-secret")
    # missing token
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 403 and "verification" in r.json()["detail"].lower()
    # token present but Cloudflare says no
    monkeypatch.setattr(main_mod, "_verify_turnstile", lambda tok, ip: False)
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0", "cf-turnstile-response": "x"})
    assert r.status_code == 403
    # token present and verified
    monkeypatch.setattr(main_mod, "_verify_turnstile", lambda tok, ip: True)
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0", "cf-turnstile-response": "good-token"})
    assert r.status_code == 200, r.text


def test_endpoint_turnstile_disabled_by_default():
    """With no secret set, no token is required (current/default behaviour)."""
    c = _client()
    assert main_mod.TURNSTILE_SECRET == ""  # not configured in tests
    r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
               data={"tolerance": "1.0"})
    assert r.status_code == 200, r.text


def test_endpoint_concurrency_503():
    c = _client()
    main_mod._concurrency = ConcurrencyGate(1)
    assert main_mod._concurrency.try_acquire()  # occupy the only slot
    try:
        r = c.post("/api/reconcile", files=_files(_one_row(), _one_row()),
                   data={"tolerance": "1.0"})
        assert r.status_code == 503 and "busy" in r.json()["detail"].lower()
    finally:
        main_mod._concurrency.release()


# --- tiny test runner (mirrors the sibling test module) ----------------------

class _MonkeyPatch:
    """Minimal monkeypatch shim so the no-pytest runner can call patched tests."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


def _main() -> int:
    import inspect
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        needs_client = t.__name__.startswith("test_endpoint")
        if needs_client and not _HAVE_CLIENT:
            print(f"SKIP  {t.__name__} (httpx/TestClient unavailable: {_CLIENT_ERR})")
            continue
        mp = _MonkeyPatch()
        try:
            if "monkeypatch" in inspect.signature(t).parameters:
                t(mp)
            else:
                t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
        finally:
            mp.undo()
    print(f"\n{len([t for t in tests]) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
