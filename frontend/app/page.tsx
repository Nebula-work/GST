"use client";

import { useMemo, useRef, useState } from "react";
import FileDrop from "@/components/FileDrop";
import SummaryCards from "@/components/SummaryCards";
import ResultsTable from "@/components/ResultsTable";
import SiteFooter from "@/components/SiteFooter";
import Turnstile, { TURNSTILE_ENABLED, type TurnstileHandle } from "@/components/Turnstile";
import { reconcile, loadSample } from "@/lib/api";
import type { ReconResult, ReconRow, TabKey, UnmatchedRow } from "@/lib/types";
import { inr } from "@/lib/format";

const TABS: { key: TabKey; label: string }[] = [
  { key: "mismatched", label: "Mismatches" },
  { key: "only_in_purchase", label: "ITC at risk" },
  { key: "only_in_portal", label: "Only in portal" },
  { key: "matched", label: "Reconciled" },
];

function matchesQuery(
  r: { supplier_name: string; gstin: string; invoice_no: string },
  q: string,
) {
  if (!q) return true;
  const hay = `${r.supplier_name} ${r.gstin} ${r.invoice_no}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

function buildCsv(result: ReconResult): string {
  const head = [
    "Category", "Supplier", "GSTIN", "Invoice No", "Date",
    "Portal Taxable", "Books Taxable", "Portal Total Tax", "Books Total Tax",
    "Portal Invoice Value", "Books Invoice Value", "Remarks",
  ];
  const esc = (v: unknown) => {
    let s = String(v ?? "");
    // Neutralise CSV formula injection on text fields (leave numbers alone so
    // negative amounts like -1000 are not mangled).
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [head.join(",")];

  const pair = (cat: string, rows: ReconRow[]) =>
    rows.forEach((r) =>
      lines.push([
        cat, r.supplier_name, r.gstin, r.invoice_no, r.invoice_date ?? "",
        r.portal.taxable_value, r.purchase.taxable_value,
        r.portal.total_tax, r.purchase.total_tax,
        r.portal.invoice_value, r.purchase.invoice_value, r.remarks,
      ].map(esc).join(",")),
    );
  const single = (cat: string, rows: UnmatchedRow[], side: "portal" | "books") =>
    rows.forEach((r) =>
      lines.push([
        cat, r.supplier_name, r.gstin, r.invoice_no, r.invoice_date ?? "",
        side === "portal" ? r.taxable_value : "", side === "books" ? r.taxable_value : "",
        side === "portal" ? r.total_tax : "", side === "books" ? r.total_tax : "",
        side === "portal" ? r.invoice_value : "", side === "books" ? r.invoice_value : "",
        r.remarks,
      ].map(esc).join(",")),
    );

  pair("Mismatch", result.mismatched);
  single("ITC at risk (only in books)", result.only_in_purchase, "books");
  single("Only in portal", result.only_in_portal, "portal");
  pair("Reconciled", result.matched);
  return lines.join("\r\n");
}

export default function Home() {
  const [portalFile, setPortalFile] = useState<File | null>(null);
  const [purchaseFile, setPurchaseFile] = useState<File | null>(null);
  const [tolerance, setTolerance] = useState("1");
  const [loading, setLoading] = useState(false);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReconResult | null>(null);
  const [tab, setTab] = useState<TabKey>("mismatched");
  const [query, setQuery] = useState("");
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const turnstileRef = useRef<TurnstileHandle>(null);

  async function runReconcile() {
    if (!portalFile || !purchaseFile) return;
    if (TURNSTILE_ENABLED && !turnstileToken) {
      setError("Please complete the verification check first.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await reconcile(
        portalFile,
        purchaseFile,
        Number(tolerance) || 0,
        turnstileToken,
      );
      setResult(res);
      // Land on the first non-empty actionable tab.
      const firstNonEmpty =
        TABS.find((t) => countFor(res, t.key) > 0)?.key ?? "matched";
      setTab(firstNonEmpty);
      setQuery("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setResult(null);
    } finally {
      setLoading(false);
      // Turnstile tokens are single-use; get a fresh one for the next run.
      if (TURNSTILE_ENABLED) turnstileRef.current?.reset();
    }
  }

  async function useSamples() {
    setLoadingSamples(true);
    setError(null);
    try {
      const [p, b] = await Promise.all([loadSample("portal"), loadSample("purchase")]);
      setPortalFile(p);
      setPurchaseFile(b);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load samples.");
    } finally {
      setLoadingSamples(false);
    }
  }

  function downloadCsv() {
    if (!result) return;
    // Prepend a UTF-8 BOM so Excel renders ₹ / Indian names correctly.
    const blob = new Blob(["﻿" + buildCsv(result)], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "gst_reconciliation.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  const filtered = useMemo(() => {
    if (!result) return { pairs: [] as ReconRow[], singles: [] as UnmatchedRow[] };
    if (tab === "matched")
      return { pairs: result.matched.filter((r) => matchesQuery(r, query)), singles: [] };
    if (tab === "mismatched")
      return { pairs: result.mismatched.filter((r) => matchesQuery(r, query)), singles: [] };
    if (tab === "only_in_portal")
      return { pairs: [], singles: result.only_in_portal.filter((r) => matchesQuery(r, query)) };
    return { pairs: [], singles: result.only_in_purchase.filter((r) => matchesQuery(r, query)) };
  }, [result, tab, query]);

  const canRun =
    portalFile && purchaseFile && !loading && (!TURNSTILE_ENABLED || !!turnstileToken);

  return (
    <main className="shell">
      <header className="masthead">
        <div className="brand">
          <div className="brand-mark">₹⇄</div>
          <div>
            <div className="eyebrow">Input Tax Credit Reconciliation</div>
            <h1>GST Purchase Matcher</h1>
          </div>
        </div>
        <div className="tag">
          Match your <b>purchase register</b> against the <b>GST portal</b> (GSTR-2A / 2B)
          and surface every invoice, tax and ITC mismatch.
        </div>
      </header>

      {/* ---------- upload ---------- */}
      <section className="upload-wrap">
        <div className="drops">
          <FileDrop
            num={1}
            kicker="From the GST portal"
            title="GSTR-2A / 2B export"
            hint="The vendor invoices auto-drafted on the GST portal."
            file={portalFile}
            onPick={setPortalFile}
          />
          <FileDrop
            num={2}
            kicker="From your books"
            title="Purchase register"
            hint="Your recorded purchases / inward supplies."
            file={purchaseFile}
            onPick={setPurchaseFile}
          />
        </div>

        <div className="controls">
          <label className="tol">
            Amount tolerance ₹
            <input
              type="number"
              min={0}
              step={0.5}
              value={tolerance}
              onChange={(e) => setTolerance(e.target.value)}
              title="Amounts within this many rupees are treated as equal (absorbs rounding)."
            />
          </label>
          <button className="linkbtn" onClick={useSamples} disabled={loadingSamples}>
            {loadingSamples ? "Loading samples…" : "↧ Load sample files"}
          </button>
          <div className="spacer" />
          <button className="btn" onClick={runReconcile} disabled={!canRun}>
            {loading ? <span className="spin" /> : "⇄"}
            {loading ? "Reconciling…" : "Reconcile"}
          </button>
        </div>

        {TURNSTILE_ENABLED && (
          <div className="turnstile-row">
            <Turnstile ref={turnstileRef} onToken={setTurnstileToken} />
          </div>
        )}

        {error && <div className="alert">⚠ {error}</div>}

        {!result && !error && (
          <div className="intro">
            {[
              ["1", "Upload both files", "Portal export + your purchase register, any column names."],
              ["2", "We auto-map columns", "GSTIN, invoice no., taxable value, IGST/CGST/SGST are detected."],
              ["3", "Smart matching", "Exact + fuzzy match on GSTIN & invoice no., tolerant of rounding."],
              ["4", "See every gap", "Mismatches, unclaimed ITC and ITC at risk — exportable to CSV."],
            ].map(([n, h, p]) => (
              <div className="step" key={n}>
                <div className="n">0{n}</div>
                <h4>{h}</h4>
                <p>{p}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ---------- results ---------- */}
      {result && (
        <section className="results">
          <div className="results-head">
            <div>
              <h2 className="section-title">Reconciliation report</h2>
              <div className="filenote">
                <b>{result.meta.portal_file}</b> vs <b>{result.meta.purchase_file}</b> ·
                tolerance {inr(result.meta.tolerance)} · {result.meta.portal.rows} portal /{" "}
                {result.meta.purchase.rows} book rows
              </div>
            </div>
            <button className="linkbtn" onClick={downloadCsv}>
              ↧ Export full report (CSV)
            </button>
          </div>

          {result.meta.warnings?.length > 0 && (
            <div
              className="alert"
              style={{
                marginTop: 0,
                marginBottom: 14,
                background: "var(--warn-bg)",
                borderColor: "var(--warn-line)",
                color: "var(--warn)",
              }}
            >
              ⚠ {result.meta.warnings.join(" ")}
            </div>
          )}

          <SummaryCards s={result.summary} />

          <div className="toolbar">
            <div className="tabs">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  className={`tab t-${t.key}`}
                  data-active={tab === t.key}
                  onClick={() => setTab(t.key)}
                >
                  <span className="dot" />
                  {t.label}
                  <span className="cnt">{countFor(result, t.key)}</span>
                </button>
              ))}
            </div>
            <div className="search">
              <input
                placeholder="Search supplier, GSTIN, invoice…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="tablewrap">
            <ResultsTable
              tab={tab}
              pairs={filtered.pairs}
              singles={filtered.singles}
              searching={query.trim().length > 0}
            />
          </div>
        </section>
      )}

      <SiteFooter />
    </main>
  );
}

function countFor(result: ReconResult, key: TabKey): number {
  if (key === "matched") return result.matched.length;
  if (key === "mismatched") return result.mismatched.length;
  if (key === "only_in_portal") return result.only_in_portal.length;
  return result.only_in_purchase.length;
}
