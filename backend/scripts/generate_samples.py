"""
Generate two realistic sample Excel files to try the reconciliation tool:

  sample_gst_portal.xlsx       - mimics a GSTR-2A/2B "B2B" export from the
                                 GST portal (title rows + govt-style columns,
                                 real dates, IGST/CGST/SGST split).
  sample_purchase_register.xlsx- mimics a taxpayer's own purchase book with
                                 completely different column names/order and
                                 dates stored as text.

The data is hand-crafted so that uploading the two files demonstrates every
reconciliation outcome: clean matches, tax/value/date mismatches, invoice-no
formatting differences, a GSTIN typo, a within-tolerance rounding match, a bill
only on the portal, and a bill only in the books (ITC at risk).

Run from the `backend/` folder:   python scripts/generate_samples.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"

# Each scenario describes the "truth" plus how each side recorded it.
# t=taxable, and taxes are given explicitly so we can model data-entry errors.
SCENARIOS = [
    # 1. Perfect match (intra-state 18%)
    dict(name="ABC Traders", gstin="27ABCDE1234F1Z5",
         portal=dict(inv="INV-2024-001", dt=date(2024, 4, 5), t=100000, cg=9000, sg=9000, ig=0),
         book=dict(inv="INV-2024-001", dt="05-04-2024", t=100000, cg=9000, sg=9000, ig=0)),

    # 2. Tax mismatch — CGST/SGST entered wrong in the books
    dict(name="Bharat Steel Co", gstin="27BHRTS5678K1Z3",
         portal=dict(inv="BS-1024", dt=date(2024, 4, 7), t=100000, cg=9000, sg=9000, ig=0),
         book=dict(inv="BS-1024", dt="07-04-2024", t=100000, cg=8500, sg=8500, ig=0)),

    # 3. Taxable value mismatch — booked at a lower value
    dict(name="Crystal Electronics", gstin="29CRYEL9012M1Z1",
         portal=dict(inv="CE/445", dt=date(2024, 4, 9), t=50000, cg=4500, sg=4500, ig=0),
         book=dict(inv="CE/445", dt="09-04-2024", t=45000, cg=4050, sg=4050, ig=0)),

    # 4. Invoice-number formatting only (DPK001 vs DPK-1) -> still a match
    dict(name="Deepak Textiles", gstin="24DEPTX3456N1Z8",
         portal=dict(inv="DPK001", dt=date(2024, 4, 10), t=80000, cg=2000, sg=2000, ig=0),
         book=dict(inv="DPK-1", dt="10-04-2024", t=80000, cg=2000, sg=2000, ig=0)),

    # 5. Perfect match (inter-state, IGST only)
    dict(name="Eagle Logistics", gstin="06EAGLE2345P1Z2",
         portal=dict(inv="EL-789", dt=date(2024, 4, 11), t=200000, cg=0, sg=0, ig=36000),
         book=dict(inv="EL-789", dt="11-04-2024", t=200000, cg=0, sg=0, ig=36000)),

    # 6. Only on the portal — vendor uploaded, you never recorded it
    dict(name="Fortune Hardware", gstin="27FRTHW6789Q1Z9",
         portal=dict(inv="FH-2201", dt=date(2024, 4, 13), t=30000, cg=2700, sg=2700, ig=0),
         book=None),

    # 7. Only in your books — vendor has NOT uploaded (ITC at risk)
    dict(name="Galaxy Plastics", gstin="27GLXPL1122R1Z7",
         portal=None,
         book=dict(inv="GP-560", dt="15-04-2024", t=60000, cg=5400, sg=5400, ig=0)),

    # 8. Date mismatch — same bill, different date recorded
    dict(name="Hindustan Paper Mills", gstin="09HNDPP3344S1Z4",
         portal=dict(inv="HP-99", dt=date(2024, 4, 12), t=40000, cg=3600, sg=3600, ig=0),
         book=dict(inv="HP-99", dt="15-04-2024", t=40000, cg=3600, sg=3600, ig=0)),

    # 9. Rounding within ±₹1 tolerance -> treated as a match
    dict(name="Indus Chemicals", gstin="27INDCH5566T1Z2",
         portal=dict(inv="IC-300", dt=date(2024, 4, 17), t=25000, cg=2250.00, sg=2250.00, ig=0),
         book=dict(inv="IC-300", dt="17-04-2024", t=25000, cg=2250.40, sg=2249.60, ig=0)),

    # 10. GSTIN typo — same invoice & amounts, last GSTIN char wrong
    dict(name="Jupiter Tools", gstin="33JUPTL7788U1Z1",
         portal=dict(inv="JT-150", dt=date(2024, 4, 19), t=70000, cg=6300, sg=6300, ig=0),
         book=dict(inv="JT-150", dt="19-04-2024", t=70000, cg=6300, sg=6300, ig=0,
                   gstin="33JUPTL7788U1Z9")),
]


def _value(rec: dict) -> float:
    return round(rec["t"] + rec["cg"] + rec["sg"] + rec["ig"], 2)


def _rate(rec: dict) -> float:
    tax = rec["cg"] + rec["sg"] + rec["ig"]
    return round(tax / rec["t"] * 100, 0) if rec["t"] else 0


def build_portal() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "B2B"
    ws.append(["Goods and Services Tax", "GSTR-2B", "Auto-drafted ITC Statement"])
    ws.append(["Taxpayer GSTIN: 27AAAAA0000A1Z5", "Period: Apr 2024", None])
    ws.append([])  # blank spacer row, like the real export
    header = ["GSTIN of supplier", "Trade/Legal name", "Invoice number",
              "Invoice Date", "Invoice Value(₹)", "Rate(%)", "Taxable Value (₹)",
              "Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)"]
    ws.append(header)
    _style_header(ws, ws.max_row, len(header))

    for s in SCENARIOS:
        p = s["portal"]
        if p is None:
            continue
        gstin = p.get("gstin", s["gstin"])
        ws.append([gstin, s["name"], p["inv"], p["dt"], _value(p), _rate(p),
                   p["t"], p["ig"], p["cg"], p["sg"], 0])
    _autosize(ws)
    return wb


def build_purchase() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    header = ["Vendor Name", "Supplier GSTIN", "Bill No", "Bill Date",
              "Taxable Amount", "IGST", "CGST", "SGST", "Cess",
              "Total Invoice Value"]
    ws.append(header)
    _style_header(ws, 1, len(header))

    for s in SCENARIOS:
        b = s["book"]
        if b is None:
            continue
        gstin = b.get("gstin", s["gstin"])
        ws.append([s["name"], gstin, b["inv"], b["dt"], b["t"], b["ig"],
                   b["cg"], b["sg"], 0, _value(b)])
    _autosize(ws)
    return wb


def _style_header(ws, row: int, ncols: int) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(bold=True, color="FFFFFF")
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = fill
        cell.font = font


def _autosize(ws) -> None:
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(width + 2, 32)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    portal_path = OUT_DIR / "sample_gst_portal.xlsx"
    purchase_path = OUT_DIR / "sample_purchase_register.xlsx"
    build_portal().save(portal_path)
    build_purchase().save(purchase_path)
    print(f"Wrote {portal_path}")
    print(f"Wrote {purchase_path}")


if __name__ == "__main__":
    main()
