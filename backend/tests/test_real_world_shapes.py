"""Regression tests for real-world Excel shapes that broke the parser once.

Two layouts from actual exports are reproduced synthetically here (no real
taxpayer data is committed):

  * GSTR-2B portal export with a TWO-ROW merged header — group labels on the
    first row ("Invoice Details", "Tax Amount") and the real column names on the
    second ("Invoice number", "Integrated Tax(₹)", ...). The old single-row
    detector picked one row and silently dropped GSTIN + Taxable Value.
  * A Tally "Day Book" purchase register whose columns are named "Particulars",
    "Supplier Invoice No.", "PURCHASE @ (GST)", "INPUT C-GST/S-GST/I-GST", with
    a trailing TOTAL row. None of those names were in the alias table.

Run directly (no pytest needed):  python -m tests.test_real_world_shapes
or under pytest:                   pytest backend/tests
"""

from __future__ import annotations

import io
import os
import sys

from openpyxl import Workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parser import parse_workbook  # noqa: E402
from app.reconcile import reconcile  # noqa: E402


def _bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_bytes(rows: list[list], title: str = "Sheet1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    return _bytes(wb)


def _build_portal_two_row_header() -> bytes:
    """Mimic a GSTR-2B B2B export: title rows + a merged two-row header."""
    wb = Workbook()
    ws = wb.active
    ws.title = "B2B"
    ws.append(["Goods and Services Tax  - GSTR-2B"])
    ws.append([])
    ws.append(["Taxable inward supplies received from registered persons"])
    # Top header row: group labels; merged cells read as None below the anchor.
    ws.append(["GSTIN of supplier", "Trade/Legal name", "Invoice Details",
               None, None, None, "Place of supply", "Taxable Value (₹)",
               "Tax Amount", None, None, None])
    # Sub-header row: the real column names.
    ws.append([None, None, "Invoice number", "Invoice type", "Invoice Date",
               "Invoice Value(₹)", None, None, "Integrated Tax(₹)",
               "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)"])
    # cols:        0      1                2          3            4
    #              5            6      7         8        9     10    11
    rows = [
        # intra-state (CGST+SGST)
        ["19AAAAA1111A1Z1", "VENDOR ALPHA", "VA/26-27/001", "Regular",
         "02/04/2026", 1180, "WB", 1000, 0, 90, 90, 0],
        # inter-state (IGST only)
        ["03BBBBB2222B1Z2", "VENDOR BETA", "9988", "Regular",
         "05/04/2026", 1180, "WB", 1000, 180, 0, 0, 0],
        # portal-only (bank charge, never recorded in books)
        ["27CCCCC3333C1Z3", "ICICI BANK LTD", "BANKFEE01", "Regular",
         "10/04/2026", 118, "WB", 100, 18, 0, 0, 0],
        # year-format invoice no. (matches books only after normalisation)
        ["19DDDDD4444D1Z4", "VENDOR DELTA", "BDC/0066/2026-27", "Regular",
         "12/04/2026", 3400, "WB", 2881.5, 0, 259.34, 259.34, 0],
    ]
    for r in rows:
        ws.append(r)
    return _bytes(wb)


def _build_purchase_tally() -> bytes:
    """Mimic a Tally Day Book purchase register + a trailing TOTAL row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    ws.append(["Date", "Particulars", "Voucher Type", "Supplier Invoice No.",
               "Supplier Invoice Date", "GSTIN/UIN", "Gross Total",
               "PURCHASE @ (GST)", "INPUT C-GST", "INPUT S-GST", "INPUT I-GST",
               "ROUND OFF"])
    rows = [
        ["02-04-2026", "Vendor Alpha (Cr)", "Purchase@GST", "VA/26-27/001",
         "02-04-2026", "19AAAAA1111A1Z1", 1180, 1000, 90, 90, None, 0],
        ["05-04-2026", "Vendor Beta", "Purchase@GST", "9988",
         "05-04-2026", "03BBBBB2222B1Z2", 1180, 1000, None, None, 180, 0],
        ["12-04-2026", "Vendor Delta", "Purchase@GST", "BDC/0066/26-27",
         "12-04-2026", "19DDDDD4444D1Z4", 3400, 2881.5, 259.34, 259.34, None, 0],
        # TOTAL row: identity columns blank, amounts present -> must be ignored.
        [None, None, None, None, None, None, 5760, 4881.5, 349.34, 349.34, 180, 0],
    ]
    for r in rows:
        ws.append(r)
    return _bytes(wb)


def test_portal_two_row_header_is_parsed():
    parsed = parse_workbook(_build_portal_two_row_header(), "portal")
    cols = parsed["detected_columns"]
    # The columns that the old single-row detector silently dropped:
    assert cols.get("gstin") == "GSTIN of supplier"
    assert cols.get("taxable_value") == "Taxable Value (₹)"
    assert cols.get("supplier_name") == "Trade/Legal name"
    # Label preference: the real sub-header, not the "Tax Amount" group label.
    assert cols.get("igst") == "Integrated Tax(₹)"
    assert cols.get("invoice_no") == "Invoice number"
    assert parsed["row_count"] == 4
    first = parsed["records"][0]
    assert first["gstin_norm"] == "19AAAAA1111A1Z1"
    assert first["taxable_value"] == 1000.0
    assert first["total_tax"] == 180.0  # 90 + 90


def test_tally_purchase_aliases_and_total_row():
    parsed = parse_workbook(_build_purchase_tally(), "purchase")
    cols = parsed["detected_columns"]
    assert cols.get("supplier_name") == "Particulars"
    assert cols.get("invoice_no") == "Supplier Invoice No."
    assert cols.get("taxable_value") == "PURCHASE @ (GST)"
    assert cols.get("cgst") == "INPUT C-GST"
    assert cols.get("sgst") == "INPUT S-GST"
    assert cols.get("igst") == "INPUT I-GST"
    # Priority: the specific supplier date beats the generic voucher "Date".
    assert cols.get("invoice_date") == "Supplier Invoice Date"
    # The TOTAL row (blank identity) must NOT be read as an invoice.
    assert parsed["row_count"] == 3
    # Inter-state row keeps IGST and zero CGST/SGST.
    beta = next(r for r in parsed["records"] if r["gstin_norm"] == "03BBBBB2222B1Z2")
    assert beta["igst"] == 180.0 and beta["cgst"] == 0.0 and beta["sgst"] == 0.0


def test_end_to_end_reconciliation_buckets():
    portal = parse_workbook(_build_portal_two_row_header(), "portal")["records"]
    purchase = parse_workbook(_build_purchase_tally(), "purchase")["records"]
    res = reconcile(portal, purchase)
    s = res["summary"]
    assert s["matched_count"] == 3        # alpha + beta + delta(year-format)
    assert s["mismatch_count"] == 0
    assert s["only_in_portal_count"] == 1  # ICICI bank charge
    assert s["only_in_purchase_count"] == 0
    assert s["itc_unclaimed"] == 18.0      # only the portal-only tax
    assert s["itc_at_risk"] == 0.0
    # The year-format invoice is matched (probably), not a false discrepancy.
    delta = next(r for r in res["matched"] if r["gstin"].endswith("D1Z4"))
    assert delta["match_type"] == "probable"
    assert delta["status"] == "matched"


def test_single_row_header_still_works():
    """A plain single-row header must keep working (no two-row regression)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["GSTIN", "Supplier Name", "Invoice No", "Invoice Date",
               "Taxable Value", "IGST", "CGST", "SGST", "Invoice Value"])
    ws.append(["19AAAAA1111A1Z1", "Alpha", "INV1", "02-04-2026",
               1000, 0, 90, 90, 1180])
    parsed = parse_workbook(_bytes(wb), "portal")
    assert parsed["row_count"] == 1
    assert parsed["detected_columns"].get("gstin") == "GSTIN"
    assert parsed["records"][0]["taxable_value"] == 1000.0


# --- header-detection edge cases (found by adversarial review) ---------------

def test_two_row_header_thin_subrow_maps_one_column():
    """A 2-row header whose sub-row refines only ONE column must still be read
    as a header (column not dropped, sub-row not parsed as data)."""
    parsed = parse_workbook(_sheet_bytes([
        ["GSTIN of supplier", "Trade/Legal name", "Invoice Details",
         "Taxable Value", "Integrated Tax"],
        [None, None, "Invoice number", None, None],
        ["27ABCDE1234F1Z5", "Acme", "INV-001", 1000, 180],
        ["27ABCDE1234F1Z6", "Beta", "INV-002", 2000, 360],
    ]), "portal")
    assert parsed["detected_columns"].get("invoice_no") == "Invoice number"
    assert [r["invoice_no"] for r in parsed["records"]] == ["INV-001", "INV-002"]


def test_first_data_row_not_absorbed_as_header():
    """A single-row header followed by a data row that happens to contain alias
    words must not eat the first invoice."""
    parsed = parse_workbook(_sheet_bytes([
        ["Supplier GSTIN", "Bill No", None, "Taxable Amount", "IGST", None],
        ["27AAAAA0000A1Z5", "INV-1", "Total", 5000, 900, "Name"],
        ["27BBBBB1111B1Z5", "INV-2", "9999", 6000, 1080, "Acme"],
    ]), "purchase")
    assert sorted(r["invoice_no"] for r in parsed["records"]) == ["INV-1", "INV-2"]


def test_stacked_tables_in_one_sheet():
    """Two tables stacked in one sheet — neither is silently dropped."""
    parsed = parse_workbook(_sheet_bytes([
        ["GSTIN of supplier", "Trade/Legal name", "Invoice number",
         "Taxable Value", "Integrated Tax"],
        ["27ABCDE1234F1Z5", "Acme", "INV-001", 1000, 180],
        ["27ABCDE1234F1Z6", "Beta", "INV-002", 2000, 360],
        [None, None, None, None, None],
        [None, None, None, None, None],
        ["Supplier GSTIN", "Supplier Name", "Bill No", "Taxable Amount",
         "CGST", "SGST"],
        ["27CCCCC2222C1Z7", "Gamma", "BILL-77", 3000, 270, 270],
    ]), "portal")
    assert sorted(r["invoice_no"] for r in parsed["records"]) == \
        ["BILL-77", "INV-001", "INV-002"]


def test_subheader_not_parsed_as_phantom_invoice():
    """A thin sub-header row must never become a phantom (blank-identity) record."""
    parsed = parse_workbook(_sheet_bytes([
        ["GSTIN of supplier", "Trade/Legal name", "Invoice number",
         "Taxable Value", "Integrated Tax", "Central Tax", "State Tax"],
        [None, None, "Invoice Date", None, None, None, None],
        ["27ABCDE1234F1Z5", "Acme", "INV-001", "02-04-2026", 1000, 0, 90, 90],
        ["27ABCDE1234F1Z6", "Beta", "INV-002", "03-04-2026", 2000, 0, 180, 180],
    ]), "portal")
    assert parsed["row_count"] == 2
    assert all(r["gstin_norm"] for r in parsed["records"])


def test_group_label_does_not_shadow_specific_sublabel():
    """A generic group label ("Total") stacked above the specific column name
    ("Taxable Value") must not win the column."""
    parsed = parse_workbook(_sheet_bytes([
        ["GSTIN", "Invoice No", "Total", "IGST"],
        [None, None, "Taxable Value", None],
        ["27ABCDE1234F1Z5", "INV-1", 1000, 180],
    ]), "portal")
    assert parsed["detected_columns"].get("taxable_value") == "Taxable Value"
    rec = parsed["records"][0]
    assert rec["taxable_value"] == 1000.0


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
