"""
Excel parsing & column normalization for GST reconciliation.

Real-world GST data comes from two very different shapes:

  * The GST portal export (GSTR-2A / GSTR-2B "B2B" sheet) which has a couple of
    title rows, government-style column names like "Integrated Tax(₹)" and a
    "GSTIN of supplier" column.
  * The taxpayer's own purchase register, which can use ANY column names
    ("Bill No", "Vendor Name", "IGST", "Taxable Amount", ...).

This module reads either file from raw bytes, auto-detects the header row, maps
whatever columns it finds onto a fixed set of canonical fields and returns a
list of clean invoice records that the reconciliation engine can compare.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

import openpyxl


# --- canonical fields ------------------------------------------------------

# Each canonical field maps to the many ways it can appear in the wild.
# Matching is done on a normalised form of the header text (lowercased,
# punctuation / currency symbols stripped) so "Integrated Tax(₹)" and
# "IGST Amount" both resolve to `igst`.
#
# Order matters: aliases are ranked by their position in each list, and when
# two columns both resolve to the same field the better-ranked (earlier) alias
# wins. So put the *specific* names first and generic catch-alls ("Date",
# "Total") last — e.g. a Tally day-book has both a voucher "Date" and a
# "Supplier Invoice Date"; the latter is the one to reconcile against.
COLUMN_ALIASES: dict[str, list[str]] = {
    "gstin": [
        "GSTIN of supplier", "GSTIN/UIN of supplier", "GSTIN of the supplier",
        "Supplier GSTIN", "GSTIN/UIN", "GSTIN No", "GSTIN", "GST No",
        "Supplier GST", "Party GSTIN", "GSTIN/UIN of Recipient",
    ],
    "supplier_name": [
        "Trade/Legal name", "Trade / Legal name", "Trade Name", "Legal Name",
        "Supplier Name", "Vendor Name", "Name of Supplier", "Party Name",
        "Particulars", "Supplier", "Vendor", "Name",
    ],
    "invoice_no": [
        "Supplier Invoice No.", "Supplier Invoice No", "Supplier Invoice Number",
        "Supplier Inv No", "Supplier Bill No",
        "Invoice number", "Invoice No", "Invoice No.", "Inv No", "Inv No.",
        "Bill No", "Bill Number", "Bill No.", "Document Number", "Voucher No",
        "Invoice", "Inv Number",
    ],
    "invoice_date": [
        "Supplier Invoice Date", "Invoice Date", "Inv Date", "Bill Date",
        "Document Date", "Invoice Dt", "Dated", "Date",
    ],
    "invoice_value": [
        "Invoice Value", "Total Invoice Value", "Invoice Amount", "Bill Amount",
        "Gross Total", "Gross Amount", "Total Amount", "Total Value",
        "Grand Total", "Total",
    ],
    "taxable_value": [
        "Taxable Value", "Taxable Amount", "Assessable Value", "Basic Amount",
        "PURCHASE @ (GST)", "Purchase @ GST", "Purchase Value", "Purchase Amount",
        "Taxable", "Net Amount", "Base Amount",
    ],
    "igst": [
        "Integrated Tax", "Integrated Tax Amount", "INPUT I-GST", "Input IGST",
        "IGST", "IGST Amount", "I GST", "I-GST",
    ],
    "cgst": [
        "Central Tax", "Central Tax Amount", "INPUT C-GST", "Input CGST",
        "CGST", "CGST Amount", "C GST", "C-GST",
    ],
    "sgst": [
        "State/UT Tax", "State Tax", "State/UT Tax Amount", "INPUT S-GST",
        "Input SGST", "SGST", "SGST Amount", "S GST", "S-GST", "SGST/UTGST",
    ],
    "cess": [
        "Cess", "Cess Amount", "GST Cess",
    ],
    "rate": [
        "Rate", "Rate(%)", "Tax Rate", "GST Rate", "Rate %",
    ],
}

NUMERIC_FIELDS = ["taxable_value", "igst", "cgst", "sgst", "cess",
                  "invoice_value", "rate"]

# Safety limits — an .xlsx is a zip and can declare an enormous sheet, so we
# stream rows and stop well before exhausting memory.
HEADER_SCAN_ROWS = 200      # how deep to look for the header row
MAX_COLS = 64               # columns kept per row
MAX_ROWS_PER_SHEET = 200_000
MAX_RECORDS = 100_000       # total invoice rows across all sheets

# Footer / subtotal rows ("Total", "Grand Total", …) that must never be read
# as invoices even though they carry amounts.
TOTAL_LABEL_RE = re.compile(
    r"^\s*(grand\s*total|sub\s*-?\s*total|totals?|net\s*total)\s*[:\-]?\s*$",
    re.IGNORECASE,
)


def _norm_header(text: Any) -> str:
    """Normalise a header cell so aliases compare cleanly."""
    s = str(text or "").lower()
    s = s.replace("₹", "").replace("rs.", "").replace("rs", "")
    return re.sub(r"[^a-z0-9]", "", s)


# Reverse lookup: normalised alias -> (canonical field name, rank).
# `rank` is the alias's position within its field list (lower = more specific /
# higher priority). When two columns map to the same field, the lower rank wins.
_ALIAS_LOOKUP: dict[str, tuple[str, int]] = {}
for _field, _aliases in COLUMN_ALIASES.items():
    for _rank, _alias in enumerate(_aliases):
        _key = _norm_header(_alias)
        # keep the best (lowest) rank if an alias normalises to an existing key
        if _key and (_key not in _ALIAS_LOOKUP or _rank < _ALIAS_LOOKUP[_key][1]):
            _ALIAS_LOOKUP[_key] = (_field, _rank)


def parse_amount(value: Any) -> float:
    """Parse a money/number cell that may contain commas, ₹, '-', blanks."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s in ("", "-", "–", "—", "NA", "N/A", "nil", "Nil", "NIL"):
        return 0.0
    s = s.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "")
    s = s.replace("%", "").strip()
    # handle parenthesised negatives e.g. (1,200.00)
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        num = float(s)
        return -num if neg else num
    except ValueError:
        return 0.0


_DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%B-%Y",
    "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%y", "%d/%m/%y", "%m/%d/%Y",
]


def parse_date(value: Any) -> tuple[str | None, date | None]:
    """Return (display string dd-mm-yyyy, date object) from any date cell."""
    if value is None or value == "":
        return None, None
    if isinstance(value, datetime):
        d = value.date()
        return d.strftime("%d-%m-%Y"), d
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y"), value
    s = str(value).strip()
    if not s:
        return None, None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.strftime("%d-%m-%Y"), d
        except ValueError:
            continue
    # could not parse — keep the raw text for display, no comparable object
    return s, None


def norm_gstin(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def norm_invoice(value: Any) -> str:
    """Normalise an invoice number for matching (INV-001 == inv 001)."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _build_mapping(block: list[list[Any]]) -> dict[str, int]:
    """Map columns -> canonical fields for a header block of 1 or 2 rows.

    Government exports (GSTR-2A/2B) split the header across two rows: a top
    row of group labels ("Invoice Details", "Tax Amount") with the real column
    names ("Invoice number", "Integrated Tax(₹)") in the row below, joined by
    merged cells. To read those we look at every header cell stacked in a
    column — across both rows and their concatenation — and keep the best
    (lowest-rank) alias match per field.
    """
    ncols = max((len(r) for r in block), default=0)
    best: dict[str, tuple[int, int]] = {}  # field -> (rank, col)
    for col in range(ncols):
        cells = [r[col] for r in block
                 if col < len(r) and r[col] is not None and str(r[col]).strip()]
        if not cells:
            continue
        # Prefer the bottom-most row's label (the specific column name, e.g.
        # "Taxable Value") over a merged group label stacked above it (e.g.
        # "Total"); fall back to the rows joined together.
        candidates = [str(c) for c in reversed(cells)]
        if len(cells) > 1:
            candidates.append(" ".join(str(c) for c in cells))
        col_hit: tuple[str, int] | None = None
        for text in candidates:
            col_hit = _ALIAS_LOOKUP.get(_norm_header(text))
            if col_hit is not None:
                break
        if col_hit is None:
            continue
        field, rank = col_hit
        cur = best.get(field)
        # When two columns map to the same field, the better-ranked alias wins.
        if cur is None or rank < cur[0]:
            best[field] = (rank, col)
    return {field: col for field, (rank, col) in best.items()}


_AMOUNT_FIELDS = ("taxable_value", "igst", "cgst", "sgst", "cess",
                  "invoice_value")


def _looks_like_header(mapping: dict[str, int]) -> bool:
    """A believable header has an identity column AND an amount column."""
    has_key = "gstin" in mapping or "invoice_no" in mapping
    has_amount = any(f in mapping for f in _AMOUNT_FIELDS)
    return has_key and has_amount


def _is_numeric_cell(value: Any) -> bool:
    """True if the cell carries an actual number (not a header label)."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float, datetime, date)):
        return True
    s = str(value).strip().replace(",", "").replace("₹", "").replace("%", "")
    if not s:
        return False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_data_row(row: list[Any], mapping: dict[str, int]) -> bool:
    """A data row carries real values in the amount/date columns; a header (or
    merged sub-header) row carries text labels or blanks there."""
    for field in _AMOUNT_FIELDS:
        col = mapping.get(field)
        if col is not None and col < len(row) and _is_numeric_cell(row[col]):
            return True
    dcol = mapping.get("invoice_date")
    if dcol is not None and dcol < len(row) and isinstance(row[dcol], (date, datetime)):
        return True
    return False


def _detect_header_blocks(
        rows: list[list[Any]]) -> list[tuple[list[int], dict[str, int]]]:
    """Find every header block in a sheet (supports stacked tables).

    A header may span more than one row: government exports (GSTR-2A/2B) stack a
    group-label row on top of the real column-name row, joined by merged cells.
    We anchor on each row that on its own looks like a header, then grow the
    block into adjacent rows that are NOT data rows and that contribute extra
    columns. Data begins on the row after the block; it ends where the next
    block begins (or at end of sheet).

    Returns a list of (header_row_indices, {canonical_field: column_index}) in
    top-to-bottom order.
    """
    scan = min(len(rows), HEADER_SCAN_ROWS)
    blocks: list[tuple[list[int], dict[str, int]]] = []
    consumed = [False] * len(rows)

    i = 0
    while i < scan:
        if consumed[i] or not _looks_like_header(_build_mapping([rows[i]])):
            i += 1
            continue
        top = bottom = i
        # Grow upward through merged group-label rows sitting above the anchor:
        # any adjacent non-data row that still carries header labels is part of
        # the header (it may add columns OR just refine one, e.g. a "Total"
        # group label above the real "Taxable Value" column name).
        while top - 1 >= 0 and not consumed[top - 1]:
            prev = rows[top - 1]
            if _is_data_row(prev, _build_mapping(rows[top:bottom + 1])):
                break
            if _build_mapping([prev]):
                top -= 1
            else:
                break
        # Grow downward through sub-label rows sitting below the anchor.
        while bottom + 1 < len(rows):
            nxt = rows[bottom + 1]
            if _is_data_row(nxt, _build_mapping(rows[top:bottom + 1])):
                break
            if _build_mapping([nxt]):
                bottom += 1
            else:
                break
        mapping = _build_mapping(rows[top:bottom + 1])
        for r in range(top, bottom + 1):
            consumed[r] = True
        blocks.append((list(range(top, bottom + 1)), mapping))
        i = bottom + 1
    return blocks


def _header_label(block: list[list[Any]], col: int) -> str | None:
    """Human-readable header text for a mapped column (for the meta report).

    Prefer the cell that actually resolved to a canonical field (the real column
    name, e.g. "Integrated Tax(₹)") over a merged group label above it
    (e.g. "Tax Amount"); fall back to the first non-empty cell.
    """
    fallback: str | None = None
    for row in reversed(block):  # bottom row holds the specific column name
        if col < len(row) and row[col] is not None and str(row[col]).strip():
            text = str(row[col]).strip()
            if fallback is None:
                fallback = text
            if _norm_header(text) in _ALIAS_LOOKUP:
                return text
    return fallback


def _is_blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def _cell(row: list[Any], col: int | None) -> Any:
    return row[col] if col is not None and col < len(row) else None


def _stream_rows(sheet) -> list[list[Any]]:
    """Read a sheet with hard row/column caps (avoids huge-dimension DoS)."""
    rows: list[list[Any]] = []
    for r in sheet.iter_rows(values_only=True):
        rows.append(list(r[:MAX_COLS]))
        if len(rows) >= MAX_ROWS_PER_SHEET:
            break
    return rows


def parse_workbook(content: bytes, source: str) -> dict[str, Any]:
    """Parse an uploaded .xlsx into normalised invoice records.

    Reads EVERY sheet that has recognisable invoice columns and concatenates
    them — a real GSTR-2B export keeps B2B, CDNR (credit notes) and B2BA
    (amendments) on separate sheets, all of which affect ITC.

    `source` is a label ("portal" or "purchase") used in messages.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True,
                                    read_only=True)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the API
        raise ValueError(f"Could not read the {source} file as an Excel "
                         f"workbook (.xlsx): {exc}") from exc

    records: list[dict[str, Any]] = []
    sheets_parsed: list[str] = []
    primary_columns: dict[str, str | None] = {}
    warnings: list[str] = []
    truncated = False

    try:
        for sheet in wb.worksheets:
            all_rows = _stream_rows(sheet)
            blocks = _detect_header_blocks(all_rows)
            if not blocks:
                continue
            for bi, (header_rows, mapping) in enumerate(blocks):
                # Each table's data runs from just after its header up to the
                # next table's header (or end of sheet).
                data_start = header_rows[-1] + 1
                data_end = (blocks[bi + 1][0][0] if bi + 1 < len(blocks)
                            else len(all_rows))
                sheet_records = _parse_sheet(all_rows, data_start, data_end,
                                             mapping, source, sheet.title)
                if not sheet_records:
                    continue
                if sheet.title not in sheets_parsed:
                    sheets_parsed.append(sheet.title)
                if not primary_columns:  # report the first real header's columns
                    header_block = [all_rows[i] for i in header_rows]
                    primary_columns = {
                        field: _header_label(header_block, col)
                        for field, col in mapping.items()
                    }
                records.extend(sheet_records)
                if len(records) >= MAX_RECORDS:
                    records = records[:MAX_RECORDS]
                    truncated = True
                    warnings.append(
                        f"Only the first {MAX_RECORDS:,} invoice rows were read "
                        f"from the {source} file (it is very large).")
                    break
            if truncated:
                break
    finally:
        wb.close()

    if not sheets_parsed:
        raise ValueError(
            f"Could not find recognisable invoice columns in the {source} "
            f"file. Expected columns like GSTIN, Invoice No, Taxable Value, "
            f"IGST/CGST/SGST."
        )

    return {
        "records": records,
        "sheet": ", ".join(sheets_parsed),
        "detected_columns": primary_columns,
        "row_count": len(records),
        "warnings": warnings,
        "truncated": truncated,
    }


def _parse_sheet(all_rows: list[list[Any]], data_start: int, data_end: int,
                 mapping: dict[str, int], source: str,
                 sheet_title: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    last_gstin: Any = None
    last_supplier: Any = None
    g_col, s_col = mapping.get("gstin"), mapping.get("supplier_name")
    i_col = mapping.get("invoice_no")

    for r_idx in range(data_start, data_end):
        row = all_rows[r_idx]
        gstin_raw = _cell(row, g_col)
        supplier_raw = _cell(row, s_col)
        invoice_raw = _cell(row, i_col)
        has_invoice = not _is_blank(invoice_raw)

        # Merged-cell carry-down: in GSTR-2A the GSTIN + Trade/Legal name are
        # merged across a vendor's invoice rows, so subsequent rows read blank.
        # Only fill when BOTH key cells are blank (the merge signature) so we
        # never fabricate a GSTIN over a genuine data gap in a purchase book.
        if (has_invoice and _is_blank(gstin_raw) and _is_blank(supplier_raw)
                and last_gstin is not None):
            gstin_raw, supplier_raw = last_gstin, last_supplier
        else:
            if not _is_blank(gstin_raw):
                last_gstin = gstin_raw
            if not _is_blank(supplier_raw):
                last_supplier = supplier_raw

        rec = _row_to_record(row, mapping, source, sheet_title, r_idx + 1,
                             gstin_raw, supplier_raw, invoice_raw)
        if rec is not None:
            records.append(rec)
    return records


def _row_to_record(row: list[Any], mapping: dict[str, int], source: str,
                   sheet: str, row_no: int, gstin_raw: Any, supplier_raw: Any,
                   invoice_raw: Any) -> dict[str, Any] | None:
    gstin = norm_gstin(gstin_raw)
    invoice_norm = norm_invoice(invoice_raw)

    # Drop "Total" / "Grand Total" footer rows: they carry summed amounts but
    # no GSTIN, and would otherwise be double-counted as a fake invoice.
    if not gstin and (TOTAL_LABEL_RE.match(str(invoice_raw or "").strip())
                      or TOTAL_LABEL_RE.match(str(supplier_raw or "").strip())):
        return None

    # A real invoice line must have an identity (GSTIN or invoice number).
    # This also discards blank spacer rows and amount-only footer rows.
    if not gstin and not invoice_norm:
        return None

    taxable = parse_amount(_cell(row, mapping.get("taxable_value")))
    igst = parse_amount(_cell(row, mapping.get("igst")))
    cgst = parse_amount(_cell(row, mapping.get("cgst")))
    sgst = parse_amount(_cell(row, mapping.get("sgst")))
    cess = parse_amount(_cell(row, mapping.get("cess")))
    total_tax = round(igst + cgst + sgst + cess, 2)

    invoice_value = parse_amount(_cell(row, mapping.get("invoice_value")))
    if invoice_value == 0.0:
        # many purchase registers omit the gross total; derive it
        invoice_value = round(taxable + total_tax, 2)

    date_display, date_obj = parse_date(_cell(row, mapping.get("invoice_date")))

    return {
        "id": f"{source}:{sheet}:{row_no}",
        "source": source,
        "row_no": row_no,
        "sheet": sheet,
        "gstin": str(gstin_raw).strip() if gstin_raw is not None else "",
        "gstin_norm": gstin,
        "supplier_name": str(supplier_raw or "").strip(),
        "invoice_no": str(invoice_raw).strip() if invoice_raw is not None else "",
        "invoice_norm": invoice_norm,
        "invoice_date": date_display,
        "_date_obj": date_obj,
        "taxable_value": round(taxable, 2),
        "igst": round(igst, 2),
        "cgst": round(cgst, 2),
        "sgst": round(sgst, 2),
        "cess": round(cess, 2),
        "total_tax": total_tax,
        "invoice_value": round(invoice_value, 2),
        "rate": parse_amount(_cell(row, mapping.get("rate"))),
    }
