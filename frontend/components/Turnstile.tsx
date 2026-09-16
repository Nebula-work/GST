"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { TURNSTILE_ENABLED, TURNSTILE_SITE_KEY } from "@/lib/turnstile";

// Web-only. See lib/turnstile.ts for the site key / enabled flag; app/page.tsx
// loads this component lazily so desktop builds never bundle it.

const SCRIPT_ID = "cf-turnstile-script";
const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      reset: (id?: string) => void;
      remove: (id?: string) => void;
    };
  }
}

export interface TurnstileHandle {
  /** Discard the current token and re-challenge (tokens are single-use). */
  reset: () => void;
}

/**
 * Cloudflare Turnstile widget. Calls `onToken` with a fresh token (or null when
 * it expires / errors). Parent passes the token to the reconcile request and
 * calls `reset()` afterwards to get a new one.
 */
const Turnstile = forwardRef<TurnstileHandle, { onToken: (t: string | null) => void }>(
  function Turnstile({ onToken }, ref) {
    const boxRef = useRef<HTMLDivElement>(null);
    const widgetId = useRef<string | null>(null);

    useImperativeHandle(
      ref,
      () => ({
        reset() {
          if (widgetId.current && window.turnstile) {
            try {
              window.turnstile.reset(widgetId.current);
            } catch {
              /* ignore */
            }
          }
          onToken(null);
        },
      }),
      [onToken],
    );

    useEffect(() => {
      if (!TURNSTILE_ENABLED) return;
      let cancelled = false;

      function render() {
        if (cancelled || !boxRef.current || !window.turnstile || widgetId.current) return;
        widgetId.current = window.turnstile.render(boxRef.current, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: (token: string) => onToken(token),
          "expired-callback": () => onToken(null),
          "error-callback": () => onToken(null),
        });
      }

      const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
      if (window.turnstile) {
        render();
      } else if (existing) {
        existing.addEventListener("load", render, { once: true });
      } else {
        const s = document.createElement("script");
        s.id = SCRIPT_ID;
        s.src = SCRIPT_SRC;
        s.async = true;
        s.defer = true;
        s.addEventListener("load", render, { once: true });
        document.head.appendChild(s);
      }
      return () => {
        cancelled = true;
      };
    }, [onToken]);

    if (!TURNSTILE_ENABLED) return null;
    return <div ref={boxRef} className="turnstile" />;
  },
);

export default Turnstile;
