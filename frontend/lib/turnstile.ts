// Cloudflare Turnstile (bot check) configuration. Kept apart from the widget
// component so pages can read the flag without pulling the widget -- and its
// Cloudflare script URL -- into builds that never show it (the desktop app).
import { IS_DESKTOP } from "./desktop";

// Public site key, baked at build time. When unset (local dev / no key), the
// widget renders nothing and the app does not require a token -- the backend
// only enforces verification when ITS secret is configured. The desktop app
// talks to a local backend with no secret, so it never shows the widget.
export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
export const TURNSTILE_ENABLED = TURNSTILE_SITE_KEY.length > 0 && !IS_DESKTOP;
