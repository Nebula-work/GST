"use client";

import { Fragment, useState } from "react";
import type { ReconRow, UnmatchedRow, TabKey } from "@/lib/types";
import { inr } from "@/lib/format";

const MONEY_FIELDS: [string, keyof ReconRow["portal"]][] = [
  ["Taxable Value", "taxable_value"],
  ["IGST", "igst"],
  ["CGST", "cgst"],
  ["SGST", "sgst"],
  ["Cess", "cess"],
  ["Total Tax", "total_tax"],
  ["Invoice Value", "invoice_value"],
];

function SupplierCell({ name, gstin }: { name: string; gstin: string }) {
  return (
    <div>
      <div className="supplier">{name || <span className="muted">— no name —</span>}</div>
      <div className="gstin">{gstin || "no GSTIN"}</div>
    </div>
  );
}

/** A money cell that shows portal→books when the field changed. */
function AmountCell({
  row,
  field,
}: {
  row: ReconRow;
  field: keyof ReconRow["portal"];
}) {
  const changed = row.diffs.some((d) => d.field === field);
  const book = row.purchase[field] as number;
  if (!changed) return <>{inr(book)}</>;
  const portal = row.portal[field] as number;
  const delta = Math.round((book - portal) * 100) / 100;
  return (
    <span className="diffcell">
      <span className="was">{inr(portal)}</span>
      <span className="now">{inr(book)}</span>
      <span className="delta">
        {delta > 0 ? "+" : ""}
        {inr(delta)}
      </span>
    </span>
  );
}

function DetailPanel({ row }: { row: ReconRow }) {
  const diffFields = new Set(row.diffs.map((d) => d.field));
  const nonMoney = row.diffs.filter((d) =>
    ["gstin", "invoice_no", "invoice_date"].includes(d.field),
  );

  return (
    <div className="detail-grid">
      <div className="dh">Field</div>
      <div className="dh num">GST Portal (2A/2B)</div>
      <div className="dh num">Your Books</div>
      <div className="dh num">Difference</div>

      {nonMoney.map((d) => (
        <Fragment key={d.field}>
          <div className="dc drow-label">{d.label}</div>
          <div className="dc num">{String(d.portal ?? "—")}</div>
          <div className="dc num changed">{String(d.purchase ?? "—")}</div>
          <div className="dc num same">—</div>
        </Fragment>
      ))}

      {MONEY_FIELDS.map(([label, field]) => {
        const p = row.portal[field] as number;
        const b = row.purchase[field] as number;
        const changed = diffFields.has(field);
        const delta = Math.round((b - p) * 100) / 100;
        return (
          <Fragment key={field}>
            <div className="dc drow-label">{label}</div>
            <div className={`dc num ${changed ? "" : "same"}`}>{inr(p)}</div>
            <div className={`dc num ${changed ? "changed" : "same"}`}>{inr(b)}</div>
            <div className={`dc num ${changed ? "changed" : "same"}`}>
              {changed ? `${delta > 0 ? "+" : ""}${inr(delta)}` : "—"}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

// Stable identity for a reconciled pair (survives search/sort/tab switches).
const rowKey = (row: ReconRow) => `${row.portal.id}|${row.purchase.id}`;

function PairTable({ rows, mismatch }: { rows: ReconRow[]; mismatch: boolean }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(k) ? next.delete(k) : next.add(k);
      return next;
    });

  return (
    <table className="ledger">
      <thead>
        <tr>
          <th>Supplier / GSTIN</th>
          <th>Invoice No</th>
          <th>Date</th>
          <th className="num">Taxable</th>
          <th className="num">Total Tax</th>
          <th className="num">Invoice Value</th>
          <th>{mismatch ? "What differs" : "Status"}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const k = rowKey(row);
          const isOpen = open.has(k);
          const detailId = `detail-${k}`;
          return (
            <Fragment key={k}>
              <tr
                className={`row${mismatch ? " clickable" : ""}`}
                onClick={mismatch ? () => toggle(k) : undefined}
                onKeyDown={
                  mismatch
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggle(k);
                        }
                      }
                    : undefined
                }
                role={mismatch ? "button" : undefined}
                tabIndex={mismatch ? 0 : undefined}
                aria-expanded={mismatch ? isOpen : undefined}
                aria-controls={mismatch ? detailId : undefined}
              >
                <td>
                  <SupplierCell name={row.supplier_name} gstin={row.gstin} />
                </td>
                <td>
                  <span className="invno">{row.invoice_no || "—"}</span>
                  {row.match_type === "probable" && (
                    <span className="tag-probable">probable</span>
                  )}
                </td>
                <td className="muted">{row.invoice_date || "—"}</td>
                <td className="num">
                  <AmountCell row={row} field="taxable_value" />
                </td>
                <td className="num">
                  <AmountCell row={row} field="total_tax" />
                </td>
                <td className="num">
                  <AmountCell row={row} field="invoice_value" />
                </td>
                <td>
                  {mismatch ? (
                    <>
                      <span className="remarks">{row.remarks}</span>
                      <div className="expander">
                        {isOpen ? "▾ hide detail" : "▸ compare side-by-side"}
                      </div>
                    </>
                  ) : (
                    <span className="pill ok">
                      <span className="pdot" />
                      {row.match_type === "exact" ? "Exact" : "Matched"}
                    </span>
                  )}
                </td>
              </tr>
              {mismatch && isOpen && (
                <tr className="detail" id={detailId}>
                  <td colSpan={7}>
                    <DetailPanel row={row} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function SingleTable({ rows, risk }: { rows: UnmatchedRow[]; risk: boolean }) {
  return (
    <table className="ledger">
      <thead>
        <tr>
          <th>Supplier / GSTIN</th>
          <th>Invoice No</th>
          <th>Date</th>
          <th className="num">Taxable</th>
          <th className="num">Total Tax</th>
          <th className="num">Invoice Value</th>
          <th>What to do</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr className="row" key={row.id}>
            <td>
              <SupplierCell name={row.supplier_name} gstin={row.gstin} />
            </td>
            <td>
              <span className="invno">{row.invoice_no || "—"}</span>
            </td>
            <td className="muted">{row.invoice_date || "—"}</td>
            <td className="num">{inr(row.taxable_value)}</td>
            <td className="num">{inr(row.total_tax)}</td>
            <td className="num">{inr(row.invoice_value)}</td>
            <td>
              <span
                className={`pill ${row.duplicate ? "warn" : risk ? "risk" : "info"}`}
                style={{ marginBottom: 4 }}
              >
                <span className="pdot" />
                {row.duplicate ? "Duplicate" : risk ? "ITC at risk" : "Unclaimed ITC"}
              </span>
              <div className="remarks">{row.remarks}</div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ResultsTable({
  tab,
  pairs,
  singles,
  searching,
}: {
  tab: TabKey;
  pairs?: ReconRow[];
  singles?: UnmatchedRow[];
  searching?: boolean;
}) {
  const count = (pairs?.length ?? 0) + (singles?.length ?? 0);
  if (count === 0) {
    return (
      <div className="empty">
        {searching ? (
          <>
            <div className="big">No matches</div>
            <div>No invoices in this tab match your search.</div>
          </>
        ) : (
          <>
            <div className="big">Nothing here ✦</div>
            <div>No invoices fall into this category — that&apos;s usually good news.</div>
          </>
        )}
      </div>
    );
  }

  if (tab === "matched") return <PairTable rows={pairs!} mismatch={false} />;
  if (tab === "mismatched") return <PairTable rows={pairs!} mismatch={true} />;
  return <SingleTable rows={singles!} risk={tab === "only_in_purchase"} />;
}
