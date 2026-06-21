import type { ReconResult } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8011";

export async function reconcile(
  portalFile: File,
  purchaseFile: File,
  tolerance: number,
): Promise<ReconResult> {
  const form = new FormData();
  form.append("portal_file", portalFile);
  form.append("purchase_file", purchaseFile);
  form.append("tolerance", String(tolerance));

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/reconcile`, { method: "POST", body: form });
  } catch {
    throw new Error(
      `Could not reach the backend at ${API_BASE}. Make sure the Python server is running.`,
    );
  }

  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Fetch a sample file from the backend and turn it into a File for upload.
export async function loadSample(which: "portal" | "purchase"): Promise<File> {
  const res = await fetch(`${API_BASE}/api/sample/${which}`);
  if (!res.ok) {
    throw new Error(
      "Sample files are not available. Run the sample generator in backend/.",
    );
  }
  const blob = await res.blob();
  const name =
    which === "portal" ? "sample_gst_portal.xlsx" : "sample_purchase_register.xlsx";
  return new File([blob], name, { type: blob.type });
}
