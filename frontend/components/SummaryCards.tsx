import type { Summary } from "@/lib/types";
import { inr, num } from "@/lib/format";

export default function SummaryCards({ s }: { s: Summary }) {
  const total = s.portal_count + s.purchase_count;
  return (
    <>
      <div className="cards">
        <div className="card ok" style={{ animationDelay: "0ms" }}>
          <div className="clabel">Reconciled</div>
          <div className="cnum">{s.matched_count}</div>
          <div className="cmeta">invoices match cleanly</div>
        </div>
        <div className="card warn" style={{ animationDelay: "60ms" }}>
          <div className="clabel">Mismatches</div>
          <div className="cnum">{s.mismatch_count}</div>
          <div className="cmeta">tax diff {inr(s.mismatch_tax_diff)}</div>
        </div>
        <div className="card info" style={{ animationDelay: "120ms" }}>
          <div className="clabel">Only in portal</div>
          <div className="cnum">{s.only_in_portal_count}</div>
          <div className="cmeta">ITC unclaimed {inr(s.itc_unclaimed)}</div>
        </div>
        <div className="card risk" style={{ animationDelay: "180ms" }}>
          <div className="clabel">ITC at risk</div>
          <div className="cnum">{s.only_in_purchase_count}</div>
          <div className="cmeta">{inr(s.itc_at_risk)} not uploaded by vendors</div>
        </div>
      </div>

      <div className="ledger-strip" style={{ animationDelay: "220ms" }}>
        <div className="li">
          <div className="ll">Portal — taxable / tax</div>
          <div className="lv">
            {inr(s.portal_taxable_total)} <span className="muted">·</span>{" "}
            {inr(s.portal_tax_total)}
          </div>
        </div>
        <div className="li">
          <div className="ll">Books — taxable / tax</div>
          <div className="lv">
            {inr(s.purchase_taxable_total)} <span className="muted">·</span>{" "}
            {inr(s.purchase_tax_total)}
          </div>
        </div>
        <div className="li">
          <div className="ll">Net tax difference (portal − books)</div>
          <div className={`lv ${s.net_tax_difference === 0 ? "" : "neg"}`}>
            {inr(s.net_tax_difference)}
          </div>
        </div>
        <div className="li">
          <div className="ll">Invoices compared</div>
          <div className="lv">
            {num(s.portal_count).replace(".00", "")} portal /{" "}
            {num(s.purchase_count).replace(".00", "")} books
          </div>
        </div>
      </div>
    </>
  );
}
