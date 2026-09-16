import type { ReconResult } from "./types";
import { desktopBridge } from "./desktop";

// Where the FastAPI backend lives, resolved at build time:
//   - NEXT_PUBLIC_API_BASE set   -> use it ("" = same-origin / relative /api)
//   - unset, production build    -> "" (same origin: nginx proxies /api -> backend)
//   - unset, development         -> http://localhost:8011 (separate dev port)
// The desktop app overrides this at runtime: its backend listens on a port
// chosen at launch, which the Electron shell exposes as window.gstDesktop.apiBase.
const RAW_API_BASE = process.env.NEXT_PUBLIC_API_BASE;
const BUILD_API_BASE =
  RAW_API_BASE !== undefined
    ? RAW_API_BASE.replace(/\/$/, "")
    : process.env.NODE_ENV === "production"
      ? ""
      : "http://localhost:8011";

function apiBase(): string {
  const runtime = desktopBridge()?.apiBase;
  return runtime ? runtime.replace(/\/$/, "") : BUILD_API_BASE;
}

export async function reconcile(
  portalFile: File,
  purchaseFile: File,
  tolerance: number,
  turnstileToken?: string | null,
): Promise<ReconResult> {
  const form = new FormData();
  form.append("portal_file", portalFile);
  form.append("purchase_file", purchaseFile);
  form.append("tolerance", String(tolerance));
  // Cloudflare Turnstile token (only present when the widget is enabled).
  if (turnstileToken) form.append("cf-turnstile-response", turnstileToken);

  const base = apiBase();
  let res: Response;
  try {
    res = await fetch(`${base}/api/reconcile`, { method: "POST", body: form });
  } catch {
    throw new Error(
      `Could not reach the backend at ${base || "/api"}. Make sure the server is running.`,
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
  const res = await fetch(`${apiBase()}/api/sample/${which}`);
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
