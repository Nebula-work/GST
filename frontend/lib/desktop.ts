// Desktop (Electron) integration.
//
// Two layers, on purpose:
//   * IS_DESKTOP is a BUILD-TIME flag (NEXT_PUBLIC_DESKTOP=1, set by
//     desktop/scripts/build-frontend.mjs). It lets the static export drop
//     web-only pieces such as the Cloudflare bot check and analytics beacon.
//   * desktopBridge() is RUNTIME data the Electron preload script exposes on
//     window.gstDesktop -- most importantly the backend URL, because the local
//     backend listens on a port chosen when the app launches.

export const IS_DESKTOP = process.env.NEXT_PUBLIC_DESKTOP === "1";

export interface GstDesktopBridge {
  /** Base URL of the local backend, e.g. "http://127.0.0.1:53211". */
  apiBase: string;
  /** Desktop app version (from desktop/package.json). */
  version: string;
  /** Node's process.platform of the host: "darwin" | "win32" | "linux". */
  platform: string;
}

declare global {
  interface Window {
    gstDesktop?: GstDesktopBridge;
  }
}

export function desktopBridge(): GstDesktopBridge | null {
  if (typeof window === "undefined") return null;
  return window.gstDesktop ?? null;
}
