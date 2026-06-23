"""
GST reconciliation engine.

Compares invoices the taxpayer recorded in their purchase register against the
invoices the vendors uploaded to the GST portal (GSTR-2A / 2B) and classifies
every invoice into one of four buckets:

  * matched           - found on both sides, all values agree (within tolerance)
  * mismatch          - found on both sides, but one or more values differ
  * only_in_portal    - vendor uploaded it, but it is missing from your books
  * only_in_purchase  - it is in your books, but the vendor has NOT uploaded it
                        (this is the dangerous one: your Input Tax Credit is at risk)

Matching happens in two passes:
  1. exact   - same GSTIN + same normalised invoice number
  2. probable- a fuzzy fallback that catches invoice-number formatting
               differences (INV-001 vs INV001) and the occasional GSTIN typo,
               as long as the money lines line up.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

# Amounts within this many rupees of each other are treated as equal
# (absorbs rounding noise between the portal and the books).
DEFAULT_TOLERANCE = 1.0

# --- DoS bounds for the matcher --------------------------------------------
# Matching is inherently quadratic in the worst case (the fuzzy fallback compares
# leftover portal rows against leftover purchase rows). Without bounds, a small
# upload of deliberately non-matching rows could pin a worker for minutes/hours.
# These caps keep real data near-linear while making the adversarial worst case
# finite. They never affect a normal reconciliation: in real data, exact matches
# in pass 1 consume almost everything, so few rows reach the fuzzy pass.
MAX_ROWS_PER_KEY = 50            # exact-match rows considered per (gstin, invoice) key
MAX_SECONDARY_BUCKET = 256       # cross-GSTIN fuzzy candidates per invoice-block / amount bucket
# Pass 2 charges each fuzzy comparison its real cost: difflib.SequenceMatcher is
# ~O(len_a * len_b), so a *count* cap wouldn't bound wall-clock (a 64-char key is
# ~35x dearer than an 8-char one). We budget character-work instead, which keeps
# the worst case to a couple of seconds regardless of how long the (capped)
# invoice strings are. Real invoice numbers are short, so legit data — which only
# fuzzy-matches the handful of rows left after exact matching — never hits this.
FUZZY_WORK_BUDGET = 20_000_000   # cap on sum(len_a * len_b) across pass 2

# Fields compared when deciding matched vs mismatch, with friendly labels.
COMPARE_FIELDS: list[tuple[str, str]] = [
    ("taxable_value", "Taxable Value"),
    ("igst", "IGST"),
    ("cgst", "CGST"),
    ("sgst", "SGST"),
    ("cess", "Cess"),
    ("total_tax", "Total Tax"),
    ("invoice_value", "Invoice Value"),
]


def _amounts_differ(a: float, b: float, tol: float) -> bool:
    return abs(round(a - b, 2)) > tol


def _public(rec: dict[str, Any]) -> dict[str, Any]:
    """Trim a record down to the JSON-safe fields the frontend needs."""
    return {
        "id": rec["id"],
        "gstin": rec["gstin"],
        "supplier_name": rec["supplier_name"],
        "invoice_no": rec["invoice_no"],
        "invoice_date": rec["invoice_date"],
        "taxable_value": rec["taxable_value"],
        "igst": rec["igst"],
        "cgst": rec["cgst"],
        "sgst": rec["sgst"],
        "cess": rec["cess"],
        "total_tax": rec["total_tax"],
        "invoice_value": rec["invoice_value"],
        "row_no": rec["row_no"],
        "source": rec["source"],
    }


def _diff_pair(portal: dict[str, Any], purchase: dict[str, Any],
               tol: float) -> list[dict[str, Any]]:
    """Return the list of fields that differ between a matched pair.

    Each diff is tagged `material` — a material difference (money, date or
    GSTIN) makes the pair a real mismatch; a non-material one (invoice-number
    formatting) is only informational and the pair still counts as matched.
    """
    diffs: list[dict[str, Any]] = []

    # GSTIN (only meaningful for probable matches where it may differ)
    if portal["gstin_norm"] != purchase["gstin_norm"]:
        diffs.append({
            "field": "gstin", "label": "GSTIN", "material": True,
            "portal": portal["gstin"], "purchase": purchase["gstin"],
            "delta": None,
        })

    # Invoice number text. A formatting-only difference (INV-001 vs INV001) is
    # cosmetic, but two genuinely different numbers — even with equal amounts —
    # are a real discrepancy worth flagging, so the materiality follows how
    # similar the numbers actually are.
    if portal["invoice_norm"] != purchase["invoice_norm"]:
        sim = (SequenceMatcher(None, portal["invoice_norm"],
                               purchase["invoice_norm"]).ratio()
               if portal["invoice_norm"] and purchase["invoice_norm"] else 0.0)
        diffs.append({
            "field": "invoice_no", "label": "Invoice No",
            "material": sim < 0.6,
            "portal": portal["invoice_no"], "purchase": purchase["invoice_no"],
            "delta": None,
        })

    # Invoice date
    pd, cd = portal["_date_obj"], purchase["_date_obj"]
    if pd and cd and pd != cd:
        diffs.append({
            "field": "invoice_date", "label": "Invoice Date", "material": True,
            "portal": portal["invoice_date"], "purchase": purchase["invoice_date"],
            "delta": None,
        })

    # Money fields
    for field, label in COMPARE_FIELDS:
        if _amounts_differ(portal[field], purchase[field], tol):
            diffs.append({
                "field": field, "label": label, "material": True,
                "portal": portal[field], "purchase": purchase[field],
                "delta": round(purchase[field] - portal[field], 2),
            })
    return diffs


def _is_match(diffs: list[dict[str, Any]]) -> bool:
    """A pair is a clean match when nothing material differs."""
    return not any(d["material"] for d in diffs)


def _material_diff_count(portal: dict[str, Any], purchase: dict[str, Any],
                         tol: float) -> int:
    return sum(1 for d in _diff_pair(portal, purchase, tol) if d["material"])


def _remark_for_pair(diffs: list[dict[str, Any]], match_type: str) -> str:
    material = [d["label"] for d in diffs if d["material"]]
    cosmetic = [d for d in diffs if not d["material"]]
    if material:
        prefix = "Probable match — " if match_type == "probable" else ""
        return prefix + "Mismatch in: " + ", ".join(material) + "."
    if cosmetic:  # only invoice-number formatting differs
        d = cosmetic[0]
        return (f"Matched — invoice no. differs only in formatting "
                f"(portal: {d['portal']}, books: {d['purchase']}).")
    return ("Exact match." if match_type == "exact"
            else "Matched after normalising invoice no.; all values agree.")


def _invblock(inv: str) -> tuple[int, str]:
    """Cheap blocking key for cross-GSTIN fuzzy candidates: two invoice numbers
    that could be ~90% similar share a length and their leading characters, so
    we only fuzzy-compare within the same block instead of all-vs-all."""
    return (len(inv), inv[:4])


def _amt_key(taxable: float) -> int:
    """Rupee-rounded amount key. The cross-GSTIN accept rule requires matching
    amounts, so bucketing leftovers by amount catches a GSTIN-typo pair even
    when its invoice number differs in length / leading chars (which would put
    it in a different _invblock)."""
    return int(round(taxable))


def _fuzzy_score(portal: dict[str, Any], purchase: dict[str, Any],
                 tol: float) -> float | None:
    """Score a candidate probable-match pair, or None if not acceptable."""
    gstin_eq = bool(portal["gstin_norm"]) and portal["gstin_norm"] == purchase["gstin_norm"]
    pn, cn = portal["invoice_norm"], purchase["invoice_norm"]
    inv_sim = SequenceMatcher(None, pn, cn).ratio() if pn and cn else 0.0
    amt_close = not _amounts_differ(portal["taxable_value"],
                                    purchase["taxable_value"], tol) \
        and (portal["taxable_value"] != 0 or purchase["taxable_value"] != 0)

    # Acceptance rules (any one is enough):
    #  - same vendor and the invoice numbers are clearly the same bill, OR
    #  - same vendor, amount matches, and the invoice no. at least resembles, OR
    #  - same invoice number and amount but a GSTIN typo.
    # The amount branch requires a little invoice-number resemblance so two
    # unrelated same-value invoices from one vendor are NOT paired blindly.
    accept = (
        (gstin_eq and inv_sim >= 0.55)
        or (gstin_eq and amt_close and inv_sim >= 0.34)
        or (inv_sim >= 0.9 and amt_close)
    )
    if not accept:
        return None
    return (2.0 if gstin_eq else 0.0) + inv_sim + (1.0 if amt_close else 0.0)


def reconcile(portal: list[dict[str, Any]], purchase: list[dict[str, Any]],
              tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    used_portal: set[int] = set()
    used_purchase: set[int] = set()
    pairs: list[tuple[int, int, str]] = []  # (portal_idx, purchase_idx, type)

    # A row with neither a GSTIN nor an invoice number has no identity to
    # match on — never pair two such rows just because both keys are ('', '').
    def has_identity(r: dict[str, Any]) -> bool:
        return bool(r["gstin_norm"] or r["invoice_norm"])

    def key_of(r: dict[str, Any]) -> tuple[str, str]:
        return (r["gstin_norm"], r["invoice_norm"])

    # --- pass 1: exact key (GSTIN + normalised invoice no.) ---------------
    # Cap how many rows we index/pair per identical key. Beyond a small number,
    # rows sharing one (gstin, invoice) key are duplicate entries; scanning the
    # whole bucket for every same-key row would be O(bucket^2), so a cheap upload
    # of N identical-key rows could pin the worker. Overflow rows fall through
    # and are flagged as duplicates below (key ∈ matched_keys).
    portal_by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(portal):
        if has_identity(r):
            bucket = portal_by_key.setdefault(key_of(r), [])
            if len(bucket) < MAX_ROWS_PER_KEY:
                bucket.append(i)

    matched_keys: set[tuple[str, str]] = set()
    purchase_seen_per_key: dict[tuple[str, str], int] = {}
    for j, pr in enumerate(purchase):
        if not has_identity(pr):
            continue
        k = key_of(pr)
        # Only the first MAX_ROWS_PER_KEY purchase rows of a key are paired; the
        # rest are duplicates (and bucket is already capped, so this scan is O(cap)).
        seen = purchase_seen_per_key.get(k, 0)
        purchase_seen_per_key[k] = seen + 1
        if seen >= MAX_ROWS_PER_KEY:
            continue
        cands = [pi for pi in portal_by_key.get(k, []) if pi not in used_portal]
        if not cands:
            continue
        # Among portal rows sharing this invoice no., take the one whose amounts
        # agree best (handles a revised/duplicate upload under the same number).
        best_pi = min(cands, key=lambda pi: _material_diff_count(portal[pi], pr, tolerance))
        pairs.append((best_pi, j, "exact"))
        used_portal.add(best_pi)
        used_purchase.add(j)
        matched_keys.add(k)

    # Leftover rows whose key was already consumed by an exact match are
    # duplicate entries (the same bill typed twice). Set them aside so they
    # can't fuzzy-match an unrelated invoice, and flag them clearly.
    dup_portal = {i for i, r in enumerate(portal)
                  if i not in used_portal and has_identity(r) and key_of(r) in matched_keys}
    dup_purchase = {j for j, r in enumerate(purchase)
                    if j not in used_purchase and has_identity(r) and key_of(r) in matched_keys}

    # --- pass 2: fuzzy / probable matches on the leftovers ----------------
    # Index the leftovers so each purchase row only scores a small candidate set
    # instead of every remaining portal row. The two strong accept rules in
    # _fuzzy_score require an equal GSTIN, so bucket by gstin_norm; the only
    # cross-GSTIN rule needs a near-identical invoice number OR an equal amount,
    # so also index by a cheap invoice block (length + leading chars) and by
    # rounded amount. This keeps real data near-linear while still finding the
    # same matches, and a global work budget caps the adversarial worst case.
    rem_portal = [i for i in range(len(portal))
                  if i not in used_portal and i not in dup_portal]
    portal_by_gstin: dict[str, list[int]] = {}
    portal_by_invblock: dict[tuple[int, str], list[int]] = {}
    portal_by_amt: dict[int, list[int]] = {}
    for pi in rem_portal:
        r = portal[pi]
        g = r["gstin_norm"]
        if g:
            portal_by_gstin.setdefault(g, []).append(pi)
        inv = r["invoice_norm"]
        if inv:
            blk = portal_by_invblock.setdefault(_invblock(inv), [])
            if len(blk) < MAX_SECONDARY_BUCKET:
                blk.append(pi)
        t = r["taxable_value"]
        if t:
            ab = portal_by_amt.setdefault(_amt_key(t), [])
            if len(ab) < MAX_SECONDARY_BUCKET:
                ab.append(pi)

    work_budget = FUZZY_WORK_BUDGET
    fuzzy_truncated = False
    for j in range(len(purchase)):
        if j in used_purchase or j in dup_purchase:
            continue
        pr = purchase[j]
        # Candidate set = same-GSTIN rows (covers the two GSTIN-equal accept
        # rules) + same invoice-block + same rounded amount (the last two cover
        # the cross-GSTIN typo rule, which needs only a similar invoice OR an
        # equal amount). A global work budget bounds the total comparisons.
        cand_ids: set[int] = set()
        g = pr["gstin_norm"]
        if g:
            cand_ids.update(portal_by_gstin.get(g, ()))
        inv = pr["invoice_norm"]
        if inv:
            cand_ids.update(portal_by_invblock.get(_invblock(inv), ()))
        t = pr["taxable_value"]
        if t:
            ak = _amt_key(t)
            for k in (ak - 1, ak, ak + 1):  # ±1 rupee absorbs rounding/tolerance
                cand_ids.update(portal_by_amt.get(k, ()))
        if not cand_ids:
            continue
        cn_cost = len(pr["invoice_norm"]) + 1
        best_pi, best_score = None, 0.0
        # sorted() keeps the original index-order tie-breaking (deterministic).
        for pi in sorted(cand_ids):
            if work_budget <= 0:
                fuzzy_truncated = True
                break
            p = portal[pi]
            # Charge every candidate examined (incl. already-used skips) so the
            # outer loop is bounded by total work, not just completed compares.
            work_budget -= (len(p["invoice_norm"]) + 1) * cn_cost
            if pi in used_portal:
                continue
            score = _fuzzy_score(p, pr, tolerance)
            if score is not None and score > best_score:
                best_pi, best_score = pi, score
        if best_pi is not None:
            pairs.append((best_pi, j, "probable"))
            used_portal.add(best_pi)
            used_purchase.add(j)
        if fuzzy_truncated:
            break

    # --- build the reconciled rows ---------------------------------------
    matched: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    for pi, j, match_type in pairs:
        p, c = portal[pi], purchase[j]
        diffs = _diff_pair(p, c, tolerance)
        is_match = _is_match(diffs)
        row = {
            "gstin": c["gstin"] or p["gstin"],
            "supplier_name": p["supplier_name"] or c["supplier_name"],
            "invoice_no": c["invoice_no"] or p["invoice_no"],
            "invoice_date": p["invoice_date"] or c["invoice_date"],
            "match_type": match_type,
            "status": "matched" if is_match else "mismatch",
            "portal": _public(p),
            "purchase": _public(c),
            "diffs": diffs,
            "remarks": _remark_for_pair(diffs, match_type),
        }
        (matched if is_match else mismatched).append(row)

    # --- the unmatched leftovers -----------------------------------------
    only_in_portal = []
    for i, r in enumerate(portal):
        if i in used_portal:
            continue
        item = _public(r)
        if i in dup_portal:
            item["duplicate"] = True
            item["remarks"] = ("Duplicate of an invoice already matched above "
                               "— repeated row in the portal data.")
        else:
            item["duplicate"] = False
            item["remarks"] = ("In GSTR-2A/2B but not in your purchase register "
                               "— record this bill / unclaimed ITC.")
        only_in_portal.append(item)

    only_in_purchase = []
    for j, r in enumerate(purchase):
        if j in used_purchase:
            continue
        item = _public(r)
        if j in dup_purchase:
            item["duplicate"] = True
            item["remarks"] = ("Duplicate of an invoice already matched above "
                               "— likely a double entry in your books.")
        else:
            item["duplicate"] = False
            item["remarks"] = ("In your books but NOT uploaded by the vendor — "
                               "ITC at risk, follow up with the supplier.")
        only_in_purchase.append(item)

    summary = _build_summary(portal, purchase, matched, mismatched,
                             only_in_portal, only_in_purchase)

    warnings: list[str] = []
    if fuzzy_truncated:
        warnings.append(
            "Too many unmatched rows to fuzzy-compare safely, so probable-match "
            "detection was stopped early; some rows that might be formatting "
            "variants are listed as 'only in portal' / 'only in books'."
        )

    return {
        "summary": summary,
        "matched": matched,
        "mismatched": mismatched,
        "only_in_portal": only_in_portal,
        "only_in_purchase": only_in_purchase,
        "warnings": warnings,
    }


def _sum(rows: list[dict[str, Any]], field: str) -> float:
    return round(sum(r.get(field, 0) or 0 for r in rows), 2)


def _sum_positive(rows: list[dict[str, Any]], field: str) -> float:
    """Sum only positive values — credit notes (negative tax) must not net
    down a genuine ITC exposure."""
    return round(sum(v for r in rows if (v := r.get(field, 0) or 0) > 0), 2)


def _build_summary(portal, purchase, matched, mismatched,
                   only_in_portal, only_in_purchase) -> dict[str, Any]:
    portal_tax = _sum(portal, "total_tax")
    purchase_tax = _sum(purchase, "total_tax")
    # ITC figures exclude flagged duplicates — those are a double-entry problem,
    # not a vendor-upload problem.
    risk_rows = [r for r in only_in_purchase if not r.get("duplicate")]
    unclaimed_rows = [r for r in only_in_portal if not r.get("duplicate")]
    return {
        "portal_count": len(portal),
        "purchase_count": len(purchase),
        "matched_count": len(matched),
        "mismatch_count": len(mismatched),
        "only_in_portal_count": len(only_in_portal),
        "only_in_purchase_count": len(only_in_purchase),
        "duplicate_count": sum(1 for r in only_in_portal + only_in_purchase
                               if r.get("duplicate")),

        "portal_taxable_total": _sum(portal, "taxable_value"),
        "purchase_taxable_total": _sum(purchase, "taxable_value"),
        "portal_tax_total": portal_tax,
        "purchase_tax_total": purchase_tax,
        "net_tax_difference": round(portal_tax - purchase_tax, 2),

        # The two numbers an accountant actually cares about (positive tax only):
        "itc_at_risk": _sum_positive(risk_rows, "total_tax"),
        "itc_unclaimed": _sum_positive(unclaimed_rows, "total_tax"),
        "mismatch_tax_diff": round(
            sum(abs(r["purchase"]["total_tax"] - r["portal"]["total_tax"])
                for r in mismatched), 2),
    }
