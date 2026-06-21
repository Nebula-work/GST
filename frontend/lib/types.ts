// Shapes returned by the FastAPI /api/reconcile endpoint.

export interface PublicRecord {
  id: string;
  gstin: string;
  supplier_name: string;
  invoice_no: string;
  invoice_date: string | null;
  taxable_value: number;
  igst: number;
  cgst: number;
  sgst: number;
  cess: number;
  total_tax: number;
  invoice_value: number;
  row_no: number;
  source: string;
}

export interface Diff {
  field: string;
  label: string;
  material: boolean;
  portal: string | number | null;
  purchase: string | number | null;
  delta: number | null;
}

export interface ReconRow {
  gstin: string;
  supplier_name: string;
  invoice_no: string;
  invoice_date: string | null;
  match_type: "exact" | "probable";
  status: "matched" | "mismatch";
  portal: PublicRecord;
  purchase: PublicRecord;
  diffs: Diff[];
  remarks: string;
}

export interface UnmatchedRow extends PublicRecord {
  remarks: string;
  duplicate: boolean;
}

export interface Summary {
  portal_count: number;
  purchase_count: number;
  matched_count: number;
  mismatch_count: number;
  only_in_portal_count: number;
  only_in_purchase_count: number;
  duplicate_count: number;
  portal_taxable_total: number;
  purchase_taxable_total: number;
  portal_tax_total: number;
  purchase_tax_total: number;
  net_tax_difference: number;
  itc_at_risk: number;
  itc_unclaimed: number;
  mismatch_tax_diff: number;
}

export interface ReconMeta {
  portal_file: string;
  purchase_file: string;
  tolerance: number;
  warnings: string[];
  portal: { sheet: string; rows: number; detected_columns: Record<string, string | null> };
  purchase: { sheet: string; rows: number; detected_columns: Record<string, string | null> };
}

export interface ReconResult {
  summary: Summary;
  matched: ReconRow[];
  mismatched: ReconRow[];
  only_in_portal: UnmatchedRow[];
  only_in_purchase: UnmatchedRow[];
  meta: ReconMeta;
}

export type TabKey =
  | "mismatched"
  | "only_in_purchase"
  | "only_in_portal"
  | "matched";
